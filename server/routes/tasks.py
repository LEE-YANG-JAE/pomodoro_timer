"""오늘 할 일 API.

★ 리터럴 경로(/order, /active, /clear-completed)를 /{tid} 보다 **먼저** 선언한다.
  지금은 메서드가 겹치지 않지만, 습관으로 두면 나중에 겹칠 때 조용한 오라우팅을 막는다.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .. import stats, tasks

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=120)
    est_pomodoros: int = Field(1, ge=1, le=40)
    note: str | None = Field(None, max_length=500)
    # ★ naive 는 422 — 세션과 같은 타임존 계약을 쓴다 (CLAUDE.md §3.5)
    created_at: AwareDatetime | None = None


class PatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=120)
    est_pomodoros: int | None = Field(None, ge=1, le=40)
    note: str | None = Field(None, max_length=500)
    completed: bool | None = None
    at: AwareDatetime | None = None


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(..., max_length=500)


class ActiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str | None = Field(None, min_length=1, max_length=64)


class ClearIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False
    today: str | None = None


def _gate(tid: str) -> None:
    if not tasks.valid_id(tid):
        raise HTTPException(status_code=400, detail="잘못된 작업 식별자입니다.")


@router.get("")
def list_tasks(
    today: str | None = Query(None, description="클라이언트 로컬 날짜 YYYY-MM-DD"),
    days: int = Query(1, ge=1, le=365),
) -> dict:
    return tasks.list_tasks(today=stats.resolve_today(today), days=days)


@router.post("")
def create_task(body: CreateIn) -> dict:
    return tasks.create_task(
        name=body.name,
        est_pomodoros=body.est_pomodoros,
        note=body.note,
        created_at=body.created_at or dt.datetime.now().astimezone(),
    )


@router.put("/order")
def reorder(body: OrderIn) -> dict:
    for tid in body.ids:
        _gate(tid)
    return {"tasks": tasks.reorder_tasks(body.ids)}


@router.put("/active")
def set_active(body: ActiveIn) -> dict:
    if body.task_id is not None:
        _gate(body.task_id)
    ok, err = tasks.set_active(body.task_id)
    if not ok:
        raise HTTPException(status_code=404, detail=err)
    return {"active_task_id": body.task_id}


@router.post("/clear-completed")
def clear_completed(body: ClearIn) -> dict:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm: true 가 필요합니다.")
    return {"removed": tasks.clear_completed(today=stats.resolve_today(body.today))}


@router.patch("/{tid}")
def patch_task(tid: str, body: PatchIn) -> dict:
    _gate(tid)
    updated = tasks.update_task(
        tid,
        name=body.name,
        est_pomodoros=body.est_pomodoros,
        note=body.note,
        completed=body.completed,
        at=body.at,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return updated


@router.delete("/{tid}")
def delete_task(tid: str) -> dict:
    _gate(tid)
    if not tasks.delete_task(tid):
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return {"deleted": True}
