"""부팅 시 준비 작업 — 의존성 설치 · 디렉터리 · 기본 데이터 시드.

★ 이 모듈은 **네트워크를 쓰지 않는다** (pip 설치 제외). 음원 다운로드는
`server/media.py` 가 서버 기동 후 백그라운드 스레드에서 처리한다.
그래야 첫 실행이 몇 분씩 막히지 않고, `create_app()` 을 쓰는 테스트가 오프라인으로 돈다.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys

from . import config

PIP_PACKAGES = [
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "pydantic>=2.9",
    "python-multipart>=0.0.12",
]

# pip 이름 → import 이름 (다른 것만)
_IMPORT_NAME = {"python-multipart": "multipart"}


def _missing_packages() -> list[str]:
    missing: list[str] = []
    for spec in PIP_PACKAGES:
        pip_name = spec.split(">=")[0].split("==")[0].strip()
        mod = _IMPORT_NAME.get(pip_name, pip_name.replace("-", "_"))
        if importlib.util.find_spec(mod) is None:
            missing.append(spec)
    return missing


def install_dependencies() -> None:
    """빠진 의존성만 현재 인터프리터(=venv)에 설치한다."""
    missing = _missing_packages()
    if not missing:
        print("[bootstrap] python 의존성 OK")
        return
    print(f"[bootstrap] 빠진 의존성 설치: {missing}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", *missing])


def seed_data() -> None:
    """`data/` 의 기본 JSON 을 없을 때만 만든다. 기존 파일은 건드리지 않는다."""
    from . import playlists, settings, tasks

    settings.ensure_file()
    playlists.ensure_file()
    tasks.ensure_file()


def run_full_bootstrap() -> None:
    install_dependencies()
    config.ensure_dirs()
    seed_data()
    print("[bootstrap] 준비 완료")
