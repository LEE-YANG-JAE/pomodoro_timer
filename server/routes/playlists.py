"""재생목록 API."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import playlists as pl

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_ko: str = Field(..., min_length=1, max_length=60)


class UpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_ko: str | None = Field(None, min_length=1, max_length=60)
    # 배열 순서가 곧 재생 순서다 — 재정렬 전용 엔드포인트를 따로 두지 않는다.
    track_ids: list[str] | None = Field(None, max_length=5000)


class AddTracksIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track_ids: list[str] = Field(..., max_length=5000)


def _check_ids(ids: list[str] | None) -> None:
    for tid in ids or []:
        if not pl.valid_id(tid):
            raise HTTPException(status_code=400, detail="잘못된 트랙 식별자입니다.")


@router.get("")
def list_playlists() -> list[dict]:
    return pl.list_playlists()


@router.post("")
def create_playlist(body: CreateIn) -> dict:
    return pl.create_playlist(body.name_ko)


@router.get("/{pid}")
def get_playlist(pid: str) -> dict:
    if not pl.valid_id(pid):
        raise HTTPException(status_code=400, detail="잘못된 재생목록 식별자입니다.")
    found = pl.get_playlist(pid)
    if found is None:
        raise HTTPException(status_code=404, detail="재생목록을 찾을 수 없습니다.")
    return found


@router.put("/{pid}")
def update_playlist(pid: str, body: UpdateIn) -> dict:
    if not pl.valid_id(pid):
        raise HTTPException(status_code=400, detail="잘못된 재생목록 식별자입니다.")
    _check_ids(body.track_ids)
    updated = pl.update_playlist(pid, name_ko=body.name_ko, track_ids=body.track_ids)
    if updated is None:
        raise HTTPException(status_code=404, detail="재생목록을 찾을 수 없습니다.")
    return updated


@router.delete("/{pid}")
def delete_playlist(pid: str) -> dict:
    if not pl.valid_id(pid):
        raise HTTPException(status_code=400, detail="잘못된 재생목록 식별자입니다.")
    ok, err = pl.delete_playlist(pid)
    if not ok:
        # builtin 삭제는 400 — 설정이 id 로 참조하므로 지우면 댕글링이 된다
        raise HTTPException(status_code=400 if "기본" in (err or "") else 404, detail=err)
    return {"deleted": True}


@router.post("/{pid}/tracks")
def add_tracks(pid: str, body: AddTracksIn) -> dict:
    if not pl.valid_id(pid):
        raise HTTPException(status_code=400, detail="잘못된 재생목록 식별자입니다.")
    _check_ids(body.track_ids)
    updated = pl.add_tracks(pid, body.track_ids)
    if updated is None:
        raise HTTPException(status_code=404, detail="재생목록을 찾을 수 없습니다.")
    return updated


@router.delete("/{pid}/tracks/{track_id}")
def remove_track(pid: str, track_id: str) -> dict:
    if not pl.valid_id(pid) or not pl.valid_id(track_id):
        raise HTTPException(status_code=400, detail="잘못된 식별자입니다.")
    updated = pl.remove_track(pid, track_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="재생목록을 찾을 수 없습니다.")
    return updated
