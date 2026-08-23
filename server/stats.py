"""뽀모도로 세션 기록 + 집계.

★ 롤업을 저장하지 않고 append-only 원시 로그에서 매번 계산한다.
   롤업은 드리프트할 수 있는 캐시이고, 여기서의 드리프트는 곧 사용자가 가장 신경 쓰는
   숫자(연속 달성 일수)가 조용히 틀리는 것이다. 5만 건 상한에서 전체 스캔은 10~30ms 라
   캐시로 얻을 게 없다. mtime 키 메모이제이션만 얹는다.
"""
from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

from . import config, settings as settings_mod, storage

_KEY = "sessions"

# mtime 키 메모 — 같은 파일 상태면 재파싱하지 않는다.
_cache: list[dict] | None = None
_cache_mtime: float | None = None


def _path() -> Path:
    return config.DATA_DIR / "sessions.json"


def _load() -> list[dict]:
    global _cache, _cache_mtime
    p = _path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        _cache, _cache_mtime = [], None
        return []
    if _cache is not None and _cache_mtime == mtime:
        return _cache
    doc = storage.read_json(p, default=None)
    items = doc.get(_KEY) if isinstance(doc, dict) else None
    _cache = items if isinstance(items, list) else []
    _cache_mtime = mtime
    return _cache


def _write(items: list[dict]) -> None:
    global _cache, _cache_mtime
    storage.atomic_write(_path(), {"version": 1, _KEY: items})
    _cache = None          # 다음 _load() 가 다시 읽도록 무효화
    _cache_mtime = None


def invalidate_cache() -> None:
    """설정 변경 등으로 외부에서 파일이 바뀌었을 때 호출."""
    global _cache, _cache_mtime
    _cache = None
    _cache_mtime = None


# ── 하루 경계 ───────────────────────────────────────────────────────────────

def local_date_of(ended_at: dt.datetime, day_start_hour: int) -> str:
    """offset-aware 종료 시각 → 사용자 기준 하루 날짜 문자열.

    day_start_hour=4 면 03:59 는 전날, 04:00 은 당일로 잡힌다.

    aware datetime 의 .date() 는 그 datetime 자신의 오프셋 기준이므로, 클라이언트가
    로컬 오프셋(+09:00)을 붙여 보내면 그대로 사용자의 달력 날짜가 된다.
    프론트가 toISOString() 의 Z(UTC)를 보내면 자정 근처 세션이 하루 어긋난다 —
    ui/modules/utils.js 의 toLocalISO() 가 이 계약을 지킨다.
    """
    return (ended_at - dt.timedelta(hours=day_start_hour)).date().isoformat()


def _server_today(day_start_hour: int = 0) -> str:
    return local_date_of(dt.datetime.now().astimezone(), day_start_hour)


def resolve_today(today: str | None) -> str:
    """클라이언트가 보낸 로컬 날짜를 쓰되, 없으면 서버 로컬 날짜로 열화한다.
    (curl / TestClient 가 파라미터 없이도 동작하도록.)"""
    if today:
        try:
            dt.date.fromisoformat(today)
            return today
        except ValueError:
            pass
    return _server_today(settings_mod.load().records.day_start_hour)


# ── 쓰기 ────────────────────────────────────────────────────────────────────

