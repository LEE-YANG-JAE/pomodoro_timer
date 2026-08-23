"""제목 정리 · 한국어화 · 라이선스 판정 — 카탈로그 생성기와 런타임 검색이 공유한다.

★ `qa/` 는 import 가능한 패키지가 아니다. 그래서 공유 코드는 `server/` 에 두고
  `qa/build_catalog.py` 가 이쪽을 import 한다 (그 파일은 이미 sys.path 에 ROOT 를 넣는다).

★ 이 모듈은 순수 함수와 정규식만 담는다 — I/O 도 네트워크도 없다.
  `media_catalog.json` 의 출력을 바꾸면 안 된다. 생성기에서 그대로 옮겨 온 것이고,
  `qa/test_search.py` 가 기존 카탈로그 엔트리로 무해성을 검증한다.
"""
from __future__ import annotations

import re

# archive.org 에서 받아들일 오디오 포맷. ★ .ogg 는 제외한다 —
# Safari 는 18.4 에서야 Ogg Vorbis 를 지원하므로 MP3 만 쓴다.
IA_FORMATS = {"VBR MP3", "128Kbps MP3", "64Kbps MP3"}

# 퍼블릭 도메인 / CC0 / CC BY-SA 만 통과. NC·ND 는 배제.
# ※ 이 두 정규식은 생성기에서 쓰던 것 그대로다. 맨 부분문자열 매칭이라 거칠지만
#   생성기의 동작을 바꾸지 않기 위해 유지한다. 새 코드는 아래 license_kind() 를 쓸 것.
LICENSE_OK = re.compile(
    r"(publicdomain|public domain|cc0|zero|pdm|mark|by-sa|attribution.?share)", re.I)
LICENSE_BAD = re.compile(r"(nc|noncommercial|nd|noderiv)", re.I)


# ── 제목 정리 ────────────────────────────────────────────────────────────────

_STRIP_PATTERNS = [
    r"^\d+[\s._-]+",                       # 앞의 트랙 번호
    r"\b(mp3|vbr|128kbps|64kbps)\b",
    r"\bUnited States (Air Force|Marine|Navy) Band\b",
    r"\b(Air Force Strings|Strolling Strings|Concert Band|Ceremonial Brass)\b",
    r"\(\d{4}\)",                          # 연도
]


def clean_title(raw: str, *, segment: str = "auto") -> str:
    """파일명에서 실제 곡목만 뽑아낸다.

    두 출처의 이름 규칙이 정반대다:
      archive.org : "Kimiko Ishizaka - Bach- WTC Book 1 - 11 Prelude No.6 ..."  → **마지막** 조각
      Commons     : "\"A Winter Prelude\" - Concert Band - US Air Force Band"   → **첫** 조각
    그래서 출처별로 어느 조각을 쓸지 지정한다. 잘못 고르면 연주자·앨범명이 제목이 되고,
    거기에 부분 한국어화가 겹쳐 "바흐- Well-Tempered Clavier" 같은 잡탕이 나온다.
    """
    t = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", raw)      # 확장자
    t = t.replace("_", " ").strip()

    if segment in ("first", "last"):
        parts = [x.strip() for x in re.split(r"\s+[-–—]\s+", t) if x.strip()]
        if len(parts) >= 2:
            t = parts[-1] if segment == "last" else parts[0]

    # 따옴표는 조각을 나눌 때 한쪽만 남는 일이 많다 ("Brindisi" -> Brindisi") — 전부 제거
    t = t.replace('"', "").replace("“", "").replace("”", "")
    # 뒤에 붙는 연주자 설명 제거 (", played by the U.S. Naval Academy Band")
    t = re.sub(r",?\s*(played|performed|arranged|conducted)\s+by\s+.*$", "", t, flags=re.I)

    for pat in _STRIP_PATTERNS:
        t = re.sub(pat, " ", t, flags=re.I)
    t = re.sub(r"\s*[-–—]\s*[-–—]\s*", " - ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–—,.'")
    return t or raw


