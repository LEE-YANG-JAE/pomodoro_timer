"""오늘 할 일 목록.

★ done_pomodoros 를 저장하지 않는다. 세션 로그에서 매번 센다 (CLAUDE.md §4.4).
  저장하면 세션 삭제·기록 초기화·오프라인 큐 재전송에서 조용히 드리프트하고,
  그 드리프트는 사용자가 화면에서 매일 보는 숫자(2/4)가 틀리는 것을 뜻한다.

  done_pomodoros = |{s ∈ sessions : phase=="focus" ∧ completed ∧ task_id == t.id}|

  덕분에 증가 엔드포인트가 필요 없다 — POST /api/stats/sessions 에 task_id 를 담는 것이
  곧 증가다. 쓰기 경로가 하나뿐이라 이중 계산이 생길 자리가 없다.

★ 아카이브를 만들지 않는다. "To Do Today" 만 만든다 — 전체 활동 목록은 태스크 매니저의
  일이고, 타이머 안에 그걸 또 만드는 건 중복이다. 미완료는 자동 이월되고 완료 항목은
  다음 날 목록에서 빠지되 주간 롤업을 위해 파일에는 남는다.
"""
from __future__ import annotations

import datetime as dt
import re
import uuid
from pathlib import Path

from . import config, settings as settings_mod, stats, storage

TASKS_VERSION = 1
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _tasks_path() -> Path:
    return config.DATA_DIR / "tasks.json"       # ★ 호출 시점에 config 를 읽는다


def valid_id(value: str) -> bool:
    return bool(isinstance(value, str) and _ID_RE.match(value))


def _today(at: dt.datetime | None = None) -> str:
    """설정의 day_start_hour 를 반영한 '오늘'. 날짜 규약을 두 개 만들지 않는다."""
    hour = settings_mod.load().records.day_start_hour
    return stats.local_date_of(at or dt.datetime.now().astimezone(), hour)


# ── 저장소 ──────────────────────────────────────────────────────────────────

def _default_doc() -> dict:
    # ★ 기본 작업을 만들지 않는다. 빈 목록이 정상 상태다.
    return {"version": TASKS_VERSION, "active_task_id": None, "tasks": []}


def _read_doc() -> dict:
    doc = storage.read_json(_tasks_path(), default=None)
    if not isinstance(doc, dict) or not isinstance(doc.get("tasks"), list):
        return _default_doc()
    doc.setdefault("version", TASKS_VERSION)
    doc.setdefault("active_task_id", None)
    return doc


def _prune(tasks: list[dict], today: str) -> list[dict]:
    """완료 항목 정리. 순수 함수이고 멱등하다 — 쓸 때마다 돌려도 안전하다."""
    try:
        cutoff = (dt.date.fromisoformat(today)
                  - dt.timedelta(days=config.TASK_RETENTION_DAYS)).isoformat()
    except ValueError:
        return tasks

    kept = [t for t in tasks
            if not t.get("completed") or (t.get("completed_date") or today) >= cutoff]

    if len(kept) > config.TASK_LIST_CAP:
        done = [t for t in kept if t.get("completed")]
        undone = [t for t in kept if not t.get("completed")]
        # 완료한 것부터, 오래된 것부터 버린다
        done.sort(key=lambda t: t.get("completed_date") or "")
        drop = len(kept) - config.TASK_LIST_CAP
        kept = [t for t in kept if t not in done[:drop]]
        if len(kept) > config.TASK_LIST_CAP:
            kept = undone[-config.TASK_LIST_CAP:]
    return kept


def _write_doc(doc: dict, *, today: str | None = None) -> None:
    doc["tasks"] = _prune(doc.get("tasks", []), today or _today())
    if doc.get("active_task_id") and not any(
            t["id"] == doc["active_task_id"] for t in doc["tasks"]):
        doc["active_task_id"] = None        # 정리로 사라진 작업이 활성으로 남지 않게
    storage.atomic_write(_tasks_path(), doc)


def ensure_file() -> None:
    if not _tasks_path().exists():
        storage.atomic_write(_tasks_path(), _default_doc())


# ── 조회 ────────────────────────────────────────────────────────────────────

def _on_todo_list(t: dict, today: str, days: int) -> bool:
    """오늘 목록에 보일 것인가.

    미완료이거나(자동 이월), 최근 `days` 일 안에 완료한 것.
    days=1 이면 오늘 완료한 것만 — 어제 끝낸 일은 다음 날 아침에 사라진다.
    """
    if not t.get("completed"):
        return True
    done_on = t.get("completed_date")
    if not done_on:
        return True
    try:
        end = dt.date.fromisoformat(today)
        start = (end - dt.timedelta(days=max(0, days - 1))).isoformat()
    except ValueError:
        return True
    return start <= done_on <= today


def _public(t: dict, *, life: dict, today_counts: dict, active_id: str | None) -> dict:
    done = life.get(t["id"], 0)
    return {
        **t,
        "done_pomodoros": done,              # ★ 저장값이 아니라 세션 로그에서 센 값
        "done_today": today_counts.get(t["id"], 0),
        "active": t["id"] == active_id,
        "remaining_est": max(0, int(t.get("est_pomodoros", 0)) - done),
    }


