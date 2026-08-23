#!/usr/bin/env python3
"""단일 진입점: venv 부트스트랩 → 의존성 → FastAPI 실행 → 브라우저 열기.

사용법:
    python launcher.py                  # 기본 실행 (127.0.0.1:8025)
    python launcher.py --no-browser     # 브라우저 자동 열기 없이
    python launcher.py --no-media       # 음원 자동 다운로드 없이
    python launcher.py --port 9000      # 포트 변경
    python launcher.py --host 0.0.0.0   # LAN 개방 (경고 출력, 인증 없음)

첫 실행 시 로컬 .venv 를 자동 생성하고 그 인터프리터로 자신을 재실행하므로
의존성 설치가 시스템 파이썬을 건드리지 않는다 (PEP 668 안전).
"""
from __future__ import annotations

import argparse
import logging
import platform
import socket
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
VENV_PY = VENV_DIR / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")


def _in_target_venv() -> bool:
    """이 인터프리터가 우리 venv 의 python 인가.

    .venv/bin/python 은 symlinks=True 로 만들면 시스템 python 을 가리키는 심링크라
    resolve() 비교는 잘못 매치된다. sys.prefix 가 올바른 신호다.
    """
    return Path(sys.prefix).resolve() == VENV_DIR.resolve()


def _ensure_venv_and_reexec() -> None:
    """venv 를 만들고(pip 없이) get-pip.py 로 pip 을 넣은 뒤 자신을 재실행."""
    import venv

    if not VENV_PY.exists():
        print(f"[launcher] 가상환경 생성 중: {VENV_DIR}")
        builder = venv.EnvBuilder(
            with_pip=False, clear=False, symlinks=platform.system() != "Windows"
        )
        builder.create(str(VENV_DIR))

    pip_check = subprocess.run([str(VENV_PY), "-m", "pip", "--version"], capture_output=True)
    if pip_check.returncode != 0:
        print("[launcher] venv 에 pip 설치 중 (get-pip.py) ...")
        get_pip = ROOT / "get-pip.py"
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", str(get_pip))
        try:
            subprocess.check_call([str(VENV_PY), str(get_pip)])
        finally:
            get_pip.unlink(missing_ok=True)

    print(f"[launcher] venv 인터프리터로 재실행: {VENV_PY}")
    result = subprocess.run([str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit(result.returncode)


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="뽀모도로 타이머 launcher")
    p.add_argument("--no-browser", action="store_true", help="브라우저 자동 열기 안 함")
    p.add_argument("--no-media", action="store_true", help="음원 자동 다운로드 안 함")
    p.add_argument("--port", type=int, default=None, help="API 포트 변경")
    p.add_argument(
        "--host",
        default=None,
        help=(
            "바인딩 호스트. 기본 127.0.0.1 (이 PC 전용). "
            "0.0.0.0 으로 LAN 개방 가능하나 인증이 없고 파일 업로드·폴더 탐색 "
            "엔드포인트가 노출되므로 신뢰할 수 있는 네트워크에서만 사용할 것. "
            "settings.json 에 저장되어 다음 실행에도 유지된다."
        ),
    )
    return p.parse_args()


def _detect_lan_ips() -> list[str]:
    """LAN IPv4 자동 탐지. 실제 패킷은 보내지 않고 커널 라우팅만 사용한다."""
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.add(ip)
        finally:
            s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip and not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def _resolve_host(cli_host: str | None, persisted: str | None) -> str:
    """우선순위: CLI > settings.json 의 host > 기본 127.0.0.1."""
    from server import config

    if isinstance(cli_host, str) and cli_host.strip():
        return cli_host.strip()
    if isinstance(persisted, str) and persisted.strip():
        return persisted.strip()
    return config.DEFAULT_HOST


def main() -> int:
    # Windows 기본 콘솔 인코딩(CP949)은 이모지를 인코딩하지 못해 print() 한 줄이
    # launcher 전체를 죽일 수 있다. 가능하면 UTF-8 로 재설정.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    args = _parse()

    from server import bootstrap, config

    if args.port:
        config.API_PORT = args.port

    bootstrap.run_full_bootstrap()

    from server import settings as settings_mod

    current = settings_mod.load()
    resolved_host = _resolve_host(args.host, current.host)
    if args.host and current.host != resolved_host:
        current.host = resolved_host
        settings_mod.save(current)

    # 음원 자동 다운로드 허용 여부를 media 모듈에 알린다. create_app() 의 startup 훅이
    # 이 플래그를 보고 백그라운드 스레드를 띄운다 — TestClient 는 절대 네트워크를 쓰지 않는다.
    try:
        from server import media

        media.enable_auto_download(not args.no_media)
    except ImportError:
        pass  # media 모듈은 이후 단계에서 추가된다

    local_url = f"http://127.0.0.1:{config.API_PORT}/"
    if not args.no_browser:
        threading.Timer(2.0, lambda: webbrowser.open(local_url)).start()

    print(f"[launcher] 서비스 시작: {local_url}")
    if resolved_host != "127.0.0.1":
        lan_ips = _detect_lan_ips()
        if lan_ips:
            print("[launcher] LAN 주소: " + "  ".join(
                f"http://{ip}:{config.API_PORT}/" for ip in lan_ips))
        print(f"[launcher] 경고: 외부 접근이 허용되었습니다 (host={resolved_host}). 인증 없음.")
        print("[launcher] 경고: 파일 업로드·폴더 탐색 엔드포인트가 함께 노출됩니다.")
        print("[launcher] 경고: 신뢰할 수 있는 네트워크에서만 사용하세요.")
        print("[launcher] 참고: http:// 는 secure context 가 아니라 다른 기기에서는")
        print("[launcher]       알림·화면 켜짐 유지 기능이 동작하지 않습니다.")

    import uvicorn

    from server.app import create_app

    uvicorn.run(create_app(), host=resolved_host, port=config.API_PORT, log_level="info")
    return 0


if __name__ == "__main__":
    if not _in_target_venv():
        _ensure_venv_and_reexec()
    sys.exit(main())
