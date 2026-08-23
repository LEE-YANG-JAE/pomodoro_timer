"""업로드 검증 — 크기 · 확장자 · 매직바이트 · 디스크 예산.

llm_wiki 의 server/input_limits.py 와 같은 역할. 파일을 받는 경계는 하나뿐이므로
검증도 여기 한 곳에 모은다.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from fastapi import HTTPException, UploadFile

from . import config

MIB = 1024 * 1024
MAX_AUDIO_BYTES = 60 * MIB          # 무손실(FLAC/WAV) 긴 악장까지 커버
MAX_IMPORT_FILES = 200              # 폴더 가져오기 1회 상한
_READ_CHUNK = 1 * MIB

ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".wav"}

# 확장자 → 파일 시작 바이트. 확장자만 믿으면 아무 파일이나 .mp3 로 올릴 수 있다.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa", b"\xff\xe3"),
    ".ogg": (b"OggS",),
    ".oga": (b"OggS",),
    ".opus": (b"OggS",),
    ".flac": (b"fLaC",),
    ".wav": (b"RIFF",),
    # .m4a 는 오프셋 4에 "ftyp" 가 있다 — 아래에서 따로 처리
}

_SAFE_DISPLAY = re.compile(r"[^\w가-힣 .()\-\[\]&,']")


def require_allowed_audio_name(filename: str | None) -> str:
    """확장자 화이트리스트 검사 후 소문자 확장자를 돌려준다."""
    if not filename:
        raise HTTPException(status_code=400, detail="파일 이름이 없습니다.")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_AUDIO_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 형식입니다 ({ext or '확장자 없음'}). 가능: {allowed}",
        )
    return ext


def sniff_audio_magic(head: bytes, ext: str) -> None:
    """파일 시작 바이트가 확장자와 맞는지 확인."""
    if ext == ".m4a":
        if len(head) >= 12 and head[4:8] == b"ftyp":
            return
        raise HTTPException(status_code=400, detail="m4a 파일 형식이 아닙니다.")
    prefixes = _MAGIC.get(ext)
    if not prefixes:
        return
    if any(head.startswith(p) for p in prefixes):
        return
    # MP3 는 앞에 ID3 태그나 패딩이 붙는 경우가 많아 프레임 싱크를 조금 더 뒤에서도 찾는다
    if ext == ".mp3" and b"\xff" in head[:1024]:
        return
    raise HTTPException(
        status_code=400,
        detail="파일 내용이 오디오 형식과 일치하지 않습니다.",
    )


def safe_display_name(raw: str | None) -> str:
    """표시용 이름. ★ 절대 경로로 쓰지 않는다 — 저장 파일명은 서버가 생성한다."""
    if not raw:
        return "이름 없는 음원"
    # Windows 구분자까지 처리 (PurePosixPath.name 은 POSIX 에서 백슬래시를 못 자른다)
    base = os.path.basename(str(raw).replace("\\", "/"))
    base = Path(base).stem
    cleaned = _SAFE_DISPLAY.sub("_", base).strip()
    if not cleaned or set(cleaned) <= {".", "_", " "}:
        return "이름 없는 음원"
    return cleaned[:120]


def media_bytes_on_disk() -> int:
    total = 0
    for p in config.MEDIA_DIR.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def assert_media_budget(additional_bytes: int) -> None:
    used = media_bytes_on_disk()
    if used + additional_bytes > config.MEDIA_MAX_TOTAL_BYTES:
        limit_gb = config.MEDIA_MAX_TOTAL_BYTES / (1024 ** 3)
        raise HTTPException(
            status_code=507,
            detail=f"음원 저장 한도({limit_gb:.0f}GB)를 초과합니다. 필요 없는 음원을 지워 주세요.",
        )


def assert_free_space(needed_bytes: int) -> None:
    try:
        free = shutil.disk_usage(config.MEDIA_DIR).free
    except OSError:
        return
    if free < needed_bytes * 1.2:
        need_mb = needed_bytes / MIB
        free_mb = free / MIB
        raise HTTPException(
            status_code=507,
            detail=f"디스크 공간이 부족합니다. 필요 약 {need_mb:.0f}MB, 남은 공간 {free_mb:.0f}MB.",
        )


async def read_audio_upload(file: UploadFile) -> tuple[bytes, str]:
    """업로드 본문을 청크로 읽으며 크기 상한을 강제한다.
    (한 번에 read() 하면 상한을 넘는 파일도 일단 메모리에 올라간다.)"""
    ext = require_allowed_audio_name(file.filename)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_AUDIO_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"파일이 너무 큽니다. 최대 {MAX_AUDIO_BYTES // MIB}MB 까지 가능합니다.",
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    data = b"".join(chunks)
    sniff_audio_magic(data[:1024], ext)
    assert_media_budget(total)
    return data, ext