def list_tasks(*, today: str, days: int = 1) -> dict:
    doc = _read_doc()
    life = stats.task_pomodoro_counts()
    today_counts = stats.task_pomodoro_counts({today})
    active_id = doc.get("active_task_id")

    rows = [_public(t, life=life, today_counts=today_counts, active_id=active_id)
            for t in doc["tasks"] if _on_todo_list(t, today, days)]

    open_rows = [r for r in rows if not r["completed"]]
    done_rows = [r for r in rows if r["completed"]]
    return {
        "tasks": rows,
        "active_task_id": active_id,
        "totals": {
            "est_total": sum(int(r.get("est_pomodoros", 0)) for r in open_rows),
            "done_total": sum(r["done_pomodoros"] for r in open_rows),
            "remaining_est": sum(r["remaining_est"] for r in open_rows),
            # 예상 대비 실제 비율은 **완료한 작업**으로만 낸다 — 진행 중인 걸 섞으면
            # 항상 1보다 작게 나와 의미가 없다.
            "completed_est_total": sum(int(r.get("est_pomodoros", 0)) for r in done_rows),
            "completed_done_total": sum(r["done_pomodoros"] for r in done_rows),
        },
    }


# ── CRUD ────────────────────────────────────────────────────────────────────

def create_task(*, name: str, est_pomodoros: int = 1, note: str | None = None,
                created_at: dt.datetime) -> dict:
    """맨 뒤에 추가한다.

    ★ 첫 작업이어도 자동 선택하지 않는다 — 선택은 사용자의 명시적 행동이고,
      강제 선택은 "작업 없이 시작" 을 은근히 막는다.
    """
    today = _today(created_at)
    entry = {
        "id": f"t-{uuid.uuid4().hex[:12]}",
        "name": name.strip(),
        "est_pomodoros": int(est_pomodoros),
        "note": note,
        "completed": False,
        "created_at": created_at.isoformat(),
        "created_date": stats.local_date_of(
            created_at, settings_mod.load().records.day_start_hour),
        "completed_at": None,
        "completed_date": None,
    }
    with storage._LOCK:
        doc = _read_doc()
        doc["tasks"].append(entry)
        _write_doc(doc, today=today)
    return entry


def update_task(tid: str, *, name: str | None = None, est_pomodoros: int | None = None,
                note: str | None = None, completed: bool | None = None,
                at: dt.datetime | None = None) -> dict | None:
    at = at or dt.datetime.now().astimezone()
    hour = settings_mod.load().records.day_start_hour
    with storage._LOCK:
        doc = _read_doc()
        for t in doc["tasks"]:
            if t["id"] != tid:
                continue
            if name is not None:
                t["name"] = name.strip()
            if est_pomodoros is not None:
                t["est_pomodoros"] = int(est_pomodoros)
            if note is not None:
                t["note"] = note
            if completed is not None and bool(completed) != bool(t.get("completed")):
                t["completed"] = bool(completed)
                if completed:
                    t["completed_at"] = at.isoformat()
                    t["completed_date"] = stats.local_date_of(at, hour)
                    # 끝낸 일에 뽀모도로가 계속 쌓이면 안 된다
                    if doc.get("active_task_id") == tid:
                        doc["active_task_id"] = None
                else:
                    t["completed_at"] = None
                    t["completed_date"] = None
            _write_doc(doc, today=stats.local_date_of(at, hour))
            return t
    return None


def reorder_tasks(ids: list[str]) -> list[dict]:
    """배열 순서가 곧 표시 순서.

    ★ playlists.update_playlist 와 의도적으로 다르다. 재생목록은 요청에 없는 트랙을
      배정 해제하지만, 작업은 **빠진 id 를 맨 뒤에 살려 둔다.** 다른 탭에서 방금 추가한
      작업을 낡은 클라이언트가 지워 버리면 그건 데이터 손실이다.
    """
    with storage._LOCK:
        doc = _read_doc()
        by_id = {t["id"]: t for t in doc["tasks"]}
        seen: set[str] = set()
        ordered: list[dict] = []
        for tid in ids:
            t = by_id.get(tid)
            if t is not None and tid not in seen:
                seen.add(tid)
                ordered.append(t)
        ordered += [t for t in doc["tasks"] if t["id"] not in seen]   # ★ 살려 둔다
        doc["tasks"] = ordered
        _write_doc(doc)
        return ordered


def delete_task(tid: str) -> bool:
    with storage._LOCK:
        doc = _read_doc()
        before = len(doc["tasks"])
        doc["tasks"] = [t for t in doc["tasks"] if t["id"] != tid]
        if len(doc["tasks"]) == before:
            return False
        if doc.get("active_task_id") == tid:
            doc["active_task_id"] = None
        _write_doc(doc)
        return True


def set_active(tid: str | None) -> tuple[bool, str | None]:
    """tid=None 은 정상 경로다 — 작업 없이 집중하는 것이 기본 상태다."""
    with storage._LOCK:
        doc = _read_doc()
        if tid is not None and not any(t["id"] == tid for t in doc["tasks"]):
            return False, "작업을 찾을 수 없습니다."
        doc["active_task_id"] = tid
        _write_doc(doc)
        return True, None


def clear_completed(*, today: str) -> int:
    with storage._LOCK:
        doc = _read_doc()
        before = len(doc["tasks"])
        doc["tasks"] = [t for t in doc["tasks"] if not t.get("completed")]
        removed = before - len(doc["tasks"])
        if removed:
            _write_doc(doc, today=today)
        return removed
