"""기록 API 단위 테스트.

실행:  .venv\\Scripts\\python.exe qa\\test_stats.py

config.DATA_DIR 를 임시 폴더로 갈아끼워 격리한다. 이게 가능한 이유는 server 의 모든
모듈이 경로를 import 시점이 아니라 **호출 시점**에 config 에서 읽기 때문이다.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Windows 기본 콘솔(CP949)은 한글 출력이 깨진다 (launcher.py 와 같은 처리)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from server import config  # noqa: E402

config.DATA_DIR = pathlib.Path(tempfile.mkdtemp(prefix="pomo-stats-"))
config.MEDIA_DIR = config.DATA_DIR / "media"
config.ensure_dirs()

from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402

c = TestClient(create_app())
checks = 0


def check(label: str, cond: bool) -> None:
    global checks
    checks += 1
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    print(f"  ok  {label}")


def rec(start, end, *, completed=True, phase="focus", planned=1500, actual=1500, cid=None):
    body = {
        "phase": phase,
        "started_at": start,
        "ended_at": end,
        "planned_seconds": planned,
        "actual_seconds": actual,
        "completed": completed,
    }
    if cid:
        body["client_id"] = cid
    return c.post("/api/stats/sessions", json=body)


print("== 타임존 가드 ==")
# 1) naive datetime 은 스키마 경계에서 거부되어야 한다. 이 가드가 없으면 자정 근처
#    세션의 날짜가 조용히 하루 어긋난다.
check("naive datetime -> 422",
      rec("2026-08-22T09:00:00", "2026-08-22T09:25:00").status_code == 422)

# 2) 정상 기록
r = rec("2026-08-22T09:00:00+09:00", "2026-08-22T09:25:00+09:00")
check("aware datetime -> 200", r.status_code == 200)
check("local_date = 2026-08-22", r.json()["local_date"] == "2026-08-22")

# 3) 자정 넘김: 23:41 시작 -> 00:06 종료. day_start_hour=0 이므로 종료일 기준 8/23.
#    (프론트가 toISOString() 의 Z 를 보냈다면 UTC 로는 8/22 라 여기서 틀렸을 것이다.)
r = rec("2026-08-22T23:41:00+09:00", "2026-08-23T00:06:00+09:00")
check("자정 넘긴 세션 -> 2026-08-23", r.json()["local_date"] == "2026-08-23")

print("== day_start_hour 소급 재계산 ==")
r = c.put("/api/settings", json={"records": {"day_start_hour": 4, "daily_goal": 8}})
check("day_start_hour=4 저장", r.status_code == 200)
check("재계산된 세션이 있다", r.json()["recomputed_sessions"] >= 1)
items = c.get("/api/stats/sessions", params={"limit": 100}).json()["items"]
midnight = [s for s in items if s["ended_at"].startswith("2026-08-23T00:06")][0]
check("자정 세션이 소급해서 2026-08-22 로 이동", midnight["local_date"] == "2026-08-22")
# 원복
c.put("/api/settings", json={"records": {"day_start_hour": 0, "daily_goal": 8}})
items = c.get("/api/stats/sessions", params={"limit": 100}).json()["items"]
midnight = [s for s in items if s["ended_at"].startswith("2026-08-23T00:06")][0]
check("day_start_hour=0 으로 되돌리면 다시 2026-08-23", midnight["local_date"] == "2026-08-23")

print("== 멱등성 (오프라인 큐 재전송) ==")
r1 = rec("2026-08-22T10:00:00+09:00", "2026-08-22T10:25:00+09:00", cid="dup-1")
check("최초 전송 duplicate=False", r1.json()["duplicate"] is False)
r2 = rec("2026-08-22T10:00:00+09:00", "2026-08-22T10:25:00+09:00", cid="dup-1")
check("재전송 duplicate=True", r2.json()["duplicate"] is True)
check("재전송해도 id 가 같다", r1.json()["id"] == r2.json()["id"])
same_cid = [s for s in c.get("/api/stats/sessions", params={"limit": 500}).json()["items"]
            if s.get("client_id") == "dup-1"]
check("기록은 1건만 남는다", len(same_cid) == 1)

print("== 집계 규칙 ==")
before = c.get("/api/stats/today", params={"today": "2026-08-22"}).json()
rec("2026-08-22T11:00:00+09:00", "2026-08-22T11:07:00+09:00", completed=False, actual=420)
after = c.get("/api/stats/today", params={"today": "2026-08-22"}).json()
check("중도 포기는 뽀모도로 개수에 안 잡힌다",
      after["pomodoro_count"] == before["pomodoro_count"])
check("중도 포기는 aborted_count 에 잡힌다", after["aborted_count"] == 1)
check("중도 포기 시간은 focus_seconds 에 안 더해진다",
      after["focus_seconds"] == before["focus_seconds"])
check("aborted_seconds = 420", after["aborted_seconds"] == 420)

rec("2026-08-22T13:00:00+09:00", "2026-08-22T13:25:00+09:00",
    phase="short_break", planned=300, actual=300)
after2 = c.get("/api/stats/today", params={"today": "2026-08-22"}).json()
check("휴식 세션은 뽀모도로로 세지 않는다",
      after2["pomodoro_count"] == after["pomodoro_count"])

print("== 시리즈 ==")
s = c.get("/api/stats/series", params={"days": 7, "today": "2026-08-22"}).json()
check("7일 요청 -> 정확히 7개", len(s) == 7)
check("마지막이 오늘", s[-1]["date"] == "2026-08-22")
check("첫날은 6일 전", s[0]["date"] == "2026-08-16")
check("기록 없는 날도 0 으로 채워진다", all("pomodoro_count" in d for d in s))
check("빈 날은 0", s[0]["pomodoro_count"] == 0)

print("== 연속 달성 일수 ==")
st = c.get("/api/stats/streak", params={"today": "2026-08-22"}).json()
check("오늘 기록이 있으면 includes_today=True", st["includes_today"] is True)
check("current >= 1", st["current"] >= 1)
# ★ 오늘 기록이 없어도 어제까지 이어졌으면 연속을 유지해야 한다.
#   (llm_wiki 의 _streak 는 여기서 0 을 돌려준다 — 오전 9시에 12일 연속이 0 으로 보인다.)
st2 = c.get("/api/stats/streak", params={"today": "2026-08-24"}).json()
check("오늘 기록이 없어도 어제까지 이어졌으면 연속 유지", st2["current"] >= 1)
check("그때 includes_today=False", st2["includes_today"] is False)
check("best 를 함께 돌려준다", st2["best"] >= 1)

print("== 검증 경계 ==")
check("planned_seconds=0 -> 422",
      rec("2026-08-22T14:00:00+09:00", "2026-08-22T14:25:00+09:00", planned=0).status_code == 422)
check("ended_at < started_at -> 422",
      rec("2026-08-22T15:00:00+09:00", "2026-08-22T14:00:00+09:00").status_code == 422)
r = c.post("/api/stats/sessions", json={
    "phase": "focus", "started_at": "2026-08-22T16:00:00+09:00",
    "ended_at": "2026-08-22T16:25:00+09:00", "planned_seconds": 1500,
    "actual_seconds": 1500, "bogus": 1})
check("알 수 없는 키 -> 422 (extra=forbid)", r.status_code == 422)
check("잘못된 phase -> 422",
      rec("2026-08-22T17:00:00+09:00", "2026-08-22T17:25:00+09:00",
          phase="nap").status_code == 422)

print("== ★ 옛 페이로드 호환 (오프라인 큐 보호) ==")
# 업그레이드 시점에 pomo.queue.v1 에 남아 있던 페이로드는 새 필드가 없다.
# 이게 422 가 되면 stats.js 의 "4xx 는 재시도하지 않는다" 규칙에 걸려 **영구 폐기**된다.
# 25분씩 버틴 세션들이 조용히 사라지는 것이다.
old_payload = {
    "phase": "focus",
    "started_at": "2026-08-22T08:00:00+09:00",
    "ended_at": "2026-08-22T08:25:00+09:00",
    "planned_seconds": 1500, "actual_seconds": 1500,
    "completed": True, "cycle_index": 0,
    "interruptions": 2,                      # 옛 필드만 있다
    "client_id": "legacy-1",
}
check("옛 페이로드 -> 200 (폐기되지 않는다)",
      c.post("/api/stats/sessions", json=old_payload).status_code == 200)

new_payload = {**old_payload, "client_id": "new-1",
               "interruptions_internal": 3, "interruptions_external": 1,
               "task_id": "t-abc", "task_name": "논문"}
check("새 페이로드 -> 200", c.post("/api/stats/sessions", json=new_payload).status_code == 200)

items = c.get("/api/stats/sessions", params={"limit": 500}).json()["items"]
legacy = [s for s in items if s.get("client_id") == "legacy-1"][0]
fresh = [s for s in items if s.get("client_id") == "new-1"][0]
check("옛 레코드의 새 필드는 0", legacy["interruptions_internal"] == 0)
check("새 레코드의 분류값 저장", fresh["interruptions_internal"] == 3 and fresh["interruptions_external"] == 1)
check("작업 필드 저장", fresh["task_id"] == "t-abc" and fresh["task_name"] == "논문")

# ★ 옛 interruptions 는 자동 감지된 일시정지지 사용자가 분류한 값이 아니다.
#   내부로 귀속하면 "이번 주 내부 21회" 가 지어낸 숫자가 된다.
ins = c.get("/api/stats/insights", params={"days": 7, "today": "2026-08-22"}).json()
check("옛 값은 미분류로 잡힌다", ins["interruptions"]["unclassified"] >= 2)
check("내부로 새지 않는다", ins["interruptions"]["internal"] == 3)
check("외부 집계", ins["interruptions"]["external"] == 1)

print("== 작업별 롤업 ==")
by_task = ins["by_task"]
check("task_id 없는 세션도 버킷으로 나온다", any(b["task_id"] is None for b in by_task))
check("작업 버킷이 있다", any(b["task_id"] == "t-abc" for b in by_task))
named = [b for b in by_task if b["task_id"] == "t-abc"][0]
check("이름이 붙는다", named["task_name"] == "논문")

# 이름을 바꿔 재전송하면 가장 최근 이름을 쓴다
c.post("/api/stats/sessions", json={**new_payload, "client_id": "new-2",
                                    "task_name": "논문(수정)"})
ins2 = c.get("/api/stats/insights", params={"days": 7, "today": "2026-08-22"}).json()
named2 = [b for b in ins2["by_task"] if b["task_id"] == "t-abc"][0]
check("가장 최근 task_name 을 쓴다", named2["task_name"] == "논문(수정)")

print("== CSV 내보내기 ==")
r = c.get("/api/stats/export.csv")
check("200", r.status_code == 200)
check("text/csv", r.headers["content-type"].startswith("text/csv"))
check("첨부 파일명", "pomodoro-sessions.csv" in r.headers.get("content-disposition", ""))
body = r.content.decode("utf-8")
# ★ BOM 이 없으면 엑셀(Windows)이 한글 작업명을 깨뜨린다
check("UTF-8 BOM 으로 시작", body.startswith("\ufeff"))
lines = [ln for ln in body.lstrip("\ufeff").split("\n") if ln.strip()]
check("헤더 + 데이터 행", len(lines) >= 2)
check("헤더에 task_name 포함", "task_name" in lines[0])
check("한글이 살아 있다", "논문" in body)

# 쉼표·따옴표가 든 작업명이 라운드트립되는가
c.post("/api/stats/sessions", json={**new_payload, "client_id": "csv-quote",
                                    "task_name": '보고서, "1장" 초안'})
body2 = c.get("/api/stats/export.csv").content.decode("utf-8")
import csv as _csv
import io as _io
rows_csv = list(_csv.reader(_io.StringIO(body2.lstrip("\ufeff"))))
names = [r[11] for r in rows_csv[1:] if len(r) > 11]
check("쉼표·따옴표가 든 이름이 온전히 복원된다", '보고서, "1장" 초안' in names)

print("== 삭제 / 초기화 ==")
sid = c.get("/api/stats/sessions", params={"limit": 1}).json()["items"][0]["id"]
check("세션 삭제", c.delete(f"/api/stats/sessions/{sid}").json()["deleted"] is True)
check("없는 세션 삭제 -> False",
      c.delete("/api/stats/sessions/nope").json()["deleted"] is False)
check("confirm 없는 초기화 -> 400",
      c.post("/api/stats/reset", json={"confirm": False}).status_code == 400)
check("초기화", c.post("/api/stats/reset", json={"confirm": True}).status_code == 200)
check("초기화 후 오늘 개수 0",
      c.get("/api/stats/today", params={"today": "2026-08-22"}).json()["pomodoro_count"] == 0)

print(f"\nstats OK - {checks} checks passed")
