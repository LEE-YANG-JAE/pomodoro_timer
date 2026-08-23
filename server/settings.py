"""사용자 설정 — Pydantic 모델 + 로드/저장/마이그레이션.

★ 시간 필드는 전부 **초 단위**다 (`focus_seconds` 등). 분은 UI 표시 단위일 뿐.
   이 결정 덕분에 스모크 테스트가 `PUT /api/settings {"timer":{"focus_seconds":3,...}}`
   만으로 3초짜리 전체 사이클을 돌릴 수 있고, 테스트 전용 코드 경로가 0개가 된다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import config, storage

SETTINGS_VERSION = 1

_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def _path() -> Path:
    return config.DATA_DIR / "settings.json"


class CycleSet(BaseModel):
    """한 세트 = 집중 한 번 + 뒤따르는 휴식 한 번."""
    model_config = ConfigDict(extra="forbid")

    # ★ 하한이 1초인 것은 의도적이다. 스모크 테스트가 focus_seconds=3 으로 전체
    #   사이클을 3초에 돌릴 수 있어야 테스트 전용 코드 경로가 0개가 된다.
    #   사람이 쓰기에 합리적인 최소값(1분)은 UI 의 input 컨트롤이 강제한다.
    focus_seconds: int = Field(1500, ge=1, le=10800)     # 기본 25분
    break_seconds: int = Field(300, ge=1, le=7200)       # 기본 5분
    label: str | None = Field(None, max_length=40)


def _default_sets() -> list[CycleSet]:
    # 기본은 한 세트 — 25분 집중 + 5분 휴식. 나머지는 사용자가 추가한다.
    return [CycleSet()]


class TimerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ★ 사이클은 "세트 목록"으로 표현한다. 예전의 (집중/짧은휴식/긴휴식 + 반복 횟수)
    #   모델은 "3번째 뒤엔 15분 쉬고 싶다" 같은 요구를 표현할 수 없었다.
    #   세트 목록이면 그런 계획을 그대로 적을 수 있다.
    sets: list[CycleSet] = Field(default_factory=_default_sets, min_length=1, max_length=24)
    repeat: bool = True                 # 마지막 세트가 끝나면 첫 세트로 돌아간다

    auto_start_break: bool = True
    auto_start_focus: bool = False
    # 절전·탭 정지 등으로 오래 끊겼을 때의 기본 동작
    interruption_policy: Literal["ask", "extend", "ignore"] = "ask"


class AudioSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    focus_playlist_id: str = Field("focus", pattern=_ID_PATTERN)
    break_playlist_id: str = Field("break", pattern=_ID_PATTERN)
    long_break_playlist_id: str | None = Field(None, pattern=_ID_PATTERN)
    # ★ 기본 음량을 낮게 둔다. Thompson/Schellenberg/Letnic(2012) — 배경음악이
    #   독해를 방해하는 정도는 템포보다 **음량**에 좌우되며, 템포는 음량이 클 때만
    #   유의미했다. 기본값을 크게 잡으면 앱이 집중을 돕는 게 아니라 방해하게 된다.
    music_volume: int = Field(55, ge=0, le=100)
    chime_volume: int = Field(70, ge=0, le=100)
    silent_mode: bool = False          # 음악 없이 타이머+차임만 (1급 옵션)
    shuffle_focus: bool = False
    shuffle_break: bool = True
    crossfade_seconds: int = Field(3, ge=0, le=10)
    chime_variant: Literal["bell", "soft", "digital"] = "bell"
    chime_enabled: bool = True
    duck_on_chime: bool = True

    # ── 백색 소음 ───────────────────────────────────────────────────────────
    # 음악과 독립된 레이어다. 음악 없이 소음만 틀 수도 있고, 클래식 위에 얹어
    # 주변 말소리를 덮을 수도 있다.
    noise_enabled: bool = False
    # 기본값이 white 가 아니라 brown 인 이유: 백색 소음은 고역이 강해 30분 넘게 들으면
    # 귀가 쉽게 피로해진다. 브라운은 같은 마스킹 효과를 내면서 훨씬 덜 거슬린다.
    noise_type: Literal["brown", "pink", "white", "rain", "waves", "fan"] = "brown"
    noise_volume: int = Field(35, ge=0, le=100)
    noise_phases: Literal["focus", "all"] = "focus"


class RecordSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 하루의 시작 시각. 4 로 두면 새벽 3:59 에 끝낸 세션이 '어제'로 집계된다.
    # 야간 작업자에게 흔한 기대이며, 변경 시 기존 기록의 local_date 가 소급 재계산된다.
    day_start_hour: int = Field(0, ge=0, le=12)
    # ★ 기본 8 은 "압도감" 유발로 문서화된 값이다. 4 로 시작해 늘려 가는 편이 낫다.
    daily_goal: int = Field(4, ge=1, le=50)


class UiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: Literal["auto", "light", "dark"] = "auto"
    notifications: bool = False
    wake_lock: bool = True
    auto_focus_mode: bool = True       # 집중 세션 중 UI 를 최소화
    dynamic_favicon: bool = True


class MediaSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_download: bool = True
    auto_download_done: bool = False
    default_tier: Literal["core", "all"] = "core"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = SETTINGS_VERSION
    timer: TimerSettings = Field(default_factory=TimerSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    records: RecordSettings = Field(default_factory=RecordSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    media: MediaSettings = Field(default_factory=MediaSettings)
    host: str | None = None            # launcher 가 --host 로 명시했을 때만 기록


class SettingsPatch(BaseModel):
    """★ 그룹 단위 **교체** (부분 병합이 아니다).

    프론트는 GET 으로 전체 설정을 갖고 있으므로 수정한 그룹을 통째로 보낸다.
    병합 규칙이 없으면 병합 버그도 없다.
    """
    model_config = ConfigDict(extra="forbid")

    timer: TimerSettings | None = None
    audio: AudioSettings | None = None
    records: RecordSettings | None = None
    ui: UiSettings | None = None
    media: MediaSettings | None = None


def defaults() -> Settings:
    return Settings()


def _migrate(raw: dict) -> dict:
    """예전 버전 설정을 현재 스키마로 올린다.

    v1 초기형은 (focus_seconds / short_break_seconds / long_break_seconds /
    cycles_until_long_break) 였다. 이를 동등한 세트 목록으로 변환한다 —
    예: 25/5/15, 4회 → [25/5, 25/5, 25/5, 25/15]
    """
    if not isinstance(raw, dict):
        return {}
    raw.setdefault("version", SETTINGS_VERSION)

    timer = raw.get("timer")
    if isinstance(timer, dict) and "sets" not in timer and "focus_seconds" in timer:
        focus = int(timer.pop("focus_seconds", 1500) or 1500)
        short = int(timer.pop("short_break_seconds", 300) or 300)
        long_b = int(timer.pop("long_break_seconds", 900) or 900)
        cycles = int(timer.pop("cycles_until_long_break", 4) or 4)
        cycles = max(1, min(24, cycles))
        sets = []
        for i in range(cycles):
            last = i == cycles - 1
            sets.append({
                "focus_seconds": focus,
                "break_seconds": long_b if (last and cycles > 1) else short,
                "label": None,
            })
        timer["sets"] = sets
        timer.setdefault("repeat", True)
    return raw


def load() -> Settings:
    """디스크에서 설정을 읽는다. 없거나 깨졌으면 기본값.

    검증 실패 시에도 raise 하지 않는다 — 손상된 설정 파일 하나로 앱이 부팅에 실패하면
    사용자가 손으로 JSON 을 고쳐야만 앱을 쓸 수 있게 된다. 기본값으로 계속 진행한다.
    """
    raw = storage.read_json(_path(), default=None)
    if raw is None:
        return defaults()
    try:
        return Settings.model_validate(_migrate(raw))
    except Exception as e:  # noqa: BLE001 — 어떤 검증 오류든 기본값으로 열화
        print(f"[settings] 설정 파일을 읽을 수 없어 기본값을 사용합니다: {e}")
        return defaults()


def save(current: Settings) -> Settings:
    storage.atomic_write(_path(), current.model_dump(mode="json"))
    return current


def apply_patch(patch: SettingsPatch) -> tuple[Settings, list[str]]:
    """변경된 그룹만 통째로 갈아끼운다. (설정, 바뀐 그룹 이름 목록) 반환."""
    current = load()
    changed: list[str] = []
    for group in ("timer", "audio", "records", "ui", "media"):
        incoming = getattr(patch, group)
        if incoming is None:
            continue
        if getattr(current, group) != incoming:
            setattr(current, group, incoming)
            changed.append(group)
    if changed:
        save(current)
    return current, changed


def ensure_file() -> None:
    """설정 파일이 없으면 기본값으로 만든다 (부트스트랩 시드)."""
    if not _path().exists():
        save(defaults())
