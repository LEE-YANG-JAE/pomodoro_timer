"""재생목록 + 트랙 레지스트리.

트랙은 두 곳에서 온다:
  - **카탈로그 트랙** — media_catalog.json 이 정본. id 는 생성기가 부여 (예: wtc1-01)
  - **사용자 트랙**   — data/tracks.json 에만 존재. id 는 u-<uuid12>

id 네임스페이스가 겹치지 않으므로 resolve_track() 이 둘을 합쳐 조회할 수 있다.
재생목록(pl-*)은 track_id 배열만 들고 있고, 배열 순서가 곧 재생 순서다.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from . import catalog, config, storage, terms

PLAYLISTS_VERSION = 1
TRACKS_VERSION = 1

BUILTIN = ("focus", "break")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _playlists_path() -> Path:
    return config.DATA_DIR / "playlists.json"


def _tracks_path() -> Path:
    return config.DATA_DIR / "tracks.json"


def valid_id(value: str) -> bool:
    """★ 경로가 아니라 **레지스트리 키**임을 강제하는 게이트.
    이 검사를 통과한 id 만 dict 조회에 쓰고, 실제 파일명은 우리가 생성한 값을 쓴다."""
    return bool(isinstance(value, str) and _ID_RE.match(value))


# ── 저장소 ──────────────────────────────────────────────────────────────────

def _default_playlists() -> dict:
    return {
        "version": PLAYLISTS_VERSION,
        "playlists": [
            {"id": "focus", "name_ko": "집중", "builtin": True, "track_ids": []},
            {"id": "break", "name_ko": "휴식", "builtin": True, "track_ids": []},
        ],
    }


def _read_playlists() -> dict:
    doc = storage.read_json(_playlists_path(), default=None)
    if not isinstance(doc, dict) or not isinstance(doc.get("playlists"), list):
        return _default_playlists()
    # builtin 두 개는 어떤 경우에도 존재해야 한다 (설정이 id 로 참조한다)
    have = {p.get("id") for p in doc["playlists"]}
    for pid, name in (("focus", "집중"), ("break", "휴식")):
        if pid not in have:
            doc["playlists"].append(
                {"id": pid, "name_ko": name, "builtin": True, "track_ids": []}
            )
    return doc


def _write_playlists(doc: dict) -> None:
    storage.atomic_write(_playlists_path(), doc)


def _read_user_tracks() -> list[dict]:
    doc = storage.read_json(_tracks_path(), default=None)
    items = doc.get("tracks") if isinstance(doc, dict) else None
    return items if isinstance(items, list) else []


def _write_user_tracks(items: list[dict]) -> None:
    storage.atomic_write(_tracks_path(), {"version": TRACKS_VERSION, "tracks": items})


def ensure_file() -> None:
    """부트스트랩 시드 + 기본 재생목록 보충.

    카탈로그의 프리셋 배정(트랙의 playlists 필드)으로 집중/휴식을 채운다. 첫 실행에서
    사용자가 아무 설정도 하지 않아도 음악이 나오게 하기 위함이다.

    ★ 파일이 이미 있어도 **비어 있는 기본 재생목록은 채운다.** 카탈로그는 나중에
    갱신될 수 있는데(생성기를 다시 돌리거나 새 버전을 받거나), 그때 목록이 비어 있으면
    음원을 받아도 재생될 곡이 하나도 없다. 사용자가 직접 비운 목록을 되살리지 않도록
    **비어 있을 때만** 보충한다.
    """
    if not _tracks_path().exists():
        _write_user_tracks([])

    with storage._LOCK:
        exists = _playlists_path().exists()
        doc = _read_playlists() if exists else _default_playlists()
        by_id = {p["id"]: p for p in doc["playlists"]}
        changed = not exists

        preset: dict[str, list[str]] = {}
        for t in catalog.tracks():
            for pid in t.get("playlists", []):
                preset.setdefault(pid, []).append(t["id"])

        for pid, ids in preset.items():
            target = by_id.get(pid)
            if target is None or target.get("track_ids"):
                continue          # 사용자가 구성해 둔 목록은 건드리지 않는다
            target["track_ids"] = ids
            changed = True

        if changed:
            _write_playlists(doc)


# ── 트랙 조회 ───────────────────────────────────────────────────────────────

def resolve_track(track_id: str) -> dict | None:
    """카탈로그 ∪ 사용자 트랙 통합 조회."""
    if not valid_id(track_id):
        return None
    t = catalog.track(track_id)
    if t is not None:
        return {**t, "origin": "catalog", "subdir": "catalog"}
    for u in _read_user_tracks():
        if u.get("id") == track_id:
            return {**u, "origin": u.get("origin", "user"), "subdir": "user"}
    return None


def track_file(track_id: str) -> Path | None:
    """트랙의 디스크 경로. **경로는 레지스트리에서만 나온다** — 요청 문자열이
    경로로 쓰이는 일이 없다."""
    t = resolve_track(track_id)
    if t is None:
        return None
    base = config.catalog_media_dir() if t["subdir"] == "catalog" else config.user_media_dir()
    target = base / t["filename"]
    # 벨트+멜빵: 레지스트리가 오염돼도 media/ 밖으로 나가지 못하게
    try:
        if not target.resolve().is_relative_to(config.MEDIA_DIR.resolve()):
            return None
    except OSError:
        return None
    return target


def media_url(track_id: str) -> str | None:
    t = resolve_track(track_id)
    if t is None:
        return None
    return f"/media/{t['subdir']}/{t['filename']}"


def all_tracks(*, ready_only: bool = False) -> list[dict]:
    """카탈로그 + 사용자 트랙 전체. 각 항목에 ready/url 을 붙여 돌려준다."""
    out: list[dict] = []
    for t in catalog.tracks():
        src = catalog.source(t.get("source_id", "")) or {}
        out.append(_public_track(t, "catalog", src))
    for u in _read_user_tracks():
        out.append(_public_track(u, "user", {}))
    if ready_only:
        out = [t for t in out if t["ready"]]
    return out


def _public_track(t: dict, subdir: str, src: dict) -> dict:
    base = config.catalog_media_dir() if subdir == "catalog" else config.user_media_dir()
    path = base / t.get("filename", "")
    ready = path.exists() and path.stat().st_size > 0 if t.get("filename") else False
    return {
        "id": t.get("id"),
        "title_ko": t.get("title_ko") or t.get("title_orig"),
        "title_orig": t.get("title_orig"),
        "composer_ko": t.get("composer_ko"),
        "performer_ko": t.get("performer_ko") or src.get("performer_ko"),
        "duration_seconds": t.get("duration_seconds"),
        "bytes": t.get("bytes"),
        "tier": t.get("tier"),
        "origin": "catalog" if subdir == "catalog" else t.get("origin", "user"),
        "license": t.get("license") or src.get("license"),
        "details_url": src.get("details_url") or (t.get("source_ref") or {}).get("details_url"),
        "integrity": t.get("integrity", "sha1" if t.get("sha1") else "none"),
        "ready": ready,
        "url": f"/media/{subdir}/{t.get('filename')}" if ready else None,
    }


# ── 재생목록 CRUD ───────────────────────────────────────────────────────────

def list_playlists() -> list[dict]:
    ready = {t["id"] for t in all_tracks(ready_only=True)}
    out = []
    for p in _read_playlists()["playlists"]:
        ids = p.get("track_ids", [])
        out.append({
            "id": p["id"],
            "name_ko": p.get("name_ko", p["id"]),
            "builtin": bool(p.get("builtin")),
            "track_ids": ids,
            "count": len(ids),
            "ready_count": sum(1 for i in ids if i in ready),
        })
    return out


def get_playlist(pid: str) -> dict | None:
    for p in _read_playlists()["playlists"]:
        if p["id"] == pid:
            tracks_detail = [
                t for t in (resolve_track(i) for i in p.get("track_ids", [])) if t
            ]
            by_id = {t["id"]: t for t in all_tracks()}
            return {
                "id": p["id"],
                "name_ko": p.get("name_ko", p["id"]),
                "builtin": bool(p.get("builtin")),
                "tracks": [by_id[t["id"]] for t in tracks_detail if t["id"] in by_id],
            }
    return None


def create_playlist(name_ko: str) -> dict:
    with storage._LOCK:
        doc = _read_playlists()
        pid = f"pl-{uuid.uuid4().hex[:8]}"
        doc["playlists"].append(
            {"id": pid, "name_ko": name_ko, "builtin": False, "track_ids": []}
        )
        _write_playlists(doc)
    return {"id": pid, "name_ko": name_ko, "builtin": False, "track_ids": []}


def update_playlist(
    pid: str, *, name_ko: str | None = None, track_ids: list[str] | None = None
) -> dict | None:
    """이름 변경 + 트랙 목록 교체(= 재정렬). 순서가 곧 배열 순서이므로
    재정렬 전용 엔드포인트를 따로 두지 않는다 — 코드 경로가 하나뿐이다."""
    with storage._LOCK:
        doc = _read_playlists()
        for p in doc["playlists"]:
            if p["id"] != pid:
                continue
            if name_ko is not None:
                p["name_ko"] = name_ko
            if track_ids is not None:
                # 존재하는 트랙만, 중복 제거하고 순서 유지
                seen: set[str] = set()
                cleaned = []
                for tid in track_ids:
                    if tid in seen or resolve_track(tid) is None:
                        continue
                    seen.add(tid)
                    cleaned.append(tid)
                p["track_ids"] = cleaned
            _write_playlists(doc)
            return next(x for x in list_playlists() if x["id"] == pid)
    return None


def delete_playlist(pid: str) -> tuple[bool, str | None]:
    if pid in BUILTIN:
        return False, "기본 재생목록(집중/휴식)은 삭제할 수 없습니다. 이름은 바꿀 수 있습니다."
    with storage._LOCK:
        doc = _read_playlists()
        before = len(doc["playlists"])
        doc["playlists"] = [p for p in doc["playlists"] if p["id"] != pid]
        if len(doc["playlists"]) == before:
            return False, "재생목록을 찾을 수 없습니다."
        _write_playlists(doc)
    return True, None


def add_tracks(pid: str, track_ids: list[str]) -> dict | None:
    with storage._LOCK:
        doc = _read_playlists()
        for p in doc["playlists"]:
            if p["id"] != pid:
                continue
            have = set(p.get("track_ids", []))
            for tid in track_ids:
                if tid not in have and resolve_track(tid) is not None:
                    p.setdefault("track_ids", []).append(tid)
                    have.add(tid)
            _write_playlists(doc)
            return next(x for x in list_playlists() if x["id"] == pid)
    return None


def remove_track(pid: str, track_id: str) -> dict | None:
    with storage._LOCK:
        doc = _read_playlists()
        for p in doc["playlists"]:
            if p["id"] != pid:
                continue
            p["track_ids"] = [i for i in p.get("track_ids", []) if i != track_id]
            _write_playlists(doc)
            return next(x for x in list_playlists() if x["id"] == pid)
    return None


# ── 사용자 트랙 ─────────────────────────────────────────────────────────────

def register_user_track(entry: dict) -> dict:
    with storage._LOCK:
        items = _read_user_tracks()
        items.append(entry)
        _write_user_tracks(items)
    return entry


def register_user_tracks(entries: list[dict]) -> list[dict]:
    """여러 건을 락 한 번으로 등록. 검색 추가는 항상 배치다."""
    with storage._LOCK:
        items = _read_user_tracks()
        items.extend(entries)
        _write_user_tracks(items)
    return entries


def has_source_file(identifier: str, name: str) -> bool:
    """같은 항목의 같은 파일을 이미 담았는가 (중복 추가 방지)."""
    for t in _read_user_tracks():
        ref = t.get("source_ref") or {}
        if ref.get("identifier") == identifier and ref.get("name") == name:
            return True
    return False


def user_credits() -> list[dict]:
    """검색으로 추가한 사용자 트랙의 출처를 항목 단위로 묶는다.

    별도 sources 파일을 두지 않는 이유: 트랙을 지울 때 고아 소스를 청소하는 코드가
    또 필요해진다. delete_track() 의 캐스케이드가 이미 완결적이므로 출처를 트랙에
    인라인으로 들고 여기서 group-by 한다.
    """
    groups: dict[str, dict] = {}
    for t in _read_user_tracks():
        ref = t.get("source_ref") or {}
        ident = ref.get("identifier")
        if not ident:
            continue
        g = groups.setdefault(ident, {
            "source_id": f"search:{ident}",
            "album_ko": ref.get("album_orig") or ident,
            "performer_ko": t.get("performer_ko"),
            "license": t.get("license"),
            "license_url": t.get("license") if str(t.get("license") or "").startswith("http") else None,
            "license_kind": t.get("license_kind") or terms.license_kind(t.get("license")),
            "license_badge_ko": terms.LICENSE_BADGE_KO[
                t.get("license_kind") or terms.license_kind(t.get("license"))],
            "details_url": ref.get("details_url"),
            "attribution_ko": t.get("performer_ko") or ref.get("album_orig"),
            "requires_attribution": terms.requires_attribution(t.get("license")),
            "origin": "search",
            "track_count": 0,
        })
        g["track_count"] += 1
    return list(groups.values())


def patch_track(track_id: str, patch: dict) -> dict | None:
    """제목·작곡가·재생시간 수정. 카탈로그 트랙은 정본이 매니페스트이므로
    사용자 트랙만 수정 가능하다."""
    allowed = {"title_ko", "composer_ko", "performer_ko", "duration_seconds"}
    with storage._LOCK:
        items = _read_user_tracks()
        for t in items:
            if t.get("id") != track_id:
                continue
            for k, v in patch.items():
                if k in allowed and v is not None:
                    t[k] = v
            _write_user_tracks(items)
            return t
    return None


def delete_track(track_id: str) -> tuple[bool, list[str]]:
    """사용자 트랙 파일 삭제 + **모든 재생목록에서 캐스케이드 제거**.

    삭제 시점에 캐스케이드하므로 부팅 때 고아 참조를 청소할 필요가 없다.
    """
    removed_from: list[str] = []
    with storage._LOCK:
        items = _read_user_tracks()
        target = next((t for t in items if t.get("id") == track_id), None)
        if target is None:
            return False, []

        path = config.user_media_dir() / target.get("filename", "")
        try:
            if path.resolve().is_relative_to(config.MEDIA_DIR.resolve()):
                path.unlink(missing_ok=True)
        except OSError:
            pass

        _write_user_tracks([t for t in items if t.get("id") != track_id])

        doc = _read_playlists()
        for p in doc["playlists"]:
            ids = p.get("track_ids", [])
            if track_id in ids:
                p["track_ids"] = [i for i in ids if i != track_id]
                removed_from.append(p["id"])
        if removed_from:
            _write_playlists(doc)
    return True, removed_from