# 자주 나오는 작곡가·작품명만 한국어로 바꾼다. 나머지는 원제를 그대로 쓴다
# (기계 번역으로 엉뚱한 제목을 만드는 것보다 원제가 낫다).
KO_TERMS = [
    (r"\bBach\b", "바흐"), (r"\bHandel\b", "헨델"), (r"\bVivaldi\b", "비발디"),
    (r"\bMozart\b", "모차르트"), (r"\bBeethoven\b", "베토벤"), (r"\bChopin\b", "쇼팽"),
    (r"\bGrieg\b", "그리그"), (r"\bStrauss\b", "슈트라우스"), (r"\bRossini\b", "로시니"),
    (r"\bBizet\b", "비제"), (r"\bSousa\b", "수자"), (r"\bTchaikovsky\b", "차이콥스키"),
    (r"\bPrelude\b", "전주곡"), (r"\bFugue\b", "푸가"), (r"\bNocturne\b", "야상곡"),
    (r"\bVariation\b", "변주곡"), (r"\bAria\b", "아리아"), (r"\bWaltz\b", "왈츠"),
    (r"\bMarch\b", "행진곡"), (r"\bOverture\b", "서곡"), (r"\bSuite\b", "모음곡"),
    (r"\bConcerto\b", "협주곡"), (r"\bSonata\b", "소나타"), (r"\bAdagio\b", "아다지오"),
    (r"\bLargo\b", "라르고"), (r"\bAndante\b", "안단테"), (r"\bAllegro\b", "알레그로"),
    (r"\bin ([A-G])(\s?(sharp|flat))? major\b", r"\1장조"),
    (r"\bin ([A-G])(\s?(sharp|flat))? minor\b", r"\1단조"),
]


