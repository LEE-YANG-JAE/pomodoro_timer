"""archive.org 음원 검색 (런타임).

카탈로그 생성기(`qa/build_catalog.py`)와 같은 API 를 쓰지만, 이쪽은 사용자가 그때그때
검색해서 곡을 골라 담는 용도다. 받은 곡은 `data/tracks.json` 에 사용자 트랙으로 들어가므로
카탈로그를 다시 생성해도 살아남는다.

★ 실측으로 확인한 함정들 — 하나라도 빠뜨리면 조용히 망가진다:
  1. `licenseurl:(publicdomain)` 은 **0건**이다. 와일드카드가 필수.
  2. 슬래시를 이스케이프하지 않으면 쿼리 전체가 깨지고 응답에 `response` 키가 아예 없다.
  3. LibriVox 오디오북은 라이선스가 깨끗하고 다운로드가 많아, 작곡가 쿼리에서 실제 연주를
     전부 밀어낸다. `-collection:librivoxaudio` 는 선택이 아니라 필수.
  4. stream_only / samples_only / loggedin 항목은 메타데이터에 VBR MP3 를 버젓이 나열하지만
     실제로 받으면 404 다. 단서는 `MP3 Sample` 포맷이 함께 있는 것.
  5. `/metadata/<id>` 는 다크 처리된 항목에 **HTTP 200 + `{}`** 를 준다. 404 도 오류도 아니다.
  6. 다운로드 URL 은 `files[].name` 을 그대로 써야 한다. 표시 제목으로 만들면 404.
  7. 항목 단위 용량 필터는 틀렸다 — 4GB/744파일짜리 앨범에 쓸 만한 MP3 가 104개 있다.
  8. 검색 결과의 `licenseurl` 은 자주 비어 있다(100건 중 5건). 라이선스 게이트는
     **추가 시점에 메타데이터로** 건다.
"""
from __future__ import annotations

import dataclasses
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from typing import Any

from . import config, terms

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/"
SEARCH_TIMEOUT = 12
META_TIMEOUT = 20
MIN_INTERVAL = 1.0            # archive.org 검색은 ~1 req/s 가 편안하다
CACHE_TTL = 600.0
CACHE_MAX = 64
MAX_ROWS = 30
MAX_PAGE = 20
MAX_ADD_FILES = 100
RESULT_CAP = 10_000           # archive.org 가 잘라 내는 지점

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/])')

_lock = threading.RLock()
_last_call = [0.0]
_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()


class SearchError(Exception):
    def __init__(self, status: int, message_ko: str):
        super().__init__(message_ko)
        self.status = status
        self.message_ko = message_ko


# ── HTTP (테스트가 갈아끼우는 유일한 지점) ─────────────────────────────────

