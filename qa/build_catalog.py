"""media_catalog.json 생성기 (유지보수 전용 — 앱 부팅 경로에 포함되지 않는다).

실행:  .venv\\Scripts\\python.exe qa\\build_catalog.py

왜 손으로 쓰지 않는가:
  조사 단계에서 그럴듯해 보이는 archive.org 식별자 6개를 확인했더니 3개가 제목만으로는
  알 수 없는 이유로 사용 불가였다 (MP3 0개 / 4.5GB 단일 ZIP / 마이크 원본 스템).
  게다가 archive.org 의 라이선스 메타데이터는 사용자 입력이라 누락이 흔하다 —
  Musopen 컬렉션 34개 중 28개가 licenseurl 자체가 없었다.
  따라서 실제 API 를 조회해 생성하고, **라이선스가 확인되지 않은 항목은 거부**한다.

리졸버 2종:
  - archive.org      : https://archive.org/metadata/<identifier>
  - Wikimedia Commons: https://commons.wikimedia.org/w/api.php  (카테고리 → imageinfo)
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Wikimedia 는 설명이 없는 User-Agent 를 강하게 제한한다. 무엇을 하는 도구인지 밝힌다.
# (개인정보는 넣지 않는다.)
from server.terms import (  # noqa: E402  (sys.path 설정 뒤여야 한다)
    IA_FORMATS, LICENSE_BAD, LICENSE_OK, clean_title, parse_length, to_korean,
)

UA = "pomodoro-timer-catalog-builder/0.1 (offline personal study timer; low-volume metadata reads)"
TIMEOUT = 30



# ── 소스 정의 ────────────────────────────────────────────────────────────────
# playlist: 이 소스의 트랙이 기본으로 들어갈 재생목록
# tier    : core = 첫 실행 자동 다운로드, extra = 사용자가 "전체 내려받기" 를 눌러야 받음
# limit   : 너무 많은 트랙이 한 소스에서 오지 않게 자른다
# pick    : 트랙 제목 정규식 — 특정 악장만 고르고 싶을 때

SOURCES = [
    # ── 집중: 솔로 피아노 바흐 (균질하고 조용하며 기복이 없다) ──────────────
    {
        "kind": "ia",
        "identifier": "bach-well-tempered-clavier-book-1",
        "source_id": "ia:wtc1",
        "prefix": "wtc1",
        "album_ko": "바흐 — 평균율 클라비어 곡집 1권",
        "performer_ko": "키미코 이시자카 (피아노)",
        "composer_ko": "요한 제바스티안 바흐",
        "playlist": "focus",
        "tier": "core",
        "limit": 12,
        "pick": r"prelude",       # 전주곡만 — 푸가는 성부가 많아 더 주의를 끈다
    },
    {
        "kind": "ia",
        "identifier": "The_Open_Goldberg_Variations-11823",
        "source_id": "ia:goldberg",
        "prefix": "gv",
        "album_ko": "바흐 — 골드베르크 변주곡",
        "performer_ko": "키미코 이시자카 (피아노)",
        "composer_ko": "요한 제바스티안 바흐",
        "playlist": "focus",
        "tier": "core",
        "limit": 8,
    },
    {
        "kind": "ia",
        "identifier": "musopen-chopin",
        "source_id": "ia:chopin",
        "prefix": "chp",
        "album_ko": "쇼팽 — 야상곡·전주곡 모음",
        "performer_ko": "Musopen",
        "composer_ko": "프레데리크 쇼팽",
        "playlist": "focus",
        "tier": "extra",
        "limit": 14,
    },
    {
        "kind": "ia",
        "identifier": "pandacd-715-js-bach-the-art-of-the-fugue-kunst-der-fuge-bwv-1080",
        "source_id": "ia:artoffugue",
        "prefix": "aof",
        "album_ko": "바흐 — 푸가의 기법 BWV 1080",
        "performer_ko": "PandaCD",
        "composer_ko": "요한 제바스티안 바흐",
        "playlist": "focus",
        "tier": "extra",
        "limit": 10,
    },
    # ── 휴식: 미군 군악대 (미국 정부 저작물 = 완전 퍼블릭 도메인) ───────────
    # 관현악 왈츠·행진곡풍이라 솔로 피아노 바흐와 편성·기분이 확연히 대비된다.
    # 저작자 표시도 ShareAlike 도 필요 없고 연주 품질도 전문가급이다.
    {
        "kind": "commons",
        "category": "Audio files of music by the United States Air Force Band",
        "source_id": "commons:usaf-band",
        "prefix": "usaf",
        "album_ko": "미 공군 군악대 연주 모음",
        "performer_ko": "United States Air Force Band",
        "composer_ko": None,
        "playlist": "break",
        "tier": "core",
        "limit": 8,
        "max_bytes": 12_000_000,
    },
    {
        "kind": "commons",
        "category": "Audio files of classical music by the United States Marine Band",
        "source_id": "commons:usmc-band",
        "prefix": "usmc",
        "album_ko": "미 해병대 군악대 연주 모음",
        "performer_ko": "United States Marine Band",
        "composer_ko": None,
        "playlist": "break",
        "tier": "core",
        "limit": 10,
        "max_bytes": 12_000_000,
    },
    {
        "kind": "commons",
        "category": "Audio files of music by the United States Navy Band",
        "source_id": "commons:usn-band",
        "prefix": "usn",
        "album_ko": "미 해군 군악대 연주 모음",
        "performer_ko": "United States Navy Band",
        "composer_ko": None,
        "playlist": "break",
        "tier": "extra",
        "limit": 16,
    },
]


# ── HTTP ─────────────────────────────────────────────────────────────────────

_last_call = [0.0]
MIN_INTERVAL = 3.0      # Commons 는 연속 요청에 429 를 낸다 — 넉넉히 간격을 둔다


def _get_json(url: str, *, retries: int = 4) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(retries):
        gap = time.monotonic() - _last_call[0]
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        _last_call[0] = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            # MediaWiki 는 오류를 HTTP 200 + {"error": ...} 로 돌려준다.
            # 이걸 흘려보내면 "결과 0건"으로 보여 원인을 못 찾는다.
            if isinstance(data, dict) and data.get("error"):
                err = data["error"]
                print(f"    ! API 오류: {err.get('code')} — {str(err.get('info'))[:120]}")
                return None
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f"    . 429 — {wait}초 대기 후 재시도")
                time.sleep(wait)
                continue
            print(f"    ! HTTP {e.code}: {url[:80]}")
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            print(f"    ! 요청 실패: {e}")
            return None
    return None


# ── 이름 정리 ────────────────────────────────────────────────────────────────

# ── archive.org 리졸버 ───────────────────────────────────────────────────────

def resolve_ia(src: dict) -> tuple[dict, list[dict]] | None:
    ident = src["identifier"]
    print(f"  archive.org/{ident} 조회 중 ...")
    meta = _get_json(f"https://archive.org/metadata/{urllib.parse.quote(ident)}")
    if not meta or not meta.get("files"):
        print("    ! 메타데이터가 비어 있음 — 건너뜀")
        return None

    md = meta.get("metadata", {}) or {}
    lic = md.get("licenseurl") or md.get("rights") or ""
    if not lic:
        # ★ 라이선스가 확인되지 않으면 조용히 넣지 않는다.
        print(f"    ! licenseurl/rights 없음 — 거부 (Musopen 컬렉션 34개 중 28개가 이랬다)")
        return None
    if LICENSE_BAD.search(lic) or not LICENSE_OK.search(lic):
        print(f"    ! 허용되지 않는 라이선스: {lic} — 거부")
        return None

    audio = [f for f in meta["files"] if f.get("format") in IA_FORMATS]
    if not audio:
        formats = sorted({f.get("format", "?") for f in meta["files"]})[:6]
        print(f"    ! 사용 가능한 MP3 없음 (포맷: {formats}) — 건너뜀")
        return None

    pick = re.compile(src["pick"], re.I) if src.get("pick") else None
    audio.sort(key=lambda f: f.get("name", ""))

    tracks = []
    n = 0
    for f in audio:
        name = f.get("name", "")
        title = clean_title(name, segment="last")
        if pick and not pick.search(name):
            continue
        size = int(f.get("size", 0) or 0)
        if size <= 0:
            continue
        n += 1
        if n > src["limit"]:
            break
        tid = f"{src['prefix']}-{n:02d}"
        tracks.append({
            "id": tid,
            "source_id": src["source_id"],
            "title_orig": title,
            "title_ko": to_korean(title),
            "composer_ko": src.get("composer_ko"),
            "performer_ko": src.get("performer_ko"),
            "duration_seconds": parse_length(f.get("length")),
            "filename": f"{tid}.mp3",
            "url": f"https://archive.org/download/{urllib.parse.quote(ident)}/"
                   f"{urllib.parse.quote(name, safe='')}",
            "bytes": size,
            "sha1": (f.get("sha1") or "").lower() or None,
            "sha256": None,
            "playlists": [src["playlist"]],
            "tier": src["tier"],
            "license": lic,
        })

    source = {
        "source_id": src["source_id"],
        "provider": "archive.org",
        "identifier": ident,
        "album_orig": md.get("title") or ident,
        "album_ko": src["album_ko"],
        "performer_ko": src.get("performer_ko"),
        "license": lic,
        "license_url": lic if lic.startswith("http") else None,
        "attribution_ko": f"{src.get('performer_ko') or ''} — {src['album_ko']}".strip(" —"),
        "details_url": f"https://archive.org/details/{ident}",
    }
    print(f"    OK {len(tracks)}곡  ({lic})")
    return source, tracks


# ── Wikimedia Commons 리졸버 ─────────────────────────────────────────────────

COMMONS_API = "https://commons.wikimedia.org/w/api.php"


def resolve_commons(src: dict) -> tuple[dict, list[dict]] | None:
    cat = src["category"]
    print(f"  Commons: Category:{cat} 조회 중 ...")

    # ★ 카테고리 목록과 imageinfo 를 **한 번의 요청**으로 가져온다.
    #   따로 조회하면 카테고리당 10회 이상 요청이 나가 429 로 막힌다.
    #   maxlag 는 위키미디어 권장 파라미터(서버가 밀릴 때 물러나라는 신호)다.
    base = {
        "action": "query", "format": "json", "maxlag": "5",
        "generator": "categorymembers",
        "gcmtitle": f"Category:{cat}", "gcmtype": "file", "gcmlimit": "50",
        "prop": "imageinfo",
        "iiprop": "url|size|sha1|mime|extmetadata",
    }

    tracks: list[dict] = []
    seen_license = None
    rejected: dict[str, int] = collections.Counter()
    non_mp3 = 0
    oversized = 0
    cont: dict[str, str] = {}
    n = 0

    for _round in range(6):        # 넉넉잡아 6회 — limit 을 채우면 먼저 빠져나간다
        params = {**base, **cont}
        data = _get_json(f"{COMMONS_API}?{urllib.parse.urlencode(params)}")
        if not data:
            break
        pages = data.get("query", {}).get("pages", {})
        for page in sorted(pages.values(), key=lambda p: p.get("title", "")):
            if n >= src["limit"]:
                break
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            # ★ Wikimedia 는 url 뒤에 UTM 추적 파라미터를 붙인다
            #   (....mp3?utm_source=...&utm_campaign=imageinfo). 쿼리를 떼지 않으면
            #   확장자 검사가 전부 실패하고 "MP3 가 하나도 없다" 는 잘못된 결론이 난다.
            url = (info.get("url") or "").split("?", 1)[0]
            size = int(info.get("size", 0) or 0)
            # MP3 만. Safari 는 18.4 에서야 Ogg Vorbis 를 지원한다.
            if not url.lower().endswith(".mp3") or size <= 0:
                non_mp3 += 1
                continue
            # 한 곡이 지나치게 크면 첫 실행 다운로드가 하염없이 길어진다
            if src.get("max_bytes") and size > src["max_bytes"]:
                oversized += 1
                continue

            ext = info.get("extmetadata", {}) or {}
            lic = (ext.get("LicenseShortName", {}) or {}).get("value", "")
            usage = (ext.get("UsageTerms", {}) or {}).get("value", "")
            blob = f"{lic} {usage}"
            # 미국 정부 저작물 / PD / CC0 만. 스위스 기준 PD(PDP-CH) 는 미국에서
            # 저작권이 살아 있으므로 배제한다.
            if "PDP-CH" in blob or LICENSE_BAD.search(blob) or not LICENSE_OK.search(blob):
                rejected[lic or usage or "(라이선스 정보 없음)"] += 1
                continue

            raw = page["title"].split(":", 1)[-1]
            title = clean_title(raw, segment="first")
            n += 1
            tid = f"{src['prefix']}-{n:02d}"
            seen_license = seen_license or lic
            tracks.append({
                "id": tid,
                "source_id": src["source_id"],
                "title_orig": title,
                "title_ko": to_korean(title),
                "composer_ko": src.get("composer_ko"),
                "performer_ko": src.get("performer_ko"),
                "duration_seconds": None,
                "filename": f"{tid}.mp3",
                "url": url,
                "bytes": size,
                "sha1": (info.get("sha1") or "").lower() or None,
                "sha256": None,
                "playlists": [src["playlist"]],
                "tier": src["tier"],
                "license": lic,
            })

        if n >= src["limit"] or "continue" not in data:
            break
        cont = data["continue"]

    if not tracks:
        print("    ! 조건을 만족하는 MP3 가 없음 — 건너뜀")
        for lic, cnt in rejected.most_common(5):
            print(f"      라이선스 거부: {lic!r} x{cnt}")
        if non_mp3:
            print(f"      MP3 아님/크기 0: {non_mp3}개")
        if oversized:
            print(f"      용량 초과: {oversized}개")
        return None
    if rejected:
        print(f"    . 라이선스로 거부 {sum(rejected.values())}개")

    source = {
        "source_id": src["source_id"],
        "provider": "wikimedia-commons",
        "identifier": cat,
        "album_orig": cat,
        "album_ko": src["album_ko"],
        "performer_ko": src.get("performer_ko"),
        "license": seen_license or "Public domain",
        "license_url": "https://commons.wikimedia.org/wiki/Commons:Licensing",
        "attribution_ko": src.get("performer_ko") or src["album_ko"],
        "details_url": f"https://commons.wikimedia.org/wiki/Category:{urllib.parse.quote(cat)}",
    }
    print(f"    OK {len(tracks)}곡  ({seen_license})")
    return source, tracks


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    sources: list[dict] = []
    tracks: list[dict] = []

    print("카탈로그 생성 시작\n")
    for src in SOURCES:
        result = resolve_ia(src) if src["kind"] == "ia" else resolve_commons(src)
        if result is None:
            continue
        s, t = result
        sources.append(s)
        tracks.extend(t)

    if not tracks:
        print("\n실패: 사용 가능한 트랙이 하나도 없습니다.")
        return 1

    doc = {
        "version": 1,
        "generated_at": None,      # 결정론적 출력을 위해 타임스탬프를 넣지 않는다
        "generator": "qa/build_catalog.py",
        "sources": sources,
        "tracks": tracks,
    }

    out = ROOT / "media_catalog.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    total = sum(t["bytes"] for t in tracks)
    core = [t for t in tracks if t["tier"] == "core"]
    core_bytes = sum(t["bytes"] for t in core)
    focus = [t for t in tracks if "focus" in t["playlists"]]
    brk = [t for t in tracks if "break" in t["playlists"]]

    print(f"\n{out.name} 저장 완료")
    print(f"  소스 {len(sources)}개 · 트랙 {len(tracks)}곡")
    print(f"  집중 {len(focus)}곡 / 휴식 {len(brk)}곡")
    print(f"  core {len(core)}곡 ({core_bytes/1e6:.0f}MB) · 전체 {total/1e6:.0f}MB")

    from server import catalog
    problems = catalog.validate(doc)
    if problems:
        print("\n검증 실패:")
        for p in problems[:20]:
            print("  -", p)
        return 1
    print("  검증 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