def to_korean(title: str) -> str:
    t = title
    for pat, rep in KO_TERMS:
        t = re.sub(pat, rep, t, flags=re.I)
    # 치환 뒤 남는 영어 소유격 정리 (Grieg's -> 그리그의).
    # ★ 캡처한 한글 글자를 \g<1> 로 반드시 되돌려 놓아야 한다 — 빠뜨리면 작곡가 이름이 통째로 사라진다.
    t = re.sub(r"([가-힣])['’]s\b", r"\g<1>의", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def parse_length(raw) -> int | None:
    """archive.org 의 length 는 '253.4' 또는 '4:13' 두 형태로 온다."""
    if raw is None:
        return None
    s = str(raw)
    if ":" in s:
        try:
            parts = [float(x) for x in s.split(":")]
        except ValueError:
            return None
        total = 0.0
        for p in parts:
            total = total * 60 + p
        return int(total)
    try:
        return int(float(s))
    except ValueError:
        return None


# ── 한국어 → 검색어 (역방향) ─────────────────────────────────────────────────

_SIMPLE_TERM = re.compile(r"^\\b([A-Za-z][A-Za-z '\-]*)\\b$")


def _invert_ko_terms() -> dict[str, str]:
    """KO_TERMS 중 되집을 수 있는 항목만 자동 반전한다.

    `(r"\\bBach\\b", "바흐")` 는 반전 가능하지만
    `(r"\\bin ([A-G])... major\\b", r"\\1장조")` 는 캡처 역참조가 있어 불가능하다.
    → 패턴이 정확히 `\\b<리터럴>\\b` 이고 치환문에 역참조가 없는 항목만 채택한다.

    손으로 두 번 쓰지 않으므로, 나중에 KO_TERMS 에 작곡가를 추가하면
    검색 사전이 자동으로 따라온다.
    """
    out: dict[str, str] = {}
    for pattern, repl in KO_TERMS:
        if "\\1" in repl or "\\g<" in repl:
            continue                     # 역참조가 있으면 되집을 수 없다
        m = _SIMPLE_TERM.match(pattern)
        if not m:
            continue
        out[repl] = m.group(1)
    return out


KO_TO_EN: dict[str, str] = _invert_ko_terms()

# 검색에만 필요한 보충어. ★ 제목 한국어화(to_korean)에는 쓰지 않는다 —
# 여기 있는 말들은 곡목이 아니라 "찾을 때 쓰는 말" 이다.
KO_SEARCH_EXTRA: dict[str, str] = {
    "클래식": "classical", "고전음악": "classical", "클래식음악": "classical",
    "피아노": "piano", "첼로": "cello", "바이올린": "violin", "비올라": "viola",
    "오르간": "organ", "하프시코드": "harpsichord", "쳄발로": "harpsichord",
    "플루트": "flute", "오보에": "oboe", "클라리넷": "clarinet", "기타": "guitar",
    "바로크": "baroque", "낭만": "romantic", "고전주의": "classical",
    "교향곡": "symphony", "실내악": "chamber music", "현악사중주": "string quartet",
    "사중주": "quartet", "삼중주": "trio", "이중주": "duet",
    "즉흥곡": "impromptu", "환상곡": "fantasia", "마주르카": "mazurka",
    "발라드": "ballade", "폴로네즈": "polonaise", "에튀드": "etude", "연습곡": "etude",
    "칸타타": "cantata", "미사": "mass", "레퀴엠": "requiem", "오라토리오": "oratorio",
    "군악대": "military band", "행진": "march", "합주": "ensemble",
    "기악": "instrumental", "합창": "choir", "성악": "vocal",
    "모음곡": "suite", "서곡": "overture", "야상곡": "nocturne",
    # KO_TERMS 에 없는 작곡가 보충
    "슈베르트": "Schubert", "브람스": "Brahms", "리스트": "Liszt",
    "드뷔시": "Debussy", "라벨": "Ravel", "사티": "Satie",
    "슈만": "Schumann", "멘델스존": "Mendelssohn", "하이든": "Haydn",
    "파헬벨": "Pachelbel", "알비노니": "Albinoni", "코렐리": "Corelli",
    "텔레만": "Telemann", "퍼셀": "Purcell", "스카를라티": "Scarlatti",
    "드보르작": "Dvorak", "시벨리우스": "Sibelius", "엘가": "Elgar",
}


def ko_lookup() -> dict[str, str]:
    """검색용 한국어 사전 (자동 반전 + 보충어). 긴 키가 먼저 오도록 정렬해 돌려준다.

    긴 키 우선이 중요하다 — '현악사중주' 가 '사중주' 보다 먼저 매칭돼야 한다.
    """
    merged = {**KO_TO_EN, **KO_SEARCH_EXTRA}
    return dict(sorted(merged.items(), key=lambda kv: -len(kv[0])))


# ── 라이선스 판정 ────────────────────────────────────────────────────────────

LICENSE_BADGE_KO = {
    "pd": "퍼블릭 도메인",
    "by": "CC BY",
    "by-sa": "CC BY-SA",
    "blocked": "사용 불가 (NC/ND)",
    "unknown": "라이선스 미확인",
}
LICENSE_ADDABLE = {"pd", "by", "by-sa"}
LICENSE_NEEDS_ATTR = {"by", "by-sa"}

# ★ NC/ND 를 **먼저** 본다. 'by-nc-sa' 가 'by-sa' 매칭에 걸리면
#   상업적 이용 금지 음원이 통과해 버린다.
_NC_ND_PATH = re.compile(r"/(by-nc-nd|by-nc-sa|by-nc|by-nd|nc-sampling)", re.I)
_BY_SA_PATH = re.compile(r"/licenses/by-sa/", re.I)
_BY_PATH = re.compile(r"/licenses/by/", re.I)
_PD_PATH = re.compile(r"/(publicdomain|licenses/publicdomain)/", re.I)


def license_kind(raw: str | None) -> str:
    """'pd' | 'by' | 'by-sa' | 'blocked' | 'unknown'.

    archive.org 의 licenseurl 은 URL 이고 Commons 의 LicenseShortName 은
    'Public domain' 같은 문자열이다. 둘 다 받아들인다.
    """
    if not raw:
        return "unknown"
    s = str(raw).strip()
    if not s:
        return "unknown"

    if _NC_ND_PATH.search(s):
        return "blocked"

    low = s.lower()
    if low.startswith("http"):
        if _BY_SA_PATH.search(s):
            return "by-sa"
        if _BY_PATH.search(s):
            return "by"
        if _PD_PATH.search(s) or "/zero/" in low or "/mark/" in low:
            return "pd"
        return "unknown"

    # URL 이 아닌 표기 (Commons 의 LicenseShortName / UsageTerms)
    if re.search(r"\b(nc|noncommercial|non-commercial|nd|noderiv|no derivative)\b", low):
        return "blocked"
    if re.search(r"by[-\s]?sa", low):
        return "by-sa"
    if re.search(r"\bcc[-\s]?by\b", low) or "attribution" in low:
        return "by"
    if ("public domain" in low or "publicdomain" in low
            or "cc0" in low or "pdm" in low or low == "pd"):
        return "pd"
    return "unknown"


def license_badge_ko(raw: str | None) -> str:
    return LICENSE_BADGE_KO[license_kind(raw)]


def requires_attribution(raw: str | None) -> bool:
    """저작자 표시 의무가 있는가 (CC BY / CC BY-SA).

    ★ 예전 catalog.credits() 는 `lic.upper().startswith("CC-BY")` 로 판정했는데
      실제 값은 전부 URL 이라 **항상 False** 였다. 지금은 모든 소스가 PD/CC0 라
      우연히 맞지만, CC BY-SA 음원이 하나라도 들어오면 표시 의무가 조용히 누락된다.
    """
    return license_kind(raw) in LICENSE_NEEDS_ATTR