def _http_json(url: str, *, timeout: int) -> Any:
    """★ 테스트는 이 함수만 monkeypatch 하면 네트워크를 완전히 차단할 수 있다.

    HTTP 200 이면 본문을 **그대로** 돌려준다 — 해석은 호출측 책임이다.
    함정 #2 와 #5 는 둘 다 "200 인데 본문이 이상한" 경우라, 여기서 판단하면 안 된다.
    """
    with _lock:
        gap = time.monotonic() - _last_call[0]
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        _last_call[0] = time.monotonic()

    req = urllib.request.Request(url, headers={
        "User-Agent": config.USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise SearchError(429, "요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요.")
        raise SearchError(502, f"archive.org 응답 오류 ({e.code}). 잠시 후 다시 시도해 주세요.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SearchError(504, "archive.org 에 연결하지 못했습니다. 네트워크를 확인해 주세요.")
    except json.JSONDecodeError:
        raise SearchError(502, "archive.org 응답을 해석할 수 없습니다.")


# ── 쿼리 빌더 ───────────────────────────────────────────────────────────────

def escape_lucene(term: str) -> str:
    """Lucene 특수문자를 이스케이프한다. 사용자 입력이 쿼리 구문을 깨뜨리지 못하게."""
    return _LUCENE_SPECIAL.sub(r"\\\1", term)


# ★ 슬래시를 반드시 이스케이프한다 (함정 #2).
#   licenseurl:(publicdomain) 은 0건이므로 와일드카드가 필수다 (함정 #1).
_LICENSE_CLAUSE = (
    r"(licenseurl:*publicdomain* OR licenseurl:*licenses\/by\/* "
    r"OR licenseurl:*licenses\/by-sa\/*)"
)

# ★ librivoxaudio 는 필수다 — 오디오북이 실제 연주를 밀어낸다 (함정 #3).
_EXCLUDE_COLLECTIONS = ("stream_only", "samples_only", "loggedin",
                        "librivoxaudio", "podcasts", "podcasts_mirror")


def build_query(terms_list: list[str], *, join: str = "AND", extra: str = "",
                field: str = "title") -> str:
    """완성된 Lucene 쿼리."""
    safe = [escape_lucene(t) for t in terms_list if t.strip()]
    if safe:
        inner = f" {join} ".join(safe)
        head = f"{field}:({inner})"
    else:
        head = "mediatype:(audio)"
    parts = [head]
    if safe:
        parts.append("mediatype:(audio)")
    parts.append(_LICENSE_CLAUSE)
    parts += [f"-collection:{c}" for c in _EXCLUDE_COLLECTIONS]
    q = " AND ".join(parts)
    return f"{q} {extra}".strip() if extra else q


# ── 한국어 → 검색어 ─────────────────────────────────────────────────────────

@dataclasses.dataclass
class QueryRewrite:
    raw: str
    terms: list[str]
    replaced: list[tuple[str, str]]
    dropped: list[str]
    all_korean_dropped: bool


_HANGUL = re.compile(r"[가-힣]")


def rewrite_query(raw: str) -> QueryRewrite:
    """한국어 질의를 영어 검색어로 바꾼다.

    ★ archive.org 는 한국어 검색이 사실상 동작하지 않는다 — '클래식' 은 모스부호·
      에스페란토 발음 파일을, '피아노' 는 K-pop 커버를 낸다. 그래서 조용히 0건을 내는
      대신 **바꿔서 검색하고 무엇으로 바꿨는지 알린다.**
    """
    lookup = terms.ko_lookup()           # 긴 키 우선 정렬돼 있다
    tokens = [t for t in re.split(r"\s+", (raw or "").strip()) if t][:6]

    out_terms: list[str] = []
    replaced: list[tuple[str, str]] = []
    dropped: list[str] = []

    for tok in tokens:
        work = tok
        for ko, en in lookup.items():
            if ko in work:
                work = work.replace(ko, f" {en} ")
                replaced.append((ko, en))
        pieces = [p for p in re.split(r"\s+", work) if p]
        for p in pieces:
            if _HANGUL.search(p):
                dropped.append(p)        # 대응어를 못 찾은 한국어는 버린다
            else:
                out_terms.append(p)

    had_korean = bool(_HANGUL.search(raw or ""))
    return QueryRewrite(
        raw=raw or "",
        terms=out_terms,
        replaced=replaced,
        dropped=dropped,
        all_korean_dropped=had_korean and not out_terms,
    )


# ── 검색 ────────────────────────────────────────────────────────────────────

_FIELDS = ("identifier", "title", "creator", "licenseurl",
           "downloads", "item_size", "files_count", "publicdate")


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _size_ko(n: int) -> str:
    n = int(n or 0)
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f}GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f}MB"
    return f"{n / 1024:.0f}KB"


def _normalize_doc(doc: dict) -> dict:
    lic = doc.get("licenseurl")
    kind = terms.license_kind(lic)
    ident = doc.get("identifier", "")
    return {
        "identifier": ident,
        "title": str(doc.get("title") or ident)[:200],
        "creator": _first(doc.get("creator")),
        "license": lic,
        "license_kind": kind,
        "license_badge_ko": terms.LICENSE_BADGE_KO[kind],
        "downloads": int(doc.get("downloads") or 0),
        "item_size": int(doc.get("item_size") or 0),
        "size_text_ko": _size_ko(doc.get("item_size") or 0),
        # ⚠ files_count 는 **트랙 수가 아니다** — 4GB/744파일 앨범에 쓸 만한 MP3 가 104개다.
        "files_count": int(doc.get("files_count") or 0),
        "details_url": f"https://archive.org/details/{ident}",
    }


def _notice(rw: QueryRewrite, total: int, shown: int, *, relaxed: bool) -> str:
    if rw.all_korean_dropped:
        return "archive.org 는 한국어 검색을 지원하지 않습니다. 아래 추천 검색을 눌러 보세요."
    eff = " ".join(rw.terms)
    if rw.replaced:
        head = f"'{rw.raw}' → {eff} 으로 검색했습니다."
    else:
        head = f"{eff} 으로 검색했습니다."
    bits = [head, f"전체 {total}건 중 {shown}건 표시."]
    if rw.dropped:
        # 조사 대신 대시를 쓴다 — 한글 종성 판정 헬퍼를 만들 값어치가 없다
        bits.append(f"'{' '.join(rw.dropped)}' — 대응하는 영어 검색어가 없어 제외했습니다.")
    if relaxed:
        bits.append("(정확히 일치하는 결과가 없어 조건을 완화했습니다.)")
    return " ".join(bits)


def _run_search(lucene: str, page: int, rows: int) -> dict:
    params = [("q", lucene), ("rows", rows), ("page", page),
              ("output", "json"), ("sort[]", "downloads desc")]
    params += [("fl[]", f) for f in _FIELDS]
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params, safe="[]")
    body = _http_json(url, timeout=SEARCH_TIMEOUT)

    resp = body.get("response") if isinstance(body, dict) else None
    if not isinstance(resp, dict) or not isinstance(resp.get("docs"), list):
        # ★ 쿼리가 깨지면 archive.org 는 response 키가 아예 없는 본문을 200 으로 낸다.
        #   body["response"] 를 바로 쓰면 KeyError 로 500 이 나고 원인을 알 수 없다.
        raise SearchError(502, "archive.org 검색 응답을 해석할 수 없습니다. 잠시 후 다시 시도해 주세요.")
    return resp


