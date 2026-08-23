"""음원 검색 단위 테스트 — **네트워크 0건**.

실행:  .venv\\Scripts\\python.exe qa\\test_search.py

search._http_json 을 픽스처로 갈아끼워 네트워크를 완전히 차단한다.
여기 있는 검사들은 전부 **실측으로 확인된 함정**의 회귀 방지다.
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

config.DATA_DIR = pathlib.Path(tempfile.mkdtemp(prefix="pomo-search-"))
config.MEDIA_DIR = config.DATA_DIR / "media"
config.ensure_dirs()

from fastapi.testclient import TestClient  # noqa: E402

from server import search  # noqa: E402
from server.app import create_app  # noqa: E402

c = TestClient(create_app())
checks = 0
calls: list[str] = []


def check(label: str, cond: bool) -> None:
    global checks
    checks += 1
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    print(f"  ok  {label}")


def mock(payload):
    """_http_json 을 고정 응답으로 교체. 호출 URL 을 calls 에 기록한다."""
    def fake(url, *, timeout):
        calls.append(url)
        return payload(url) if callable(payload) else payload
    search._http_json = fake
    search._cache.clear()
    calls.clear()


print("== 쿼리 빌더 (함정 #1 #2 #3) ==")
q = search.build_query(["chopin"])
# ★ 슬래시를 이스케이프하지 않으면 쿼리 전체가 깨지고 응답에 response 키가 사라진다
check("슬래시가 이스케이프된다", r"licenses\/by\/" in q)
check("이스케이프 안 된 형태가 없다", "licenses/by/" not in q)
# ★ licenseurl:(publicdomain) 은 0건이다 — 와일드카드가 필수
check("와일드카드를 쓴다", "licenseurl:*publicdomain*" in q)
check("0건 형태를 쓰지 않는다", "licenseurl:(publicdomain)" not in q)
# ★ 오디오북이 실제 연주를 밀어낸다
check("librivox 를 제외한다", "-collection:librivoxaudio" in q)
check("stream_only 를 제외한다", "-collection:stream_only" in q)
check("mediatype 을 건다", "mediatype:(audio)" in q)

check("특수문자 이스케이프", search.escape_lucene('a/b"c(d') == r"a\/b\"c\(d")
check("사용자 입력이 구문을 깨뜨리지 못한다",
      r"\(" in search.build_query(["evil) OR x:("]))

print("== 한국어 재작성 ==")
rw = search.rewrite_query("쇼팽 야상곡")
check("쇼팽 야상곡 -> Chopin Nocturne", [t.lower() for t in rw.terms] == ["chopin", "nocturne"])
check("무엇을 바꿨는지 기록한다", len(rw.replaced) == 2)
rw2 = search.rewrite_query("클래식 피아노")
check("보충 사전도 쓴다", [t.lower() for t in rw2.terms] == ["classical", "piano"])
rw3 = search.rewrite_query("국악 판소리")
check("대응어가 없으면 전부 dropped", rw3.all_korean_dropped is True)

print("== 대응어 없는 한국어는 네트워크를 타지 않는다 ==")
mock({"response": {"numFound": 999, "docs": []}})
r = c.get("/api/media/search", params={"q": "국악 판소리"})
check("200", r.status_code == 200)
check("0건", r.json()["total"] == 0)
check("네트워크 호출 없음", len(calls) == 0)
check("한국어 미지원을 알린다", "한국어" in r.json()["query"]["notice_ko"])

print("== ★ 함정 #2 — response 키 없는 200 응답 ==")
# 쿼리가 깨지면 archive.org 는 response 가 아예 없는 본문을 200 으로 낸다.
# body["response"] 를 바로 쓰면 KeyError -> 500 이 나고 원인을 알 수 없다.
mock({"responseHeader": {"status": 0}})
r = c.get("/api/media/search", params={"q": "chopin"})
check("502 로 처리 (KeyError 아님)", r.status_code == 502)
check("한국어 메시지", "archive.org" in r.json()["detail"])

print("== ★ 함정 #5 — /metadata 가 {} 를 200 으로 ==")
mock({})
r = c.get("/api/media/search/item/musopen-chopin")
check("404 로 처리", r.status_code == 404)
check("사유를 설명한다", "제공되지 않습니다" in r.json()["detail"])

print("== 식별자 검증 ==")
mock({"files": [{"name": "a.mp3", "format": "VBR MP3", "size": "1000000"}]})
for bad in ["..%2Fetc", "http:%2F%2Fevil.com", "a" * 120]:
    r = c.get(f"/api/media/search/item/{bad}")
    check(f"잘못된 식별자 거부 ({bad[:16]})", r.status_code in (400, 404))
check("네트워크 호출 없음", len(calls) == 0)

print("== ★ 함정 #4 — 샘플만 제공하는 항목 ==")
SAMPLE_ITEM = {
    "metadata": {"identifier": "x", "title": "샘플뿐",
                 "licenseurl": "http://creativecommons.org/publicdomain/zero/1.0/"},
    "files": [
        {"name": "a_vbr.mp3", "format": "VBR MP3", "size": "4000000", "length": "240"},
        {"name": "a_sample.mp3", "format": "MP3 Sample", "size": "500000", "length": "30"},
    ],
}
mock(SAMPLE_ITEM)
d = c.get("/api/media/search/item/x").json()
check("addable=false", d["addable"] is False)
check("미리듣기라고 설명한다", "미리듣기" in d["reason_ko"])
check("그래도 곡 목록은 보여준다", len(d["tracks"]) > 0)

print("== 접근 제한 (문자열 'true' / str collection) ==")
mock({"metadata": {"identifier": "y", "access-restricted-item": "true",
                   "licenseurl": "http://creativecommons.org/publicdomain/zero/1.0/"},
      "files": [{"name": "a.mp3", "format": "VBR MP3", "size": "4000000", "length": "240"}]})
check("로그인 필요 항목 거부", c.get("/api/media/search/item/y").json()["addable"] is False)

mock({"metadata": {"identifier": "z", "collection": "stream_only",
                   "licenseurl": "http://creativecommons.org/publicdomain/zero/1.0/"},
      "files": [{"name": "a.mp3", "format": "VBR MP3", "size": "4000000", "length": "240"}]})
# collection 은 str 일 수도 list 일 수도 있다 (IA 의 오래된 스키마 흔적)
check("collection 이 문자열이어도 처리한다",
      c.get("/api/media/search/item/z").json()["addable"] is False)

print("== ★ 함정 #7 — 파일 단위 필터 ==")
BIG_ITEM = {
    "metadata": {"identifier": "big", "title": "큰 앨범", "creator": "Musopen",
                 "licenseurl": "http://creativecommons.org/publicdomain/zero/1.0/"},
    "files": (
        [{"name": f"t{i:03d}.mp3", "format": "VBR MP3", "size": "4000000", "length": "240"}
         for i in range(10)]
        + [{"name": f"t{i:03d}.flac", "format": "Flac", "size": "40000000"} for i in range(20)]
        + [{"name": "cover.jpg", "format": "JPEG", "size": "100000"}]
    ),
}
mock(BIG_ITEM)
d = c.get("/api/media/search/item/big").json()
check("전체 파일 수는 그대로", d["total_files"] == 31)
check("쓸 수 있는 MP3 만 센다", d["usable_count"] == 10)
check("나머지는 형식으로 제외", d["skipped"]["format"] == 21)

print("== ★ 함정 #6 — URL 은 files[].name 기반 ==")
url = d["tracks"][0]["url"]
check("download 경로", url.startswith("https://archive.org/download/big/"))
check("파일 이름을 쓴다", url.endswith("t000.mp3"))

print("== ★ 함정 #8 — 말이 안 되는 재생시간 ==")
# 4.4MB 파일에 18:10(=1090초)이면 약 32kbps — 산술적으로 불가능하다
mock({"metadata": {"identifier": "d", "licenseurl": "http://creativecommons.org/publicdomain/zero/1.0/"},
      "files": [
          {"name": "x_vbr.mp3", "format": "VBR MP3", "size": "4402188",
           "length": "18:10", "original": "x"},
          {"name": "x_128.mp3", "format": "128Kbps MP3", "size": "4800000",
           "length": "4:31", "original": "x"},
      ]})
d2 = c.get("/api/media/search/item/d").json()
t = d2["tracks"][0]
check("의심 표시", t["duration_suspect"] is True)
check("의심스러우면 값을 비운다", t["duration_seconds"] is None)

print("== ★ 함정 #10 — 라이선스 게이트는 추가 시점에 ==")
mock({"metadata": {"identifier": "nolic", "title": "라이선스 없음"},
      "files": [{"name": "a.mp3", "format": "VBR MP3", "size": "4000000", "length": "240"}]})
r = c.post("/api/media/search/add",
           json={"identifier": "nolic", "names": ["a.mp3"], "playlists": ["focus"]})
check("라이선스 미확인 -> 409", r.status_code == 409)
check("사유 설명", "라이선스" in r.json()["detail"])
check("아무것도 저장하지 않는다",
      not any(t["origin"] == "search" for t in c.get("/api/media/tracks").json()))

mock({"metadata": {"identifier": "nc",
                   "licenseurl": "https://creativecommons.org/licenses/by-nc-sa/3.0/"},
      "files": [{"name": "a.mp3", "format": "VBR MP3", "size": "4000000", "length": "240"}]})
r = c.post("/api/media/search/add", json={"identifier": "nc", "names": ["a.mp3"]})
check("NC 라이선스 -> 409", r.status_code == 409)

print("== 정상 추가 ==")
mock(BIG_ITEM)
r = c.post("/api/media/search/add",
           json={"identifier": "big", "names": ["t000.mp3", "t001.mp3", "없는파일.mp3"],
                 "playlists": ["focus"], "download": False})
check("200", r.status_code == 200)
j = r.json()
check("2곡 추가", j["added"] == 2)
check("메타데이터에 없는 이름은 건너뛴다", j["skipped"] == 1)
tracks = [t for t in c.get("/api/media/tracks").json() if t["origin"] == "search"]
check("tracks.json 에 2건", len(tracks) == 2)
import re as _re
check("저장 파일명이 u-<uuid>.mp3",
      all(_re.match(r"^u-[0-9a-f]{12}$", t["id"]) for t in tracks))
pl = [p for p in c.get("/api/playlists").json() if p["id"] == "focus"][0]
check("집중 목록에 담긴다", pl["count"] == 2)

r = c.post("/api/media/search/add",
           json={"identifier": "big", "names": ["t000.mp3"], "download": False})
check("같은 파일 재추가는 중복으로 건너뛴다", r.json()["duplicates"] == 1)

print("== 크레딧에 출처가 나온다 ==")
cr = c.get("/api/media/credits").json()
mine = [x for x in cr if x.get("origin") == "search"]
check("검색 출처 섹션", len(mine) == 1)
check("트랙 수", mine[0]["track_count"] == 2)
check("배지", mine[0]["license_badge_ko"] == "퍼블릭 도메인")

print("== 추천 검색 ==")
r = c.get("/api/media/search/presets")
check("200", r.status_code == 200)
check("6개", len(r.json()) == 6)
check("모두 한국어 라벨", all(p.get("label_ko") for p in r.json()))

print("== 캐시 ==")
mock({"response": {"numFound": 3, "docs": [
    {"identifier": "a", "title": "A",
     "licenseurl": "http://creativecommons.org/publicdomain/zero/1.0/"}]}})
c.get("/api/media/search", params={"q": "chopin"})
n1 = len(calls)
r2 = c.get("/api/media/search", params={"q": "chopin"})
check("같은 질의는 재호출하지 않는다", len(calls) == n1)
check("cached 표시", r2.json()["cached"] is True)

print(f"\nsearch OK - {checks} checks passed")