def append_session(rec: dict) -> dict:
    """세션 1건 기록. client_id 가 같은 기존 레코드가 있으면 덮어쓴다(upsert).

    프론트의 오프라인 큐가 같은 세션을 재전송할 수 있으므로 멱등해야 한다.
    """
    day_start_hour = settings_mod.load().records.day_start_hour
    ended_at: dt.datetime = rec["ended_at"]
    started_at: dt.datetime = rec["started_at"]

    entry = {
        "id": rec.get("id") or uuid.uuid4().hex[:12],
        "client_id": rec.get("client_id"),
        "phase": rec.get("phase", "focus"),
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "local_date": local_date_of(ended_at, day_start_hour),
        "planned_seconds": int(rec["planned_seconds"]),
        "actual_seconds": int(rec["actual_seconds"]),
        "completed": bool(rec.get("completed", True)),
        "cycle_index": int(rec.get("cycle_index", 0)),
        "interruptions": int(rec.get("interruptions", 0)),
        "interruptions_internal": int(rec.get("interruptions_internal", 0)),
        "interruptions_external": int(rec.get("interruptions_external", 0)),
        "task_id": rec.get("task_id"),
        "task_name": rec.get("task_name"),
    }

    with storage._LOCK:                       # 읽기-수정-쓰기를 원자적으로
        items = list(_load())
        duplicate = False
        cid = entry["client_id"]
        if cid:
            for i, existing in enumerate(items):
                if existing.get("client_id") == cid:
                    entry["id"] = existing.get("id", entry["id"])
                    items[i] = entry
                    duplicate = True
                    break
        if not duplicate:
            items.append(entry)
            if len(items) > config.SESSION_LOG_CAP:
                items = items[-config.SESSION_LOG_CAP:]
        _write(items)

    return {"id": entry["id"], "local_date": entry["local_date"], "duplicate": duplicate}


def delete_session(sid: str) -> bool:
    with storage._LOCK:
        items = list(_load())
        kept = [s for s in items if s.get("id") != sid]
        if len(kept) == len(items):
            return False
        _write(kept)
        return True


def reset_all() -> int:
    with storage._LOCK:
        n = len(_load())
        _write([])
        return n


def recompute_local_dates(day_start_hour: int) -> int:
    """day_start_hour 가 바뀌었을 때 전체 로그의 local_date 를 소급 재계산한다.

    5만 건에서 ~50ms. 동기로 하는 게 맞다 — 백그라운드 잡으로 빼면 재계산 전/후 상태가
    공존해 통계가 일시적으로 틀리게 되고, 그게 바로 롤업을 쓰지 않기로 한 이유다.
    """
    with storage._LOCK:
        items = list(_load())
        changed = 0
        for s in items:
            try:
                ended = dt.datetime.fromisoformat(s["ended_at"])
            except (KeyError, ValueError):
                continue
            new_date = local_date_of(ended, day_start_hour)
            if s.get("local_date") != new_date:
                s["local_date"] = new_date
                changed += 1
        if changed:
            _write(items)
        return changed


# ── 집계 ────────────────────────────────────────────────────────────────────
# 규칙 (API.md 에도 명시):
#   - 뽀모도로 개수 = phase == "focus" AND completed
#   - 총 집중 시간  = 위 세션들의 actual_seconds 합
#   - 중도 포기     = 개수/시간에 포함하지 않고 aborted_* 로 따로 보고

def interruption_counts(s: dict) -> tuple[int, int, int]:
    """(내부, 외부, 미분류).

    ★ 새 키가 아예 없는 옛 레코드는 전부 **미분류**다. 옛 `interruptions` 는 사용자가
      분류한 값이 아니라 자동 감지된 일시정지 횟수였으므로 내부로 귀속하면
      "이번 주 내부 21회" 가 지어낸 숫자가 된다. 없는 정보는 없다고 말한다.
    """
    return (
        int(s.get("interruptions_internal", 0) or 0),
        int(s.get("interruptions_external", 0) or 0),
        int(s.get("interruptions", 0) or 0),
    )


def _is_pomodoro(s: dict) -> bool:
    return s.get("phase") == "focus" and bool(s.get("completed"))


def _is_aborted_focus(s: dict) -> bool:
    return s.get("phase") == "focus" and not s.get("completed")


def today_summary(today: str) -> dict:
    rows = [s for s in _load() if s.get("local_date") == today]
    done = [s for s in rows if _is_pomodoro(s)]
    aborted = [s for s in rows if _is_aborted_focus(s)]
    return {
        "date": today,
        "pomodoro_count": len(done),
        "focus_seconds": sum(int(s.get("actual_seconds", 0)) for s in done),
        "aborted_count": len(aborted),
        "aborted_seconds": sum(int(s.get("actual_seconds", 0)) for s in aborted),
        # interruptions 는 이제 **총합**이다 (내부 + 외부 + 미분류).
        # 옛 값은 그 부분집합이므로 기존 표시가 깨지지 않는다.
        "interruptions": sum(sum(interruption_counts(s)) for s in rows),
        "interruptions_internal": sum(interruption_counts(s)[0] for s in rows),
        "interruptions_external": sum(interruption_counts(s)[1] for s in rows),
        "interruptions_unclassified": sum(interruption_counts(s)[2] for s in rows),
    }