def search_items(raw_query: str, *, page: int = 1, rows: int = 20,
                 preset: str | None = None) -> dict:
    page = max(1, min(MAX_PAGE, int(page)))
    rows = max(1, min(MAX_ROWS, int(rows)))

    extra = ""
    field = "title"
    if preset:
        spec = next((p for p in PRESETS if p["id"] == preset), None)
        if spec is None:
            raise SearchError(400, "알 수 없는 추천 검색입니다.")
        rw = QueryRewrite(raw=spec["label_ko"], terms=list(spec["terms"]),
                          replaced=[], dropped=[], all_korean_dropped=False)
        extra = spec.get("extra", "")
        field = spec.get("field", "title")
    else:
        q = (raw_query or "").strip()
        if not q:
            raise SearchError(400, "검색어를 입력해 주세요.")
        if len(q) > 80:
            raise SearchError(400, "검색어가 너무 깁니다 (최대 80자).")
        rw = rewrite_query(q)
        if rw.all_korean_dropped:
            # ★ 네트워크를 아예 타지 않는다 — 어차피 쓸모없는 결과만 나온다
            return {"query": {"raw": rw.raw, "effective": "", "replaced": [],
                              "dropped": rw.dropped, "relaxed": False, "lucene": "",
                              "notice_ko": _notice(rw, 0, 0, relaxed=False)},
                    "total": 0, "page": 1, "rows": rows, "has_more": False,
                    "cached": False, "items": []}

    key = (tuple(rw.terms), page, rows, extra, field)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            _cache.move_to_end(key)
            return {**hit[1], "cached": True}

    lucene = build_query(rw.terms, extra=extra, field=field)
    resp = _run_search(lucene, page, rows)
    relaxed = False

    # 결과가 없고 토큰이 여럿이면 OR 로 한 번만 완화한다
    if int(resp.get("numFound", 0)) == 0 and len(rw.terms) > 1:
        lucene = build_query(rw.terms, join="OR", extra=extra, field=field)
        resp = _run_search(lucene, page, rows)
        relaxed = True

    total = int(resp.get("numFound", 0))
    items = [_normalize_doc(d) for d in resp["docs"]]
    result = {
        "query": {
            "raw": rw.raw, "effective": " ".join(rw.terms),
            "replaced": [list(p) for p in rw.replaced], "dropped": rw.dropped,
            "relaxed": relaxed, "lucene": lucene,
            "notice_ko": _notice(rw, total, len(items), relaxed=relaxed),
        },
        "total": total, "page": page, "rows": rows,
        "has_more": page * rows < min(total, RESULT_CAP),
        "cached": False, "items": items,
    }
    with _lock:
        _cache[key] = (now, result)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return result


