"""기록 API.

★ 타임존 계약: started_at / ended_at 은 **offset-aware** 여야 한다 (AwareDatetime).
  naive 값은 스키마 경계에서 422 로 거부된다. 프론트가 toISOString() 의 Z(UTC)를 보내면
  자정 근처 세션의 날짜가 하루 어긋나므로, ui/modules/utils.js 의 toLocalISO() 가
  로컬 오프셋(+09:00)을 붙여 보낸다.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .. import stats

router = APIRouter(prefix="/api/stats", tags=["stats"])


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # short_break/long_break 은 예전 기록 호환용. 현재는 focus/break 만 쓴다.
    phase: Literal["focus", "break", "short_break", "long_break"] = "focus"
    started_at: AwareDatetime
    ended_at: AwareDatetime
    planned_seconds: int = Field(..., ge=1, le=86400)
    actual_seconds: int = Field(..., ge=0, le=86400)
    completed: bool = True
    cycle_index: int = Field(0, ge=0, le=100)

    # ── 방해 ────────────────────────────────────────────────────────────────
    # 자동 감지된 중단(일시정지·절전). 사용자가 분류한 값이 아니다.
    # ★ 이 필드를 지우면 안 된다. extra="forbid" 이고 stats.js 의 오프라인 큐는
    #   4xx 를 영구 실패로 보고 폐기하므로, 업그레이드 시점에 큐에 남아 있던
    #   옛 페이로드가 전부 조용히 버려진다 — 25분씩 버틴 세션들이.
    interruptions: int = Field(0, ge=0, le=1000)
    # ★ 프론트는 더 이상 이 두 필드를 보내지 않는다(예전 수동 내부/외부 방해 로깅
    #   기능 제거됨). 그래도 필드는 남겨 둔다 — 업그레이드 시점에 오프라인 큐에
    #   남아 있던 옛 페이로드가 이 필드를 포함해 재전송될 수 있는데, extra="forbid"
    #   에서 지웠다면 그 요청 전체가 422 로 거부되어 세션 자체가 사라진다.
    #   받되 저장/집계하지 않는다(아래 append_session 참고).
    interruptions_internal: int = Field(0, ge=0, le=1000)
    interruptions_external: int = Field(0, ge=0, le=1000)

    # ── 작업 ────────────────────────────────────────────────────────────────
    # task_name 을 비정규화하는 이유는 하나다 — 작업을 지워도 기록이 살아남게 하려고.
    task_id: str | None = Field(None, min_length=1, max_length=64)
    task_name: str | None = Field(None, min_length=1, max_length=120)

    # 클라이언트 생성 멱등키. 오프라인 큐가 재전송해도 기록이 중복되지 않는다.
    client_id: str | None = Field(None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _check_order(self):
        if self.ended_at < self.started_at:
            raise ValueError("ended_at 은 started_at 보다 빠를 수 없습니다.")
        return self


class ResetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


@router.post("/sessions")
def post_session(body: SessionIn) -> dict:
    return stats.append_session(body.model_dump())


@router.get("/summary")
def get_summary(
    days: int = Query(14, ge=1, le=365),
    today: str | None = Query(None, description="클라이언트 로컬 날짜 YYYY-MM-DD"),
) -> dict:
    return stats.summary(days, stats.resolve_today(today))


@router.get("/today")
def get_today(today: str | None = Query(None)) -> dict:
    return stats.today_summary(stats.resolve_today(today))


@router.get("/series")
def get_series(
    days: int = Query(7, ge=1, le=365), today: str | None = Query(None)
) -> list[dict]:
    return stats.series(days, stats.resolve_today(today))


@router.get("/streak")
def get_streak(today: str | None = Query(None)) -> dict:
    return stats.streak(stats.resolve_today(today))


@router.get("/sessions")
def get_sessions(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    date: str | None = Query(None),
) -> dict:
    return stats.list_sessions(limit=limit, offset=offset, date=date)


@router.get("/export.csv")
def export_csv(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
) -> StreamingResponse:
    """append-only 로그 전체를 CSV 로. 스트리밍이라 순수 <a download> 로 받을 수 있다."""
    return StreamingResponse(
        stats.iter_csv(date_from=date_from, date_to=date_to),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="pomodoro-sessions.csv"'},
    )


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    return {"deleted": stats.delete_session(session_id)}


@router.post("/reset")
def reset_stats(body: ResetIn) -> dict:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm: true 가 필요합니다.")
    return {"deleted": stats.reset_all()}
