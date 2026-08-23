"""경로·포트·상한 상수.

★ 중요한 관례 — 다른 모듈은 이 모듈의 값을 **import 시점에 바인딩하지 말고
호출 시점에 읽어야 한다.**

    def _sessions_path() -> Path:
        return config.DATA_DIR / "sessions.json"   # O — 호출 시점에 읽는다

    SESSIONS_FILE = config.DATA_DIR / "sessions.json"   # X — 테스트 격리 불가

테스트가 `config.DATA_DIR` 를 임시 폴더로 monkeypatch 해서 격리하는 유일한 조건이다.
형제 프로젝트 llm_wiki 의 `server/cards.py:_cards_path()` 와 동일한 패턴.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 디렉터리 ────────────────────────────────────────────────────────────────
UI_DIR = ROOT / "ui"
DATA_DIR = ROOT / "data"            # 사용자 데이터 JSON (gitignore)
MEDIA_DIR = ROOT / "media"          # 음원 (gitignore)
CATALOG_FILE = ROOT / "media_catalog.json"  # 트랙 매니페스트 (리포에 커밋)


# MEDIA_DIR 하위 경로는 **함수로** 노출한다. 상수로 두면 테스트가 MEDIA_DIR 를
# monkeypatch 해도 하위 경로가 import 시점의 옛 값을 계속 가리킨다.
def catalog_media_dir() -> Path:
    """카탈로그에서 내려받은 음원 디렉터리."""
    return MEDIA_DIR / "catalog"


def user_media_dir() -> Path:
    """업로드 · 폴더 가져오기로 추가된 음원 디렉터리."""
    return MEDIA_DIR / "user"

# ── 네트워크 ────────────────────────────────────────────────────────────────
API_PORT = 8025                     # 8000 은 형제 프로젝트 llm_wiki 가 사용 중
DEFAULT_HOST = "127.0.0.1"          # ★ llm_wiki 의 0.0.0.0 기본을 뒤집는다.
#   이 백엔드는 파일 업로드 · 서버측 폴더 브라우저 · 임의 경로 복사 엔드포인트를
#   갖는다. 인증 없이 LAN 에 여는 것은 읽기 위주 위키를 여는 것보다 훨씬 나쁘다.
#   뽀모도로 타이머는 작업하는 바로 그 책상에서 쓰므로 LAN 필요성도 낮다.
#   `--host 0.0.0.0` 으로 opt-in 은 가능하며 그때 경고가 출력된다.

USER_AGENT = "pomodoro-timer/0.1 (+local personal use)"

# ── 상한 ────────────────────────────────────────────────────────────────────
MIB = 1024 * 1024
MEDIA_MAX_TOTAL_BYTES = 2 * 1024 * MIB      # media/ 전체 디스크 예산
SESSION_LOG_CAP = 50_000                    # sessions.json 최대 레코드 수
TASK_LIST_CAP = 500                         # tasks.json 최대 작업 수
TASK_RETENTION_DAYS = 14                    # 완료한 작업을 파일에 남겨 두는 기간
DOWNLOAD_CHUNK = 256 * 1024
DOWNLOAD_TIMEOUT = 60


def ensure_dirs() -> None:
    """필요한 디렉터리를 모두 만든다. 호출 시점의 상수를 읽으므로 테스트에서
    DATA_DIR/MEDIA_DIR 를 바꾼 뒤 호출하면 그쪽에 만들어진다."""
    for d in (DATA_DIR, MEDIA_DIR, catalog_media_dir(), user_media_dir()):
        d.mkdir(parents=True, exist_ok=True)