# ── 항목 상세 ───────────────────────────────────────────────────────────────

def fetch_metadata(identifier: str) -> dict:
    """★ 다크/미존재 항목은 **{} 를 HTTP 200 으로** 돌려준다 (함정 #5).
    404 도 오류도 아니라서 여기서 명시적으로 잡지 않으면 트랙 0개로 조용히 흘러간다."""
    if not _IDENTIFIER_RE.match(identifier or ""):
        raise SearchError(400, "잘못된 항목 식별자입니다.")
    meta = _http_json(METADATA_URL + urllib.parse.quote(identifier, safe=""),
                      timeout=META_TIMEOUT)
    if not isinstance(meta, dict) or not meta.get("files"):
        raise SearchError(
            404, "이 항목은 archive.org 에서 더 이상 제공되지 않습니다 "
                 "(비공개 처리되었거나 삭제되었습니다).")
    return meta


def _as_list(v) -> list[str]:
    """collection 은 str 일 수도 list 일 수도 있다 (IA 의 오래된 스키마 흔적)."""
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def restriction_reason(md: dict, files: list[dict]) -> str | None:
    """다운로드가 404 로 끝날 항목을 미리 걸러낸다 (함정 #4)."""
    meta = md.get("metadata", {}) or {}
    collections = {c.lower() for c in _as_list(meta.get("collection"))}

    # access-restricted-item 은 불리언이 아니라 문자열 "true" 로 온다
    restricted = str(meta.get("access-restricted-item", "")).lower() == "true"
    if restricted or "loggedin" in collections:
        return "이 항목은 archive.org 로그인이 필요해 내려받을 수 없습니다."
    if collections & {"stream_only", "samples_only"}:
        return "이 항목은 미리듣기(샘플)만 제공되어 내려받을 수 없습니다."
    # 결정적 단서: VBR MP3 옆에 MP3 Sample 이 있다
    formats = {str(f.get("format", "")) for f in files}
    if "MP3 Sample" in formats:
        return "이 항목은 미리듣기(샘플)만 제공되어 내려받을 수 없습니다."
    return None


_MIN_BPS, _MAX_BPS = 24_000, 400_000


def _stem(name: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name or "")


