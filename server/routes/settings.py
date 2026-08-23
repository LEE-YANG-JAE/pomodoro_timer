"""설정 API."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from .. import playlists, settings as settings_mod, stats

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ResetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


def _assert_playlists_exist(s: settings_mod.Settings) -> None:
    """설정이 참조하는 재생목록 id 가 실제로 존재하는지 확인.

    모델 안에서 못 하는 이유: 검증에 재생목록 저장소가 필요하다. 여기서 막지 않으면
    존재하지 않는 id 를 가리키는 설정이 저장돼 재생이 조용히 실패한다.
    """
    known = {p["id"] for p in playlists.list_playlists()}
    for field in ("focus_playlist_id", "break_playlist_id", "long_break_playlist_id"):
        pid = getattr(s.audio, field)
        if pid and pid not in known:
            raise HTTPException(
                status_code=422,
                detail=f"재생목록 '{pid}' 을(를) 찾을 수 없습니다.",
            )


@router.get("")
def get_settings() -> dict:
    return settings_mod.load().model_dump(mode="json")


@router.put("")
def put_settings(patch: settings_mod.SettingsPatch) -> dict:
    before = settings_mod.load()
    merged = before.model_copy(deep=True)
    for group in ("timer", "audio", "records", "ui", "media"):
        incoming = getattr(patch, group)
        if incoming is not None:
            setattr(merged, group, incoming)
    _assert_playlists_exist(merged)

    current, changed = settings_mod.apply_patch(patch, before)

    # ★ 하루 시작 시각이 바뀌면 기존 기록의 local_date 를 소급 재계산한다.
    #   동기로 처리해야 재계산 전/후 상태가 공존하지 않는다 (플랜 §4.4 참조).
    recomputed = 0
    if before.records.day_start_hour != current.records.day_start_hour:
        recomputed = stats.recompute_local_dates(current.records.day_start_hour)

    return {
        "settings": current.model_dump(mode="json"),
        "changed": changed,
        "recomputed_sessions": recomputed,
    }


@router.post("/reset")
def reset_settings(body: ResetIn) -> dict:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm: true 가 필요합니다.")
    return settings_mod.save(settings_mod.defaults()).model_dump(mode="json")
