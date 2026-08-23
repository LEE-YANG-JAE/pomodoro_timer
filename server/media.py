"""음원 다운로드 잡 (백그라운드 스레드) + 진행률 + ready 스캔.

설계 요지:
  - **단일 워커 스레드, 직렬 다운로드.** archive.org 에 예의 바르고, 진행률 로그가 읽히고,
    취소가 단순해진다. urllib.request 로 충분하므로 httpx 의존성이 없다.
  - **실패는 절대 치명적이지 않다.** 트랙별로 감싸고 다음 트랙으로 넘어간다.
    트랙이 0개여도 타이머는 완전히 동작해야 한다.
  - **이어받기.** 중단된 .part 파일이 있으면 Range 요청으로 이어받는다.
  - **_AUTO_OK 플래그** — launcher 가 켜 줄 때만 자동 다운로드가 돈다.
    덕분에 create_app() 은 네트워크를 쓰지 않고 TestClient 가 오프라인으로 즉시 뜬다.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from . import catalog, config, input_limits

_JOB_LOCK = threading.RLock()
_JOB: dict[str, Any] | None = None
_CANCEL = threading.Event()
_WORKER: threading.Thread | None = None

_AUTO_OK = False        # launcher 만 True 로 만든다


class _Cancelled(Exception):
    pass


def enable_auto_download(on: bool) -> None:
    global _AUTO_OK
    _AUTO_OK = bool(on)


def auto_download_allowed() -> bool:
    return _AUTO_OK


# ── ready 스캔 ──────────────────────────────────────────────────────────────

def is_ready(track_id: str, *, resolved: dict | None = None) -> bool:
    """★ `resolved` 를 넘기면 재조회하지 않는다 — 호출측이 이미 트랙 dict 를
    갖고 있는 루프(예: start_download())에서 트랙마다 조회를 두 번 하지 않게."""
    if resolved is not None:
        t = resolved
    else:
        from . import playlists
        t = playlists.resolve_track(track_id)       # 카탈로그 ∪ 사용자 트랙
    if not t:
        return False
    base = (config.catalog_media_dir() if t.get("subdir", "catalog") == "catalog"
            else config.user_media_dir())
    p = base / t["filename"]
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def ready_count() -> int:
    return sum(1 for t in catalog.tracks() if is_ready(t["id"], resolved=t))


def sweep_stale_parts(max_age_days: int = 7) -> int:
    """오래된 .part 를 청소한다. 중단 직후 것은 이어받아야 하므로 남긴다."""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        for p in config.catalog_media_dir().glob("*.part"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed


# ── 다운로드 ────────────────────────────────────────────────────────────────

def _sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify(path: Path, track: dict) -> str:
    """무결성 확인. 'sha1' | 'size' | 'mismatch' | 'none' 을 돌려준다."""
    try:
        size = path.stat().st_size
    except OSError:
        return "mismatch"
    expected = track.get("bytes")
    if expected and size != expected:
        return "mismatch"
    sha1 = track.get("sha1")
    if sha1:
        return "sha1" if _sha1_of(path) == sha1 else "mismatch"
    return "size" if expected else "none"


# 호스트별 최소 요청 간격(초).
# ★ upload.wikimedia.org 는 연속 다운로드에 429 를 낸다 — 실제로 Commons 음원 32곡이
#   전부 이것 때문에 실패했다. 위키미디어는 비영리 인프라이므로 넉넉히 간격을 둔다.
_HOST_MIN_INTERVAL = {
    "upload.wikimedia.org": 3.0,
    "commons.wikimedia.org": 3.0,
}
_DEFAULT_INTERVAL = 0.3
_last_host_call: dict[str, float] = {}


def _throttle(url: str) -> None:
    host = urllib.parse.urlparse(url).netloc.lower()
    interval = _HOST_MIN_INTERVAL.get(host, _DEFAULT_INTERVAL)
    gap = time.monotonic() - _last_host_call.get(host, 0.0)
    if gap < interval:
        # 대기 중에도 취소에 반응해야 한다
        if _CANCEL.wait(interval - gap):
            raise _Cancelled()
    _last_host_call[host] = time.monotonic()


def _resumable_get(url: str, part: Path, expect_bytes: int, on_chunk) -> None:
    have = part.stat().st_size if part.exists() else 0
    if expect_bytes and have >= expect_bytes:
        have = 0                       # .part 가 과대 → 손상된 것, 처음부터

    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    if have:
        req.add_header("Range", f"bytes={have}-")

    _throttle(url)
    with urllib.request.urlopen(req, timeout=config.DOWNLOAD_TIMEOUT) as resp:
        status = getattr(resp, "status", 200)
        if have and status != 206:
            have = 0                   # 서버가 Range 를 무시했다 → 이어받기 포기
        mode = "ab" if have else "wb"
        if have:
            on_chunk(have)
        with open(part, mode) as f:
            while True:
                if _CANCEL.is_set():
                    raise _Cancelled()          # .part 를 남겨 다음에 이어받는다
                chunk = resp.read(config.DOWNLOAD_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                on_chunk(len(chunk))


def _download_one(track: dict, prog: dict) -> None:
    # 검색으로 추가한 트랙은 media/user/ 로 간다 — 카탈로그를 다시 생성해도 안 지워진다
    dest_dir = (config.catalog_media_dir() if track.get("subdir", "catalog") == "catalog"
                else config.user_media_dir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / track["filename"]
    part = final.with_suffix(final.suffix + ".part")

    if final.exists() and _verify(final, track) != "mismatch":
        prog["status"] = "skipped"
        prog["bytes_done"] = prog["bytes_total"]
        return

    prog["status"] = "downloading"
    base = prog["bytes_done"]

    def on_chunk(n: int) -> None:
        prog["bytes_done"] = min(prog["bytes_total"], prog["bytes_done"] + n)
        with _JOB_LOCK:
            if _JOB is not None:
                _JOB["bytes_done"] = sum(
                    t["bytes_done"] for t in _JOB["tracks"].values())

    # 429(요청 과다)는 일시적이다 — 물러났다 다시 시도한다. 다른 오류는 즉시 올린다.
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            _resumable_get(track["url"], part, track.get("bytes", 0), on_chunk)
            last_error = None
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            last_error = e
            wait = 15 * (attempt + 1)
            print(f"[media] {track['id']}: 429 — {wait}초 대기 후 재시도")
            if _CANCEL.wait(wait):
                raise _Cancelled()
            # 이어받기가 가능하도록 진행량을 되돌린다
            prog["bytes_done"] = base + (part.stat().st_size if part.exists() else 0)
    if last_error is not None:
        raise last_error

    os.replace(part, final)

    integrity = _verify(final, track)
    prog["integrity"] = integrity
    if integrity == "mismatch":
        # 크기·해시 불일치는 손상이거나 변조다. 조용히 재생 목록에 넣지 않는다.
        final.unlink(missing_ok=True)
        prog["status"] = "failed"
        prog["error"] = "내려받은 파일이 손상되었습니다 (무결성 검증 실패)."
        prog["bytes_done"] = base
        return
    prog["status"] = "ready"
    prog["bytes_done"] = prog["bytes_total"]


def _run_job(track_ids: list[str]) -> None:
    from . import playlists
    # 요청 순서를 유지한다. url 이 없는 트랙(업로드/폴더 가져오기)은 받을 게 없으므로 건너뛴다.
    tracks = [t for t in (playlists.resolve_track(i) for i in track_ids)
              if t and t.get("url")]
    try:
        for t in tracks:
            if _CANCEL.is_set():
                break
            with _JOB_LOCK:
                if _JOB is None:
                    return
                _JOB["current_track_id"] = t["id"]
                _JOB["current_title_ko"] = t.get("title_ko") or t.get("title_orig") or t["id"]
            prog = _JOB["tracks"][t["id"]]
            try:
                _download_one(t, prog)
            except _Cancelled:
                prog["status"] = "cancelled"
                break
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                # ★ 한 곡이 실패해도 나머지는 계속 받는다. 앱은 음악 없이도 동작한다.
                prog["status"] = "failed"
                prog["error"] = str(e)[:200]
                print(f"[media] 실패 {t['id']}: {e}")
            with _JOB_LOCK:
                if _JOB is not None:
                    _JOB["done"] = sum(
                        1 for p in _JOB["tracks"].values()
                        if p["status"] in ("ready", "skipped"))
                    _JOB["failed"] = sum(
                        1 for p in _JOB["tracks"].values() if p["status"] == "failed")
    finally:
        with _JOB_LOCK:
            if _JOB is not None:
                _JOB["status"] = "cancelled" if _CANCEL.is_set() else "done"
                _JOB["current_track_id"] = None
                _JOB["current_title_ko"] = None
        print("[media] 내려받기 종료")


def start_download(track_ids: list[str] | None = None, *, tier: str = "core") -> dict:
    """다운로드 잡을 시작한다. 이미 도는 중이면 그 잡을 그대로 돌려준다."""
    global _JOB, _WORKER

    with _JOB_LOCK:
        if _JOB is not None and _JOB["status"] == "running":
            return job_snapshot()

        if track_ids:
            from . import playlists as _pl
            wanted = [t for t in (_pl.resolve_track(i) for i in track_ids)
                      if t and t.get("url")]
        else:
            wanted = catalog.tracks_by_tier(tier)
        wanted = [t for t in wanted if not is_ready(t["id"], resolved=t)]

        if not wanted:
            _JOB = None
            return {"active": False, "message_ko": "이미 모든 음원이 준비되어 있습니다."}

        need = sum(int(t.get("bytes", 0)) for t in wanted)
        input_limits.assert_free_space(need)
        input_limits.assert_media_budget(need)

        _CANCEL.clear()
        _JOB = {
            "job_id": uuid.uuid4().hex[:12],
            "status": "running",
            "total": len(wanted),
            "done": 0,
            "failed": 0,
            "bytes_done": 0,
            "bytes_total": need,
            "current_track_id": None,
            "current_title_ko": None,
            "tracks": {
                t["id"]: {
                    "track_id": t["id"],
                    "title_ko": t.get("title_ko") or t.get("title_orig") or t["id"],
                    "status": "pending",
                    "bytes_done": 0,
                    "bytes_total": int(t.get("bytes", 0)),
                    "integrity": "none",
                    "error": None,
                }
                for t in wanted
            },
        }
        ids = [t["id"] for t in wanted]

    _WORKER = threading.Thread(target=_run_job, args=(ids,), daemon=True,
                               name="pomodoro-media-download")
    _WORKER.start()
    print(f"[media] {len(ids)}곡 내려받기 시작 (약 {need / 1e6:.0f}MB)")
    return job_snapshot()


def cancel_download() -> bool:
    with _JOB_LOCK:
        if _JOB is None or _JOB["status"] != "running":
            return False
    _CANCEL.set()
    return True


def job_snapshot() -> dict:
    with _JOB_LOCK:
        job = None if _JOB is None else {
            k: v for k, v in _JOB.items() if k != "tracks"
        }
    active = bool(job and job["status"] == "running")
    ready = ready_count()
    total_catalog = len(catalog.tracks())

    if active:
        message = f"음원 내려받는 중 {job['done']}/{job['total']}"
        if job.get("current_title_ko"):
            message += f" — {job['current_title_ko']}"
    elif job and job["status"] == "cancelled":
        message = "내려받기를 중단했습니다. 받던 파일은 다음에 이어받습니다."
    elif job and job.get("failed"):
        message = f"{job['done']}곡 완료, {job['failed']}곡 실패"
    elif total_catalog == 0:
        message = "음원 목록이 아직 없습니다. 음악 없이도 타이머는 정상 동작합니다."
    else:
        message = ""

    return {
        "active": active,
        "job": job,
        "ready_count": ready,
        "catalog_count": total_catalog,
        "has_any_ready": ready > 0,
        "auto_download_allowed": _AUTO_OK,
        "message_ko": message,
    }


def maybe_start_auto_download() -> None:
    """서버 기동 직후 호출. launcher 가 허용했고 아직 안 받았을 때만 백그라운드로 시작한다."""
    if not _AUTO_OK:
        return
    from . import settings as settings_mod

    s = settings_mod.load()
    if not s.media.auto_download or s.media.auto_download_done:
        return
    if not catalog.tracks():
        print("[media] 카탈로그가 비어 있어 자동 내려받기를 건너뜁니다.")
        return

    sweep_stale_parts()
    s.media.auto_download_done = True
    settings_mod.save(s)
    try:
        start_download(tier=s.media.default_tier)
    except Exception as e:  # noqa: BLE001 — 음원 실패로 서버가 죽으면 안 된다
        print(f"[media] 자동 내려받기를 시작하지 못했습니다: {e}")