def _resolve_duration(f: dict, siblings: list[dict]) -> tuple[int | None, bool]:
    """(초, 의심여부).

    ★ 같은 원본의 파생본끼리 length 가 어긋나는 경우가 실제로 있다 — 4.4MB 파일에
      18:10 이 붙어 있었다(약 32kbps, 산술적으로 불가능). 형제 파일의 중앙값과
      비트레이트로 교차검증한다.
    """
    own = terms.parse_length(f.get("length"))
    cand = [terms.parse_length(s.get("length")) for s in siblings]
    cand = sorted(x for x in cand if x and x > 0)
    median = cand[len(cand) // 2] if cand else None

    value, suspect = own, False
    if median and own and abs(own - median) > median * 0.10:
        value, suspect = median, True
    if value is None:
        value = median
        suspect = suspect or median is not None

    size = int(f.get("size", 0) or 0)
    if value and size:
        bps = size * 8 / value
        if not (_MIN_BPS <= bps <= _MAX_BPS):
            return None, True          # 말이 안 되는 값은 없다고 한다
    return value, suspect


def resolve_tracks(identifier: str, meta: dict) -> dict:
    """항목 메타데이터 → 실제로 받을 수 있는 MP3 목록.

    ★ 아이템 단위가 아니라 **파일 단위**로 거른다 (함정 #7).
    ★ URL 은 files[].name 을 그대로 quote 해서 만든다 — 표시 제목이면 404 다 (함정 #6).
    """
    files = meta.get("files", []) or []
    md = meta.get("metadata", {}) or {}
    lic = md.get("licenseurl") or md.get("rights")
    kind = terms.license_kind(lic)

    skipped = {"format": 0, "zero_size": 0, "oversized": 0}
    usable: list[dict] = []

    # 형제 그룹 — original 필드 또는 확장자 뗀 이름으로 묶는다
    groups: dict[str, list[dict]] = {}
    for f in files:
        key = str(f.get("original") or _stem(f.get("name", "")))
        groups.setdefault(key, []).append(f)

    for f in sorted(files, key=lambda x: str(x.get("name", ""))):
        name = str(f.get("name", ""))
        if f.get("format") not in terms.IA_FORMATS or not name.lower().endswith(".mp3"):
            skipped["format"] += 1
            continue
        size = int(f.get("size", 0) or 0)
        if size <= 0:
            skipped["zero_size"] += 1
            continue
        from . import input_limits
        if size > input_limits.MAX_AUDIO_BYTES:
            skipped["oversized"] += 1
            continue

        sibs = groups.get(str(f.get("original") or _stem(name)), [])
        dur, suspect = _resolve_duration(f, sibs)
        title_orig = terms.clean_title(name, segment="last")
        usable.append({
            "name": name,
            "title_orig": title_orig,
            "title_ko": terms.to_korean(title_orig),
            "format": f.get("format"),
            "bytes": size,
            "size_text_ko": _size_ko(size),
            "duration_seconds": None if suspect else dur,
            "duration_text": _dur_text(dur),
            "duration_suspect": suspect,
            "sha1": (f.get("sha1") or "").lower() or None,
            "url": (f"https://archive.org/download/{urllib.parse.quote(identifier)}/"
                    f"{urllib.parse.quote(name, safe='')}"),
        })

    reason = restriction_reason(meta, files)
    if reason is None and kind not in terms.LICENSE_ADDABLE:
        reason = ("상업적 이용 금지·변경 금지 라이선스라 사용할 수 없습니다."
                  if kind == "blocked" else "라이선스가 확인되지 않아 추가할 수 없습니다.")
    if reason is None and not usable:
        reason = "내려받을 수 있는 MP3 가 없습니다."

    return {
        "identifier": identifier,
        "title": str(md.get("title") or identifier)[:200],
        "creator": _first(md.get("creator")),
        "details_url": f"https://archive.org/details/{identifier}",
        "license": lic,
        "license_kind": kind,
        "license_badge_ko": terms.LICENSE_BADGE_KO[kind],
        "addable": reason is None,
        "reason_ko": reason,
        "total_files": len(files),
        "usable_count": len(usable),
        "skipped": skipped,
        "tracks": usable,
    }


def _dur_text(sec: int | None) -> str | None:
    if not sec:
        return None
    return f"{sec // 60}:{sec % 60:02d}"


# ── 추천 검색 ───────────────────────────────────────────────────────────────

PRESETS: list[dict] = [
    {"id": "piano_focus", "label_ko": "집중용 피아노",
     "hint_ko": "Musopen 컬렉션의 솔로 피아노",
     "terms": ["piano"], "extra": "AND collection:(musopen)"},
    {"id": "bach", "label_ko": "바흐", "hint_ko": "평균율·골드베르크·푸가의 기법",
     "terms": ["bach"], "extra": ""},
    {"id": "chopin", "label_ko": "쇼팽", "hint_ko": "야상곡·전주곡·왈츠",
     "terms": ["chopin"], "extra": ""},
    {"id": "baroque", "label_ko": "바로크", "hint_ko": "느린 악장 위주",
     "terms": ["baroque"], "extra": ""},
    {"id": "chamber", "label_ko": "실내악", "hint_ko": "사중주·삼중주",
     "terms": ["quartet"], "extra": ""},
    {"id": "band", "label_ko": "군악대", "hint_ko": "미 정부 저작물 — 완전 퍼블릭 도메인",
     "terms": ["marine band"], "extra": "", "field": "creator"},
]


def presets() -> list[dict]:
    return [{k: v for k, v in p.items() if k != "extra"} for p in PRESETS]
