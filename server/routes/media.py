"""음원 API — 카탈로그 · 다운로드 · 업로드 · 폴더 가져오기 · 트랙 관리."""
from __future__ import annotations

import os
import platform
import re
import string
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .. import catalog, config, input_limits, media, playlists

router = APIRouter(tags=["media"])

# 시스템 디렉터리는 탐색 대상에서 제외한다 (llm_wiki routes/config.py 의 같은 패턴).
_FORBIDDEN = [
    "c:\\windows", "c:\\program files", "c:\\program files (x86)",
    "c:\\programdata", "/etc", "/bin", "/sbin", "/usr/bin", "/system", "/private",
]


def _forbidden(path: Path) -> bool:
    p = str(path).lower().replace("/", os.sep).replace("\\", os.sep)
    return any(p.startswith(f.replace("\\", os.sep).replace("/", os.sep)) for f in _FORBIDDEN)


def _safe_resolve(raw: str) -> Path:
    try:
        p = Path(raw).expanduser()
        # ★ 심링크 검사는 resolve() **전에** 한다. resolve() 는 링크를 따라가 버려서
        #   검사 시점엔 이미 링크가 가리키는 곳을 보게 된다.
        if p.is_symlink():
            raise HTTPException(status_code=400, detail="바로가기(심볼릭 링크)는 사용할 수 없습니다.")
        p = p.resolve()
    except OSError:
        raise HTTPException(status_code=400, detail="경로를 확인할 수 없습니다.")
    if _forbidden(p):
        raise HTTPException(status_code=403, detail="시스템 폴더는 열 수 없습니다.")
    return p


# ── 카탈로그 / 다운로드 ─────────────────────────────────────────────────────

class DownloadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: Literal["core", "extra", "all"] = "core"
    track_ids: list[str] | None = None


@router.get("/api/media/catalog")
def get_catalog(tier: str | None = Query(None)) -> dict:
    items = catalog.tracks_by_tier(tier)
    return {
        "sources": catalog.sources(),
        "tracks": [{**t, "ready": media.is_ready(t["id"])} for t in items],
        "ready_count": media.ready_count(),
    }


@router.get("/api/media/credits")
def get_credits() -> list[dict]:
    """카탈로그 + 검색으로 추가한 음원의 출처를 함께 낸다.
    응답 형태(리스트)는 그대로라 기존 renderCredits() 가 무수정으로 동작한다."""
    return catalog.credits() + playlists.user_credits()


@router.get("/api/media/status")
def get_status() -> dict:
    return media.job_snapshot()


@router.post("/api/media/download")
def post_download(body: DownloadIn) -> dict:
    if body.track_ids:
        for tid in body.track_ids:
            if not playlists.valid_id(tid):
                raise HTTPException(status_code=400, detail="잘못된 트랙 식별자입니다.")
    return media.start_download(body.track_ids, tier=body.tier)


@router.post("/api/media/download/cancel")
def post_cancel() -> dict:
    return {"cancelled": media.cancel_download()}


# ── 트랙 ────────────────────────────────────────────────────────────────────

class TrackPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title_ko: str | None = Field(None, min_length=1, max_length=200)
    composer_ko: str | None = Field(None, max_length=200)
    performer_ko: str | None = Field(None, max_length=200)
    duration_seconds: int | None = Field(None, ge=1, le=86400)


@router.get("/api/media/tracks")
def get_tracks(ready_only: bool = Query(False)) -> list[dict]:
    return playlists.all_tracks(ready_only=ready_only)


@router.patch("/api/media/tracks/{track_id}")
def patch_track(track_id: str, body: TrackPatch) -> dict:
    if not playlists.valid_id(track_id):
        raise HTTPException(status_code=400, detail="잘못된 트랙 식별자입니다.")
    updated = playlists.patch_track(track_id, body.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail="사용자가 추가한 음원만 수정할 수 있습니다.",
        )
    return updated


@router.delete("/api/media/tracks/{track_id}")
def delete_track(track_id: str) -> dict:
    if not playlists.valid_id(track_id):
        raise HTTPException(status_code=400, detail="잘못된 트랙 식별자입니다.")
    ok, removed_from = playlists.delete_track(track_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="사용자가 추가한 음원만 삭제할 수 있습니다.",
        )
    return {"deleted": True, "removed_from": removed_from}


# ── 업로드 ──────────────────────────────────────────────────────────────────

@router.post("/api/media/upload")
async def upload_track(file: UploadFile = File(...)) -> dict:
    data, ext = await input_limits.read_audio_upload(file)

    # ★ 저장 파일명은 **사용자 입력에서 파생하지 않는다.** 원본 이름은 표시용으로만 쓴다.
    #   이러면 경로 traversal · Windows 금지문자 · MAX_PATH 문제가 한 번에 사라진다.
    track_id = f"u-{uuid.uuid4().hex[:12]}"
    filename = f"{track_id}{ext}"
    dest = config.user_media_dir()
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / filename

    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, target)

    entry = {
        "id": track_id,
        "origin": "upload",
        "title_ko": input_limits.safe_display_name(file.filename),
        "composer_ko": None,
        "performer_ko": None,
        "filename": filename,
        "bytes": len(data),
        "duration_seconds": None,   # 프론트가 loadedmetadata 에서 PATCH 로 채운다
        "license": None,
        "integrity": "none",
    }
    playlists.register_user_track(entry)
    return {**entry, "ready": True, "url": f"/media/user/{filename}"}


