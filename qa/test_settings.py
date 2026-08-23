"""설정 API 단위 테스트.

실행:  .venv\\Scripts\\python.exe qa\\test_settings.py
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

config.DATA_DIR = pathlib.Path(tempfile.mkdtemp(prefix="pomo-settings-"))
config.MEDIA_DIR = config.DATA_DIR / "media"
config.ensure_dirs()

from fastapi.testclient import TestClient  # noqa: E402

from server import playlists  # noqa: E402
from server.app import create_app  # noqa: E402

playlists.ensure_file()
c = TestClient(create_app())
checks = 0


def check(label: str, cond: bool) -> None:
    global checks
    checks += 1
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    print(f"  ok  {label}")


print("== 기본 세트 ==")
d = c.get("/api/settings").json()
sets = d["timer"]["sets"]
check("기본 세트는 1개", len(sets) == 1)
check("기본 세트 = 집중 25분", sets[0]["focus_seconds"] == 1500)
check("기본 세트 = 휴식 5분", sets[0]["break_seconds"] == 300)
check("기본 반복 켜짐", d["timer"]["repeat"] is True)
check("긴 휴식 설정이 없다", "long_break_seconds" not in d["timer"])
check("사이클 수 설정이 없다", "cycles_until_long_break" not in d["timer"])
# ★ 기본 음량을 낮게 두는 것은 연구 근거에 따른 의도적 선택이다
#   (Thompson/Schellenberg/Letnic 2012 — 방해 정도는 템포보다 음량이 좌우)
check("music_volume 기본값이 낮다 (<= 60)", d["audio"]["music_volume"] <= 60)
check("silent_mode 기본 False", d["audio"]["silent_mode"] is False)
check("theme = auto", d["ui"]["theme"] == "auto")

print("== 백색 소음 기본값 ==")
check("기본은 꺼짐", d["audio"]["noise_enabled"] is False)
# 백색 소음은 고역이 강해 오래 들으면 피로하다 — 기본은 브라운
check("기본 종류는 brown", d["audio"]["noise_type"] == "brown")
check("소음 음량도 낮게 (<= 50)", d["audio"]["noise_volume"] <= 50)
check("기본은 집중 구간에만", d["audio"]["noise_phases"] == "focus")

print("== 세트 편집 ==")
three = {"sets": [
    {"focus_seconds": 1500, "break_seconds": 300, "label": None},
    {"focus_seconds": 1500, "break_seconds": 300, "label": None},
    {"focus_seconds": 3000, "break_seconds": 900, "label": "긴 세트"},
], "repeat": True, "auto_start_break": True, "auto_start_focus": False,
    "interruption_policy": "ask"}
r = c.put("/api/settings", json={"timer": three})
check("세트 3개 저장", r.status_code == 200)
saved = c.get("/api/settings").json()["timer"]["sets"]
check("3개가 순서대로 저장됨", len(saved) == 3 and saved[2]["break_seconds"] == 900)
check("라벨도 저장됨", saved[2]["label"] == "긴 세트")

check("세트 0개 -> 422",
      c.put("/api/settings", json={"timer": {**three, "sets": []}}).status_code == 422)
check("세트 25개 -> 422",
      c.put("/api/settings", json={"timer": {**three, "sets": three["sets"] * 9}}).status_code == 422)
check("세트 시간 0 -> 422",
      c.put("/api/settings", json={"timer": {**three,
          "sets": [{"focus_seconds": 0, "break_seconds": 300}]}}).status_code == 422)

print("== 예전 설정 마이그레이션 ==")
# 25/5/15 + 4회 -> [25/5, 25/5, 25/5, 25/15] 로 변환되어야 한다
from server import settings as settings_mod  # noqa: E402
legacy = settings_mod._migrate({"timer": {
    "focus_seconds": 1500, "short_break_seconds": 300, "long_break_seconds": 900,
    "cycles_until_long_break": 4, "auto_start_break": True,
    "auto_start_focus": False, "interruption_policy": "ask"}})
migrated = settings_mod.Settings.model_validate(legacy).timer.sets
check("4세트로 변환", len(migrated) == 4)
check("앞 3세트는 5분 휴식", all(x.break_seconds == 300 for x in migrated[:3]))
check("마지막 세트만 15분 휴식", migrated[3].break_seconds == 900)

print("== 범위 검증 ==")
check("music_volume=150 -> 422",
      c.put("/api/settings", json={"audio": {**d["audio"], "music_volume": 150}}).status_code == 422)
check("noise_volume=150 -> 422",
      c.put("/api/settings", json={"audio": {**d["audio"], "noise_volume": 150}}).status_code == 422)
check("day_start_hour=13 -> 422",
      c.put("/api/settings", json={"records": {**d["records"], "day_start_hour": 13}}).status_code == 422)
check("잘못된 chime_variant -> 422",
      c.put("/api/settings", json={"audio": {**d["audio"], "chime_variant": "airhorn"}}).status_code == 422)
check("잘못된 noise_type -> 422",
      c.put("/api/settings", json={"audio": {**d["audio"], "noise_type": "vuvuzela"}}).status_code == 422)
check("알 수 없는 키 -> 422 (extra=forbid)",
      c.put("/api/settings", json={"timer": {**three, "bogus": 1}}).status_code == 422)

print("== 재생목록 참조 무결성 ==")
r = c.put("/api/settings", json={"audio": {**d["audio"], "focus_playlist_id": "nope"}})
check("존재하지 않는 재생목록 -> 422", r.status_code == 422)
check("오류 메시지에 해당 id 가 들어간다", "nope" in str(r.json()["detail"]))

print("== 정상 변경 + 영속 ==")
one = {**three, "sets": [{"focus_seconds": 3000, "break_seconds": 600, "label": None}]}
r = c.put("/api/settings", json={"timer": one})
check("변경 성공", r.status_code == 200)
check("changed 에 timer 가 들어간다", "timer" in r.json()["changed"])
check("디스크에 반영됨",
      c.get("/api/settings").json()["timer"]["sets"][0]["focus_seconds"] == 3000)
r = c.put("/api/settings", json={"timer": one})
check("같은 값 재전송이면 changed 가 비어 있다", r.json()["changed"] == [])

print("== 초 단위 계약 (테스트가 3초 사이클을 만들 수 있는가) ==")
r = c.put("/api/settings", json={"timer": {
    "sets": [{"focus_seconds": 3, "break_seconds": 2, "label": None}],
    "repeat": True, "auto_start_break": True,
    "auto_start_focus": True, "interruption_policy": "ask"}})
check("3초 집중 세션 설정 가능 -> 200", r.status_code == 200)
check("반영 확인", c.get("/api/settings").json()["timer"]["sets"][0]["focus_seconds"] == 3)

print("== 초기화 ==")
check("confirm 없으면 400",
      c.post("/api/settings/reset", json={"confirm": False}).status_code == 400)
r = c.post("/api/settings/reset", json={"confirm": True})
check("초기화 성공", r.status_code == 200)
check("기본값 복원 — 세트 1개", len(r.json()["timer"]["sets"]) == 1)
check("기본값 복원 — 25분", r.json()["timer"]["sets"][0]["focus_seconds"] == 1500)

print("== Origin 가드 (CSRF / DNS rebinding) ==")
evil = {"Origin": "https://evil.example"}
check("cross-site POST -> 403",
      c.post("/api/settings/reset", json={"confirm": True}, headers=evil).status_code == 403)
check("cross-site PUT -> 403",
      c.put("/api/settings", json={"timer": three}, headers=evil).status_code == 403)
check("cross-site GET 은 통과 (읽기는 안전)",
      c.get("/api/settings", headers=evil).status_code == 200)
check("Origin 없는 요청은 통과 (curl / 앱)",
      c.post("/api/settings/reset", json={"confirm": True}).status_code == 200)

print(f"\nsettings OK - {checks} checks passed")