def series(days: int, today: str) -> list[dict]:
    """최근 days 일. 기록이 없는 날도 0 으로 채워 돌려준다 —
    프론트 막대그래프가 날짜 산술을 하지 않아도 되게."""
    end = dt.date.fromisoformat(today)
    buckets: dict[str, dict] = {}
    for i in range(days - 1, -1, -1):
        key = (end - dt.timedelta(days=i)).isoformat()
        buckets[key] = {"date": key, "pomodoro_count": 0, "focus_seconds": 0}
    for s in _load():
        b = buckets.get(s.get("local_date", ""))
        if b is None or not _is_pomodoro(s):
            continue
        b["pomodoro_count"] += 1
        b["focus_seconds"] += int(s.get("actual_seconds", 0))
    return list(buckets.values())


def _active_dates() -> set[str]:
    return {s["local_date"] for s in _load() if _is_pomodoro(s) and s.get("local_date")}


def streak(today: str) -> dict:
    """연속 달성 일수.

    llm_wiki 의 cards.py:_streak 는 오늘 기록이 없으면 즉시 0 을 돌려준다. 오전 9시에
    12일 연속이 0 으로 보이는 건 틀렸고 사기를 꺾는다. → 오늘이 조건을 만족하면 오늘부터,
    아니면 어제부터 역행하고 includes_today 를 함께 돌려줘 UI 가
    "연속 12일 · 오늘 아직" 과 "연속 12일" 을 구분하게 한다.
    """
    active = _active_dates()
    if not active:
        return {"current": 0, "best": 0, "includes_today": False, "last_active_date": None}

    end = dt.date.fromisoformat(today)
    includes_today = today in active
    cursor = end if includes_today else end - dt.timedelta(days=1)

    current = 0
    while cursor.isoformat() in active:
        current += 1
        cursor -= dt.timedelta(days=1)

    # 최고 기록 — 정렬된 날짜 집합을 한 번 훑는다
    ordered = sorted(dt.date.fromisoformat(d) for d in active)
    best = run = 1
    for prev, cur in zip(ordered, ordered[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        best = max(best, run)

    return {
        "current": current,
        "best": best,
        "includes_today": includes_today,
        "last_active_date": ordered[-1].isoformat(),
    }


def summary(days: int, today: str) -> dict:
    """프론트가 부팅 시 한 번에 가져가는 묶음."""
    return {
        "today": today_summary(today),
        "series": series(days, today),
        "streak": streak(today),
        "daily_goal": settings_mod.load().records.daily_goal,
    }


def _window_dates(today: str, days: int) -> set[str]:
    end = dt.date.fromisoformat(today)
    return {(end - dt.timedelta(days=i)).isoformat() for i in range(max(1, days))}


def task_pomodoro_counts(dates: set[str] | None = None) -> dict[str | None, int]:
    """작업 id → 완료 뽀모도로 수. dates=None 이면 전 기간.

    ★ tasks.py 가 done_pomodoros / done_today 를 만들 때 쓴다.
      개수를 저장하지 않고 여기서 세는 이유는 CLAUDE.md §4.4 — 롤업은 드리프트한다.
      세션을 지우거나 기록을 초기화하면 이 값이 자동으로 따라온다.
    """
    out: dict[str | None, int] = {}
    for s in _load():
        if not _is_pomodoro(s):
            continue
        if dates is not None and s.get("local_date") not in dates:
            continue
        key = s.get("task_id")
        out[key] = out.get(key, 0) + 1
    return out


def task_rollup(today: str, days: int = 1, limit: int = 12) -> list[dict]:
    """작업별 롤업. 뽀모도로 개수 내림차순.

    ★ task_id 가 없는 세션도 하나의 버킷(task_id=None)으로 반드시 보여준다 —
      작업을 고르지 않는 것은 합법이고, 그 시간을 숨기면 합계가 맞지 않는다.
    이름은 그 기간에서 **가장 최근** task_name 을 쓴다 (이름은 수정될 수 있다).
    """
    dates = _window_dates(today, days)
    buckets: dict[str | None, dict] = {}
    for s in _load():
        if s.get("local_date") not in dates or s.get("phase") != "focus":
            continue
        key = s.get("task_id")
        b = buckets.setdefault(key, {
            "task_id": key, "task_name": None, "pomodoro_count": 0,
            "focus_seconds": 0, "interruptions_internal": 0, "interruptions_external": 0,
        })
        if s.get("task_name"):
            b["task_name"] = s["task_name"]        # 뒤에 나오는 것이 더 최근이다
        if _is_pomodoro(s):
            b["pomodoro_count"] += 1
            b["focus_seconds"] += int(s.get("actual_seconds", 0))
        i, e, _ = interruption_counts(s)
        b["interruptions_internal"] += i
        b["interruptions_external"] += e
    rows = sorted(buckets.values(), key=lambda b: -b["pomodoro_count"])
    return rows[:limit]


def interruption_summary(today: str, days: int = 7) -> dict:
    """주간 방해 요약."""
    dates = _window_dates(today, days)
    internal = external = unclassified = 0
    sessions = 0
    for s in _load():
        if s.get("local_date") not in dates or s.get("phase") != "focus":
            continue
        sessions += 1
        i, e, u = interruption_counts(s)
        internal += i
        external += e
        unclassified += u
    total = internal + external + unclassified
    return {
        "days": days, "internal": internal, "external": external,
        "unclassified": unclassified, "sessions": sessions,
        "per_session": round(total / sessions, 2) if sessions else 0.0,
    }


_CSV_COLUMNS = [
    "id", "client_id", "local_date", "started_at", "ended_at", "phase",
    "planned_seconds", "actual_seconds", "completed", "cycle_index",
    "task_id", "task_name",
    "interruptions_internal", "interruptions_external", "interruptions_unclassified",
]


def iter_csv(*, date_from: str | None = None, date_to: str | None = None):
    """append-only 로그를 스트리밍 CSV 로. 제너레이터.

    ★ 맨 앞에 UTF-8 BOM 을 흘린다 — 없으면 엑셀(Windows)이 한글 작업명을 깨뜨린다.
    ★ csv.writer 를 쓴다 — 작업명에 쉼표·따옴표·줄바꿈이 들어갈 수 있다.
    ★ 행을 돌기 전에 스냅샷을 뜬다. StreamingResponse 는 이 동기 제너레이터를
      스레드풀에서 돌리므로, 행마다 락을 잡으면 느린 다운로드가 쓰기를 막는다.
    """
    import csv
    import io

    rows = list(_load())
    if date_from:
        rows = [r for r in rows if (r.get("local_date") or "") >= date_from]
    if date_to:
        rows = [r for r in rows if (r.get("local_date") or "") <= date_to]

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")

    def flush() -> str:
        out = buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        return out

    # ★ UTF-8 BOM — 없으면 엑셀(Windows)이 한글 작업명을 깨뜨린다.
    yield "\ufeff"
    writer.writerow(_CSV_COLUMNS)
    yield flush()

    for r in rows:
        i, e, u = interruption_counts(r)
        writer.writerow([
            r.get("id", ""), r.get("client_id") or "", r.get("local_date", ""),
            r.get("started_at", ""), r.get("ended_at", ""), r.get("phase", ""),
            r.get("planned_seconds", 0), r.get("actual_seconds", 0),
            "true" if r.get("completed") else "false", r.get("cycle_index", 0),
            r.get("task_id") or "", r.get("task_name") or "",
            i, e, u,
        ])
        yield flush()


def list_sessions(*, limit: int, offset: int, date: str | None = None) -> dict[str, Any]:
    rows = _load()
    if date:
        rows = [s for s in rows if s.get("local_date") == date]
    total = len(rows)
    return {"items": rows[offset: offset + limit], "total": total}