# ── 폴더 탐색 / 가져오기 ────────────────────────────────────────────────────

def _windows_drives() -> list[str]:
    if platform.system() != "Windows":
        return []
    return [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]


@router.get("/api/dirs")
def list_dirs(path: str | None = Query(None)) -> dict:
    """폴더 브라우저. ★ 이름·크기만 돌려주고 파일 내용은 절대 읽지 않는다."""
    if not path:
        home = Path.home()
        return {
            "path": str(home),
            "parent": str(home.parent) if home.parent != home else None,
            "drives": _windows_drives(),
            "entries": _dir_entries(home),
            "audio_count": _audio_count(home),
        }
    p = _safe_resolve(path)
    if not p.is_dir():
        raise HTTPException(status_code=400, detail="폴더가 아닙니다.")
    return {
        "path": str(p),
        "parent": str(p.parent) if p.parent != p else None,
        "drives": _windows_drives(),
        "entries": _dir_entries(p),
        "audio_count": _audio_count(p),
    }


def _dir_entries(p: Path) -> list[dict]:
    out = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            try:
                if not child.is_dir() or child.name.startswith(".") or child.is_symlink():
                    continue
                if _forbidden(child):
                    continue
                out.append({"name": child.name, "path": str(child)})
            except OSError:
                continue
    except (OSError, PermissionError):
        return []
    return out[:500]


def _audio_files(p: Path) -> list[Path]:
    out = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            try:
                if child.is_symlink() or not child.is_file():
                    continue
                if child.suffix.lower() in input_limits.ALLOWED_AUDIO_EXTENSIONS:
                    out.append(child)
            except OSError:
                continue
    except (OSError, PermissionError):
        return []
    return out


def _audio_count(p: Path) -> int:
    return len(_audio_files(p))


@router.get("/api/media/scan-folder")
def scan_folder(path: str = Query(...)) -> dict:
    p = _safe_resolve(path)
    if not p.is_dir():
        raise HTTPException(status_code=400, detail="폴더가 아닙니다.")
    files = _audio_files(p)
    return {
        "path": str(p),
        "files": [{"name": f.name, "size": f.stat().st_size} for f in files[:input_limits.MAX_IMPORT_FILES]],
        "total": len(files),
    }


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: str = Field(..., min_length=1, max_length=4096)
    names: list[str] = Field(..., max_length=input_limits.MAX_IMPORT_FILES)


@router.post("/api/media/import-folder")
def import_folder(body: ImportIn) -> dict:
    folder = _safe_resolve(body.folder)
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="폴더가 아닙니다.")

    dest_dir = config.user_media_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)

    imported: list[dict] = []
    skipped = 0
    for raw_name in body.names:
        # ★ 클라이언트가 준 이름을 그대로 붙이지 않는다. basename 만 취하고,
        #   합쳐진 경로가 선언된 폴더 안에 있는지 서버가 다시 확인한다.
        name = os.path.basename(str(raw_name).replace("\\", "/"))
        if not name or name in (".", ".."):
            skipped += 1
            continue
        src = folder / name
        try:
            if src.is_symlink() or not src.is_file():
                skipped += 1
                continue
            if src.resolve().parent != folder:
                skipped += 1
                continue
        except OSError:
            skipped += 1
            continue

        ext = src.suffix.lower()
        if ext not in input_limits.ALLOWED_AUDIO_EXTENSIONS:
            skipped += 1
            continue
        size = src.stat().st_size
        if size == 0 or size > input_limits.MAX_AUDIO_BYTES:
            skipped += 1
            continue
        try:
            input_limits.assert_media_budget(size)
        except HTTPException:
            break       # 예산 초과 — 여기서 멈추고 지금까지 가져온 것만 보고한다

        track_id = f"u-{uuid.uuid4().hex[:12]}"
        filename = f"{track_id}{ext}"
        target = dest_dir / filename
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            with open(src, "rb") as fsrc, open(tmp, "wb") as fdst:
                while True:
                    chunk = fsrc.read(1 << 20)
                    if not chunk:
                        break
                    fdst.write(chunk)
            os.replace(tmp, target)
        except OSError:
            tmp.unlink(missing_ok=True)
            skipped += 1
            continue

        entry = {
            "id": track_id,
            "origin": "import",
            "title_ko": input_limits.safe_display_name(name),
            "composer_ko": None,
            "performer_ko": None,
            "filename": filename,
            "bytes": size,
            "duration_seconds": None,
            "license": None,
            "integrity": "none",
        }
        playlists.register_user_track(entry)
        imported.append(entry)

    return {"imported": len(imported), "skipped": skipped, "tracks": imported}
