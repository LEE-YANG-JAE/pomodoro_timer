// 전역 상태 + 이벤트 버스.
//
// 모듈 간 통신은 전부 이 버스를 거친다. timer.js 가 ui.js 나 audio.js 를 직접 import 하지
// 않으므로 순환 import 가 0개가 되고, 타이머 엔진을 DOM 없이 단독 테스트할 수 있다.

export const LS = {
  TIMER: "pomo.timer.v1",       // 진행 중인 타이머 (매 틱 저장)
  SETTINGS: "pomo.settings.v1", // 서버 실패 시 폴백 캐시
  QUEUE: "pomo.queue.v1",       // 전송 실패한 세션 큐
  STATS: "pomo.stats.v1",       // 오프라인 표시용 캐시
  AUDIO: "pomo.audio.v1",       // 재생 위치 / 셔플 순서
  VOLUME: "pomo.volume.v1",     // ★ 기기별 음량 override (서버 값보다 우선)
  TASKS: "pomo.tasks.v1",       // 오프라인 표시용 캐시
  THEME: "pomo.theme",          // boot.js 와 공유
};

// 서버 스키마와 동일한 snake_case 를 그대로 쓴다. 경계에서 이름을 바꾸지 않으므로
// 변환 버그가 생길 자리가 없다.
export const DEFAULT_SETTINGS = {
  timer: {
    // 한 세트 = 집중 한 번 + 뒤따르는 휴식 한 번. 기본은 25분 집중 + 5분 휴식 1세트.
    sets: [{ focus_seconds: 1500, break_seconds: 300, label: null }],
    repeat: true,
    auto_start_break: true,
    auto_start_focus: false,
    interruption_policy: "ask",
  },
  audio: {
    focus_playlist_id: "focus",
    break_playlist_id: "break",
    long_break_playlist_id: null,
    music_volume: 55,
    chime_volume: 70,
    silent_mode: false,
    shuffle_focus: false,
    shuffle_break: true,
    crossfade_seconds: 3,
    chime_variant: "bell",
    chime_enabled: true,
    duck_on_chime: true,
    noise_enabled: false,
    noise_type: "brown",
    noise_volume: 35,
    noise_phases: "focus",
  },
  records: { day_start_hour: 0, daily_goal: 4 },
  ui: {
    theme: "auto",
    notifications: false,
    wake_lock: true,
    auto_focus_mode: true,
    dynamic_favicon: true,
  },
  media: { auto_download: true, auto_download_done: false, default_tier: "core" },
};

export const PHASES = ["focus", "break"];

export const PHASE_LABEL = {
  focus: "집중",
  break: "휴식",
  // 예전 기록 표시 호환
  short_break: "휴식",
  long_break: "긴 휴식",
};

// 절전·탭 정지 후 이 시간(ms) 이하의 초과는 정상 오버슛으로 보고 자동 진행한다.
// 그보다 크면 "정말 그 시간 동안 집중했는가"를 알 수 없으므로 사용자에게 묻는다.
export const GRACE_MS = 120_000;

export const state = {
  timer: {
    phase: "focus",
    status: "idle",         // idle | running | paused | interrupted
    endsAt: null,           // ★ 단일 진실원 (epoch ms). running 일 때만 유효
    remainingMs: 1500_000,  // paused / idle 일 때 유효 + 표시 캐시
    plannedMs: 1500_000,    // 이번 구간 총 길이 (진행 링의 분모)
    phaseStartedAt: null,
    setIndex: 0,            // 현재 진행 중인 세트 번호 (0부터)
    completedFocus: 0,      // 이번 실행에서 완료한 집중 구간 수
    sessionId: null,        // 클라이언트 생성 멱등키
    interruptions: 0,       // 절전 등으로 자동 감지된 중단 횟수 (일시정지·복구 포함)
    lastTickAt: 0,          // 갭 감지용
    warnedSoon: false,          // 종료 30초 전 예고를 이미 했는가
    pendingGap: null,       // { gapMs, phase, endsAt, plannedMs, startedAt }
    lastSkipped: null,      // 되돌리기용 스냅샷
  },
  settings: structuredClone(DEFAULT_SETTINGS),
  audio: {
    supported: true,
    unlocked: false,
    blocked: false,
    muted: false,
    ctxState: "closed",
    activeDeck: 0,
    current: null,          // { id, title_ko, performer_ko, url }
    order: { focus: [], break: [] },   // 셔플 반영된 트랙 id 순서
    index: { focus: 0, break: 0 },     // ★ 위상이 바뀌어도 유지 (이어듣기)
    failed: new Set(),
    playlistDead: false,
    warnedEmpty: false,
    noisePlaying: false,
    noiseType: null,
    userPaused: false,      // 사용자가 음악만 직접 멈춘 상태 (타이머와 무관)
  },
  tasks: {
    items: [],            // ★ 서버 응답으로 통째로 교체된다 (§3.6)
    activeId: null,
    totals: { est_total: 0, done_total: 0, remaining_est: 0,
              completed_est_total: 0, completed_done_total: 0 },
    loadedAt: 0,
    stale: false,
  },
  tracks: [],
  playlists: [],
  download: { active: false, done: 0, total: 0, failed: 0, message: "" },
  stats: {
    today: { pomodoro_count: 0, focus_seconds: 0, aborted_count: 0 },
    series: [],
    streak: { current: 0, best: 0, includes_today: false },
    daily_goal: 4,
    loadedAt: 0,
    stale: false,
  },
  queue: [],
  ui: {
    view: "timer",
    focusMode: false,
    modalDirty: false,
    modalLastTrigger: null,
    srLast: "",
  },
};

// ── 이벤트 버스 ──────────────────────────────────────────────────────────────
const bus = new EventTarget();

export function on(type, handler) {
  bus.addEventListener(type, (e) => handler(e.detail));
}

export function emit(type, detail) {
  bus.dispatchEvent(new CustomEvent(type, { detail }));
}

// ── localStorage 헬퍼 ────────────────────────────────────────────────────────
// 사파리 프라이빗 모드 등에서 localStorage 접근 자체가 throw 할 수 있으므로 항상 감싼다.

export function lsGet(key, fallback = null) {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function lsSet(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

export function lsRemove(key) {
  try {
    localStorage.removeItem(key);
  } catch {
    /* 무시 */
  }
}
