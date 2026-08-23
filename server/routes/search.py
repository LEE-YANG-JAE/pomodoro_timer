"""archive.org 음원 검색 API.

★ 추가(POST /add)는 클라이언트가 보낸 url·bytes·sha1 을 **절대 받지 않는다.**
  identifier 와 파일 이름만 받고 메타데이터를 다시 가져와 서버가 모든 값을 파생한다.
  이유가 셋이다:
    ① 검색 시점의 licenseurl 은 신뢰할 수 없다 (100건 중 5건만 존재)
    ② 검색과 추가 사이에 항목이 다크 처리될 수 있다
    ③ names 가 방금 받은 파일 목록에 있을 때만 통과하므로 임의 URL 주입이 불가능하다
"""
from __future__ import annotations

import datetime as dt
import os
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import config, input_limits, media, playlists, search, terms

router = APIRouter(prefix="/api/media/search", tags=["search"])


class AddIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifier: str = Field(..., min_length=1, max_length=100)
    names: list[str] = Field(..., min_length=1, max_length=search.MAX_ADD_FILES)
    playlists: list[Literal["focus", "break"]] = Field(default_factory=list)
    download: bool = True


def _bail(e: search.SearchError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.message_ko)


@router.get("/presets")
def get_presets() -> list[dict]:
    return search.presets()


@router.get("")
def do_search(
    q: str = Query("", max_length=80),
    page: int = Query(1, ge=1, le=search.MAX_PAGE),
    rows: int = Query(20, ge=1, le=search.MAX_ROWS),
    preset: str | None = Query(None, max_length=40),
) -> dict:
    try:
        return search.search_items(q, page=page, rows=rows, preset=preset)
    except search.SearchError as e:
        raise _bail(e)


@router.get("/item/{identifier}")
def get_item(identifier: str) -> dict:
    try:
        meta = search.fetch_metadata(identifier)
        return search.resolve_tracks(identifier, meta)
    except search.SearchError as e:
        raise _bail(e)


@router.post("/add")
def add_tracks(body: AddIn) -> dict:
    try:
        meta = search.fetch_metadata(body.identifier)
        detail = search.resolve_tracks(body.identifier, meta)
    except search.SearchError as e:
        raise _bail(e)

    # ★ 라이선스·접근제한 게이트는 여기서 확정된다 (검색 시점이 아니라).
    if not detail["addable"]:
        raise HTTPException(status_code=409, detail=detail["reason_ko"])

    by_name = {t["name"]: t for t in detail["tracks"]}
    wanted, skipped, duplicates = [], 0, 0
    for name in body.names:
        t = by_name.get(name)
        if t is None:
            skipped += 1                      # 메타데이터에 없는 이름은 통과시키지 않는다
        elif playlists.has_source_file(body.identifier, name):
            duplicates += 1
        else:
            wanted.append(t)

    if wanted:
        input_limits.assert_media_budget(sum(t["bytes"] for t in wanted))

    now = dt.datetime.now().astimezone().isoformat()
    src_ref_base = {
        "provider": "archive.org",
        "identifier": body.identifier,
        "album_orig": detail["title"],
        "details_url": detail["details_url"],
    }

    entries = []
    for t in wanted:
        # 저장 파일명은 원격 이름에서 파생하지 않는다 — 업로드와 같은 원칙
        tid = f"u-{uuid.uuid4().hex[:12]}"
        entries.append({
            "id": tid,
            "origin": "search",
            "title_ko": t["title_ko"],
            "title_orig": t["title_orig"],
            "composer_ko": None,
            "performer_ko": detail.get("creator"),
            "filename": f"{tid}.mp3",
            "bytes": t["bytes"],
            "duration_seconds": t["duration_seconds"],
            "duration_suspect": t["duration_suspect"],
            "sha1": t["sha1"],
            "integrity": "sha1" if t["sha1"] else "size",
            "license": detail["license"],
            "license_kind": detail["license_kind"],
            "url": t["url"],
            "subdir": "user",
            "source_ref": {**src_ref_base, "name": t["name"]},
            "added_at": now,
        })

    if entries:
        playlists.register_user_tracks(entries)
        ids = [e["id"] for e in entries]
        for pid in body.playlists:
            playlists.add_tracks(pid, ids)

    dl = {"active": False, "job": None}
    pending: list[str] = []
    if entries and body.download:
        snap = media.job_snapshot()
        if snap.get("active"):
            # 이미 잡이 돌고 있으면 새 트랙은 이번 잡에 안 들어간다 — 프론트가 다시 요청한다
            pending = [e["id"] for e in entries]
            dl = snap
        else:
            dl = media.start_download([e["id"] for e in entries])

    where = " · ".join("집중" if p == "focus" else "휴식" for p in body.playlists)
    msg = f"{len(entries)}곡을 {where or '보관함'}에 추가했습니다."
    if duplicates:
        msg += f" ({duplicates}곡은 이미 있어 건너뛰었습니다.)"
    if pending:
        msg += " 지금 내려받는 중인 작업이 끝나면 이어서 받습니다."
    elif entries and body.download:
        msg += " 내려받는 중입니다."

    return {
        "added": len(entries),
        "skipped": skipped,
        "duplicates": duplicates,
        "tracks": [t for t in playlists.all_tracks()
                   if t["id"] in {e["id"] for e in entries}],
        "playlists": playlists.list_playlists(),
        "download": dl,
        "pending_track_ids": pending,
        "message_ko": msg,
    }
