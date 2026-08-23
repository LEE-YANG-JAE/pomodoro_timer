"""할 일 목록 API 단위 테스트.

실행:  .venv\\Scripts\\python.exe qa\\test_tasks.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from server import config  # noqa: E402

config.DATA_DIR = pathlib.Path(tempfile.mkdtemp(prefix="pomo-tasks-"))
config.MEDIA_DIR = config.DATA_DIR / "media"
config.ensure_dirs()

from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402

c = TestClient(create_app())
checks = 0

TODAY = "2026-08-23"
TOMORROW = "2026-08-24"


def check(label: str, cond: bool) -> None:
    global checks
    checks += 1
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    print(f"  ok  {label}")


def mk(name, est=1, **kw):
    return c.post("/api/tasks", json={"name": name, "est_pomodoros": est, **kw})


def rec(task_id=None, *, completed=True, cid=None, start="2026-08-23T09:00:00+09:00",
        end="2026-08-23T09:25:00+09:00", **kw):
    body = {"phase": "focus", "started_at": start, "ended_at": end,
            "planned_seconds": 1500, "actual_seconds": 1500, "completed": completed}
    if task_id:
        body["task_id"] = task_id
        body["task_name"] = kw.get("task_name", "이름")
    if cid:
        body["client_id"] = cid
    body.update({k: v for k, v in kw.items() if k not in ("task_name",)})
    return c.post("/api/stats/sessions", json=body)


print("== 빈 상태 ==")
r = c.get("/api/tasks", params={"today": TODAY})
check("파일이 없어도 200", r.status_code == 200)
check("빈 목록", r.json()["tasks"] == [])
# ★ 기본 작업을 만들지 않는다 — 빈 목록이 정상 상태다
check("active_task_id 는 null", r.json()["active_task_id"] is None)

print("== 생성 ==")
r = mk("논문 3장 초고", 4)
check("생성 200", r.status_code == 200)
t1 = r.json()["id"]
check("id 가 t- 로 시작", t1.startswith("t-"))
# ★ 첫 작업이어도 자동 선택하지 않는다 — 강제 선택은 '작업 없이 시작'을 막는다
check("첫 작업을 만들어도 active 는 여전히 null",
      c.get("/api/tasks", params={"today": TODAY}).json()["active_task_id"] is None)
t2 = mk("코드 리뷰", 2).json()["id"]
rows = c.get("/api/tasks", params={"today": TODAY}).json()["tasks"]
check("맨 뒤에 붙는다", [x["id"] for x in rows] == [t1, t2])

print("== 검증 경계 ==")
check("est 0 -> 422", mk("x", 0).status_code == 422)
check("est 41 -> 422", mk("x", 41).status_code == 422)
check("빈 이름 -> 422", mk("", 1).status_code == 422)
check("121자 이름 -> 422", mk("가" * 121, 1).status_code == 422)
check("알 수 없는 키 -> 422 (extra=forbid)",
      c.post("/api/tasks", json={"name": "x", "bogus": 1}).status_code == 422)
check("naive created_at -> 422 (타임존 계약)",
      c.post("/api/tasks", json={"name": "x", "created_at": "2026-08-23T09:00:00"}).status_code == 422)
check("잘못된 id 형식 -> 400",
      c.patch("/api/tasks/..%2Fetc", json={"name": "x"}).status_code in (400, 404))

print("== 선택 / 해제 ==")
check("선택", c.put("/api/tasks/active", json={"task_id": t1}).status_code == 200)
check("반영됨",
      c.get("/api/tasks", params={"today": TODAY}).json()["active_task_id"] == t1)
# ★ null 은 오류가 아니라 정상 경로다
check("해제(null)가 정상 경로",
      c.put("/api/tasks/active", json={"task_id": None}).status_code == 200)
check("해제 반영",
      c.get("/api/tasks", params={"today": TODAY}).json()["active_task_id"] is None)
check("없는 id 선택 -> 404",
      c.put("/api/tasks/active", json={"task_id": "t-nope"}).status_code == 404)

print("== 파생 개수 (저장하지 않는다) ==")
c.put("/api/tasks/active", json={"task_id": t1})
rec(t1, cid="s1")
rec(t1, cid="s2")
rec(t1, completed=False, cid="s3")          # 중도 포기는 세지 않는다
rec(t2, cid="s4")                            # 다른 작업
rows = {x["id"]: x for x in c.get("/api/tasks", params={"today": TODAY}).json()["tasks"]}
check("완료 세션만 센다 (t1 = 2)", rows[t1]["done_pomodoros"] == 2)
check("다른 작업은 따로 (t2 = 1)", rows[t2]["done_pomodoros"] == 1)
check("remaining_est = 4 - 2", rows[t1]["remaining_est"] == 2)
check("totals.remaining_est",
      c.get("/api/tasks", params={"today": TODAY}).json()["totals"]["remaining_est"] == 3)

# ★ 같은 client_id 재전송이 이중 계산되지 않는다 (오프라인 큐 재시도 안전성)
rec(t1, cid="s1")
rows = {x["id"]: x for x in c.get("/api/tasks", params={"today": TODAY}).json()["tasks"]}
check("같은 client_id 재전송 -> 개수 그대로", rows[t1]["done_pomodoros"] == 2)

print("== 재정렬 ==")
t3 = mk("세 번째", 1).json()["id"]
c.put("/api/tasks/order", json={"ids": [t3, t1, t2]})
order = [x["id"] for x in c.get("/api/tasks", params={"today": TODAY}).json()["tasks"]]
check("순서 반영", order == [t3, t1, t2])
# ★ 재생목록과 달리 빠진 id 를 살려 둔다 — 낡은 클라이언트가 데이터를 지우면 안 된다
c.put("/api/tasks/order", json={"ids": [t2]})
order = [x["id"] for x in c.get("/api/tasks", params={"today": TODAY}).json()["tasks"]]
check("일부만 보내도 빠진 작업이 살아남는다", set(order) == {t1, t2, t3})
check("보낸 것이 맨 앞", order[0] == t2)

print("== 완료 / 이월 ==")
c.put("/api/tasks/active", json={"task_id": t3})
c.patch(f"/api/tasks/{t3}", json={"completed": True, "at": "2026-08-23T18:00:00+09:00"})
doc = c.get("/api/tasks", params={"today": TODAY}).json()
check("완료 표시됨", [x for x in doc["tasks"] if x["id"] == t3][0]["completed"] is True)
# ★ 끝낸 일에 뽀모도로가 계속 쌓이면 안 된다
check("완료하면 활성이 해제된다", doc["active_task_id"] is None)

ids_today = {x["id"] for x in c.get("/api/tasks", params={"today": TODAY}).json()["tasks"]}
ids_tmrw = {x["id"] for x in c.get("/api/tasks", params={"today": TOMORROW}).json()["tasks"]}
check("완료 항목은 오늘 목록엔 보인다", t3 in ids_today)
check("완료 항목은 다음 날 목록에서 빠진다", t3 not in ids_tmrw)
check("미완료는 다음 날에도 이월된다", {t1, t2} <= ids_tmrw)
wide = {x["id"] for x in c.get("/api/tasks",
                               params={"today": TOMORROW, "days": 7}).json()["tasks"]}
check("days=7 이면 완료 항목도 나온다", t3 in wide)

print("== 삭제 ==")
check("활성 작업 삭제", c.put("/api/tasks/active", json={"task_id": t1}).status_code == 200)
check("삭제 200", c.delete(f"/api/tasks/{t1}").status_code == 200)
check("활성이 해제된다",
      c.get("/api/tasks", params={"today": TODAY}).json()["active_task_id"] is None)
check("없는 작업 삭제 -> 404", c.delete("/api/tasks/t-nope").status_code == 404)
# ★ 작업을 지워도 기록은 살아남는다 — task_name 을 비정규화한 이유
sess = c.get("/api/stats/sessions", params={"limit": 500}).json()["items"]
check("지운 작업의 세션에 이름이 남아 있다",
      any(s.get("task_id") == t1 and s.get("task_name") for s in sess))

print("== 기록 초기화가 개수를 0 으로 만든다 ==")
# ★ 롤업을 저장하지 않는다는 결정적 증거. 저장했다면 세션이 없는 작업에 2/4 가 남는다.
c.post("/api/stats/reset", json={"confirm": True})
rows = c.get("/api/tasks", params={"today": TODAY}).json()["tasks"]
check("모든 작업의 done_pomodoros 가 0",
      all(x["done_pomodoros"] == 0 for x in rows))

print("== 완료 항목 정리 ==")
check("confirm 없으면 400",
      c.post("/api/tasks/clear-completed", json={"confirm": False}).status_code == 400)
r = c.post("/api/tasks/clear-completed", json={"confirm": True, "today": TODAY})
check("정리 200", r.status_code == 200)
check("완료 항목이 사라짐",
      not any(x["completed"] for x in
              c.get("/api/tasks", params={"today": TODAY}).json()["tasks"]))

print(f"\ntasks OK - {checks} checks passed")
