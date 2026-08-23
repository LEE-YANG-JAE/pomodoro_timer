"""media_catalog.json 로드 + 검증.

카탈로그는 **손으로 쓰지 않는다.** `qa/build_catalog.py` 가 archive.org metadata API 와
Wikimedia Commons MediaWiki API 를 실제로 조회해 생성한다.

이 방침의 근거: 조사 단계에서 그럴듯해 보이는 archive.org 식별자 6개를 확인했더니
3개가 제목만으로는 알 수 없는 이유로 사용 불가였다(MP3 0개 / 4.5GB 단일 ZIP /
마이크 원본 스템). 또 archive.org 의 라이선스 메타데이터는 사용자 입력이라 누락이 흔하다
— Musopen 컬렉션 34개 중 28개가 licenseurl 자체가 없었다.
따라서 런타임은 생성된 매니페스트만 신뢰하고 URL 을 절대 추측하지 않는다.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config, storage, terms

CATALOG_VERSION = 1

_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.(mp3|m4a|flac|wav)$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")

VALID_TIERS = {"core", "extra"}
VALID_PLAYLISTS = {"focus", "break"}

_cache: dict | None = None
_cache_mtime: float | None = None

_EMPTY: dict = {"version": CATALOG_VERSION, "sources": [], "tracks": []}


def _path() -> Path:
    return config.CATALOG_FILE


def load() -> dict:
    """카탈로그를 읽는다. 파일이 없으면 빈 카탈로그 —
    ★ 음원이 하나도 없어도 타이머는 완전히 동작해야 하므로 절대 raise 하지 않는다."""
    global _cache, _cache_mtime
    p = _path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return dict(_EMPTY)
    if _cache is not None and _cache_mtime == mtime:
        return _cache
    doc = storage.read_json(p, default=None)
    if not isinstance(doc, dict) or "tracks" not in doc:
        doc = dict(_EMPTY)
    doc.setdefault("sources", [])
    doc.setdefault("tracks", [])
    _cache, _cache_mtime = doc, mtime
    return doc


def sources() -> list[dict]:
    return load().get("sources", [])


def tracks() -> list[dict]:
    return load().get("tracks", [])


def track(track_id: str) -> dict | None:
    for t in tracks():
        if t.get("id") == track_id:
            return t
    return None


def tracks_by_tier(tier: str | None = None) -> list[dict]:
    if tier in (None, "all"):
        return tracks()
    return [t for t in tracks() if t.get("tier") == tier]


def source(source_id: str) -> dict | None:
    for s in sources():
        if s.get("source_id") == source_id:
            return s
    return None


def credits() -> list[dict]:
    """크레딧 화면용.

    CC BY / CC BY-SA 는 저작자 표시가 **의무**다. CC0/PD 는 의무가 없지만 연주자를
    밝히는 게 예의이므로 requires_attribution 플래그로 구분해 함께 돌려준다.

    ★ 예전 구현은 `lic.upper().startswith("CC-BY")` 로 판정했는데, 카탈로그의 license
      값은 전부 URL(`http://creativecommons.org/publicdomain/mark/1.0/`)이라
      **모든 소스에서 항상 False** 였다. 지금은 전부 PD/CC0 라 우연히 맞았을 뿐이고,
      CC BY-SA 음원이 하나라도 들어오는 순간 표시 의무가 조용히 누락됐을 것이다.
      terms.license_kind() 로 URL·문자열 표기를 모두 정확히 판정한다.
    """
    out = []
    for s in sources():
        lic = s.get("license")
        kind = terms.license_kind(lic)
        out.append({
            "source_id": s.get("source_id"),
            "album_ko": s.get("album_ko") or s.get("album_orig"),
            "performer_ko": s.get("performer_ko") or s.get("performer"),
            "license": lic,
            "license_url": s.get("license_url"),
            "license_kind": kind,
            "license_badge_ko": terms.LICENSE_BADGE_KO[kind],
            "details_url": s.get("details_url"),
            "attribution_ko": s.get("attribution_ko"),
            "requires_attribution": kind in terms.LICENSE_NEEDS_ATTR,
            "origin": "catalog",
            "track_count": sum(1 for t in tracks() if t.get("source_id") == s.get("source_id")),
        })
    return out


def validate(doc: dict | None = None) -> list[str]:
    """구조 검증. 문제 목록을 돌려준다 (빈 리스트면 정상)."""
    doc = doc if doc is not None else load()
    problems: list[str] = []

    source_ids = set()
    for i, s in enumerate(doc.get("sources", [])):
        sid = s.get("source_id")
        if not sid:
            problems.append(f"sources[{i}]: source_id 없음")
            continue
        if sid in source_ids:
            problems.append(f"sources[{i}]: source_id 중복 {sid}")
        source_ids.add(sid)
        # 라이선스가 확인되지 않은 소스는 애초에 생성기가 거부해야 한다
        if not s.get("license"):
            problems.append(f"sources[{i}] {sid}: license 없음 (생성기가 거부했어야 함)")

    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    for i, t in enumerate(doc.get("tracks", [])):
        tid = t.get("id", "")
        where = f"tracks[{i}] {tid or '<id 없음>'}"
        if not _ID_RE.match(tid):
            problems.append(f"{where}: id 형식 오류")
        if tid in seen_ids:
            problems.append(f"{where}: id 중복")
        seen_ids.add(tid)

        fn = t.get("filename", "")
        if not _FILENAME_RE.match(fn):
            problems.append(f"{where}: filename 형식 오류 ({fn!r}) — mp3/m4a/flac/wav 만")
        if fn in seen_files:
            problems.append(f"{where}: filename 중복 ({fn})")
        seen_files.add(fn)

        if t.get("source_id") not in source_ids:
            problems.append(f"{where}: source_id 미해결 ({t.get('source_id')})")
        if not isinstance(t.get("bytes"), int) or t.get("bytes", 0) <= 0:
            problems.append(f"{where}: bytes 가 양의 정수가 아님")
        sha1 = t.get("sha1")
        if sha1 is not None and not _SHA1_RE.match(str(sha1)):
            problems.append(f"{where}: sha1 형식 오류")
        if t.get("tier") not in VALID_TIERS:
            problems.append(f"{where}: tier 는 {VALID_TIERS} 중 하나여야 함")
        pls = t.get("playlists")
        if not isinstance(pls, list) or not set(pls) <= VALID_PLAYLISTS:
            problems.append(f"{where}: playlists 는 {VALID_PLAYLISTS} 의 부분집합이어야 함")
        if not t.get("url"):
            problems.append(f"{where}: url 없음")

    return problems
