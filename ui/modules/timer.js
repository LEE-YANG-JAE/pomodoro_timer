// 뽀모도로 타이머 엔진.
//
// ★ 이 모듈은 DOM 도 오디오도 모른다. 상태를 바꾸고 이벤트만 쏜다.
//   덕분에 브라우저 없이도 로직을 검증할 수 있고 순환 import 가 생기지 않는다.
//
// ★ 데드라인 기반이다. setInterval 로 남은 시간을 "차감"하면 안 된다 — 브라우저는
//   백그라운드 탭의 타이머를 1초 이상(종종 1분)으로 스로틀하고, 완전히 가려진 탭은
//   아예 얼린다. 절대 시각 endsAt 하나만 진실원으로 두고 매 틱마다
//   endsAt - Date.now() 를 다시 계산하면 20분간 잠들었다 깨어나도 값이 정확하다.

import { GRACE_MS, LS, emit, lsGet, lsRemove, lsSet, state } from "./state.js";
import { toLocalISO, uid } from "./utils.js";

const TICK_MS = 250;   // 표시 갱신용. 남은 시간은 언제나 endsAt 에서 재계산한다.
const PREWARN_MS = 30_000;   // 구간 종료 몇 ms 전에 예고할 것인가
// ★ 자동 시작이 켜져 있어도 곧바로 넘기지 않고 잠깐 붙잡는다.
//   "휴식 침식" — 0:00 에 휴식이 자동 시작되면 그 앞부분을 하던 일 마무리에 쓰게 되어
//   5분 휴식이 실제로는 2분이 된다. 유예를 두면 자동/수동 양쪽 요구를 동시에 만족한다.
const HOLD_SECONDS = 20;
let tickHandle = null;

// ── 세트 ─────────────────────────────────────────────────────────────────────
//
// ★ 사이클은 "세트 목록"으로 표현한다. 한 세트 = 집중 한 번 + 뒤따르는 휴식 한 번.
//   예전의 (집중/짧은휴식/긴휴식 + 반복 횟수) 모델로는 "3번째 세트만 길게 쉬고 싶다"
//   같은 계획을 표현할 수 없었다. 세트 목록이면 그대로 적으면 된다.

export function sets() {
  const list = state.settings.timer.sets;
  return Array.isArray(list) && list.length ? list : [{ focus_seconds: 1500, break_seconds: 300 }];
}

export function setCount() {
  return sets().length;
}

/** 현재 세트. 목록이 줄어들어 인덱스가 범위를 벗어나면 앞으로 되돌린다. */
export function currentSet() {
  const list = sets();
  const i = Math.min(Math.max(0, state.timer.setIndex ?? 0), list.length - 1);
  if (state.timer.setIndex !== i) state.timer.setIndex = i;
  return list[i];
}

export function phaseSeconds(phase, setIdx = null) {
  const list = sets();
  const i = setIdx == null
    ? Math.min(Math.max(0, state.timer.setIndex ?? 0), list.length - 1)
    : Math.min(Math.max(0, setIdx), list.length - 1);
  const set = list[i];
  return phase === "focus" ? set.focus_seconds : set.break_seconds;
}

/** 휴식이 끝났을 때 다음 세트로. repeat 가 꺼져 있고 마지막 세트면 null. */
function advanceSet() {
  const list = sets();
  const next = (state.timer.setIndex ?? 0) + 1;
  if (next < list.length) return next;
  return state.settings.timer.repeat ? 0 : null;
}

function nextPhaseAfter(phase) {
  // 세트 안에서는 집중 → 휴식, 휴식이 끝나면 다음 세트의 집중.
  return phase === "focus" ? "break" : "focus";
}

function shouldAutoStart(nextPhase) {
  const t = state.settings.timer;
  return nextPhase === "focus" ? t.auto_start_focus : t.auto_start_break;
}

// ── 조회 ─────────────────────────────────────────────────────────────────────

export function getRemainingMs(now = Date.now()) {
  const T = state.timer;
  if (T.status === "running" && T.endsAt != null) return Math.max(0, T.endsAt - now);
  return Math.max(0, T.remainingMs);
}

export function getProgress(now = Date.now()) {
  const T = state.timer;
  if (!T.plannedMs) return 0;
  return Math.min(1, Math.max(0, 1 - getRemainingMs(now) / T.plannedMs));
}

function tickDetail(now = Date.now()) {
  const T = state.timer;
  return {
    remainingMs: getRemainingMs(now),
    plannedMs: T.plannedMs,
    progress: getProgress(now),
    phase: T.phase,
    status: T.status,
    setIndex: T.setIndex,
  };
}

// ── 영속화 ───────────────────────────────────────────────────────────────────

export function saveTimer() {
  const T = state.timer;
  lsSet(LS.TIMER, {
    phase: T.phase,
    status: T.status,
    endsAt: T.endsAt,
    remainingMs: T.remainingMs,
    plannedMs: T.plannedMs,
    phaseStartedAt: T.phaseStartedAt,
    setIndex: T.setIndex,
    completedFocus: T.completedFocus,
    sessionId: T.sessionId,
    interruptions: T.interruptions,
    savedAt: Date.now(),
  });
}

// ── 위상 전환 ────────────────────────────────────────────────────────────────

function setStatus(next) {
  const prev = state.timer.status;
  if (prev === next) return;
  state.timer.status = next;
  emit("timer:status", { status: next, prev });
}

/**
 * 새 구간을 준비한다. autoStart 면 곧바로 카운트다운을 시작한다.
 */
export function setPhase(phase, { autoStart = false, autoStarted = false, setIndex = null } = {}) {
  const T = state.timer;
  if (setIndex != null) T.setIndex = setIndex;
  const ms = phaseSeconds(phase) * 1000;
  T.phase = phase;
  T.plannedMs = ms;
  T.remainingMs = ms;
  T.endsAt = null;
  T.phaseStartedAt = null;
  T.sessionId = null;
  T.interruptions = 0;
  T.warnedSoon = false;
  T.pendingGap = null;
  setStatus("idle");
  emit("timer:phase-start", { phase, plannedMs: ms, autoStarted, setIndex: T.setIndex });
  saveTimer();
  if (autoStart) startTimer();
  else emit("timer:tick", tickDetail());
}

/**
 * 구간 종료 처리. 집중 구간이었다면 기록할 세션 객체를 만들어 함께 넘긴다.
 *
 * @param {object} opts
 *   - endedAt: 실제 종료 시각 (기본 endsAt). ★ Date.now() 가 아니다 — 절전에서
 *     깨어나 뒤늦게 처리하는 경우 실제로 타이머가 끝난 시점을 기록해야 한다.
 *   - completed: 끝까지 갔는지
 *   - actualMs: 실제 집중한 시간 (기본 plannedMs)
 */
function finishPhase({ endedAt = null, completed = true, actualMs = null } = {}) {
  const T = state.timer;
  const phase = T.phase;
  const end = endedAt ?? T.endsAt ?? Date.now();
  const actual = actualMs ?? T.plannedMs;
  const started = T.phaseStartedAt ?? end - actual;

  let session = null;
  if (phase === "focus") {
    // 휴식은 기록하지 않는다 — 통계 범위가 집중 시간이다.
    session = {
      phase: "focus",
      started_at: toLocalISO(new Date(started)),
      ended_at: toLocalISO(new Date(end)),
      planned_seconds: Math.round(T.plannedMs / 1000),
      actual_seconds: Math.round(actual / 1000),
      completed,
      cycle_index: T.setIndex,
      interruptions: T.interruptions,
      client_id: T.sessionId || uid("s"),
    };
    if (completed) T.completedFocus += 1;
  }

  const next = nextPhaseAfter(phase);
  const lateMs = Math.max(0, Date.now() - end);

  // 휴식이 끝나면 다음 세트로 넘어간다. repeat 가 꺼져 있고 마지막 세트였다면
  // 계획이 모두 끝난 것이므로 멈춘다.
  let nextSet = T.setIndex;
  let planDone = false;
  if (phase !== "focus") {
    const adv = advanceSet();
    if (adv == null) planDone = true;
    else nextSet = adv;
  }

  emit("timer:phase-end", { phase, next, session, lateMs, completed, planDone });

  if (planDone) {
    T.setIndex = 0;
    setPhase("focus", { autoStart: false, setIndex: 0 });
    emit("timer:plan-done", {});
    return session;
  }

  const wantsAuto = completed && shouldAutoStart(next);
  setPhase(next, { autoStart: false, autoStarted: true, setIndex: nextSet });
  if (wantsAuto) {
    // 바로 시작하지 않고 UI 가 유예 카운트다운을 띄운다. 끝나면 startTimer() 를 부른다.
    emit("timer:hold", { phase: next, seconds: HOLD_SECONDS });
  }
  return session;
}

// ── 조작 ─────────────────────────────────────────────────────────────────────

export function startTimer() {
  const T = state.timer;
  if (T.status === "running") return;
  const remaining = T.remainingMs > 0 ? T.remainingMs : phaseSeconds(T.phase) * 1000;
  const now = Date.now();
  T.endsAt = now + remaining;
  if (T.phaseStartedAt == null) T.phaseStartedAt = now;
  if (!T.sessionId) T.sessionId = uid("s");
  T.lastTickAt = now;
  setStatus("running");
  saveTimer();
  emit("timer:tick", tickDetail(now));
  startLoop();
}

export function pauseTimer() {
  const T = state.timer;
  if (T.status !== "running") return;
  T.remainingMs = getRemainingMs();
  T.endsAt = null;
  T.interruptions += 1;
  setStatus("paused");
  saveTimer();
  emit("timer:tick", tickDetail());
}

export function toggleTimer() {
  if (state.timer.status === "running") pauseTimer();
  else startTimer();
}

/**
 * 진행 중인 구간을 연장한다.
 *
 * ★ 이탈 원인 1위가 "알림이 몰입을 끊는 것" 이다. 타이머가 데드라인 기반이라
 *   endsAt 을 미루는 것만으로 끝난다. planned_seconds 도 함께 늘려 기록이
 *   "25분 계획했는데 30분 했다" 가 아니라 "30분 계획하고 30분 했다" 가 되게 한다.
 */
export function extendPhase(seconds = 300) {
  const T = state.timer;
  if (T.status === "running" && T.endsAt != null) {
    T.endsAt += seconds * 1000;
  } else {
    T.remainingMs += seconds * 1000;
  }
  T.plannedMs += seconds * 1000;
  T.warnedSoon = false;          // 연장했으면 예고를 다시 할 수 있어야 한다
  saveTimer();
  emit("timer:tick", tickDetail());
  emit("timer:extended", { seconds, plannedMs: T.plannedMs });
  return seconds;
}

/** 남은 시간을 버리고 다음 구간으로. 집중 구간은 기록하지 않는다. */
export function skipPhase() {
  const T = state.timer;
  T.lastSkipped = {
    phase: T.phase,
    status: T.status,
    remainingMs: getRemainingMs(),
    plannedMs: T.plannedMs,
    phaseStartedAt: T.phaseStartedAt,
    setIndex: T.setIndex,
    sessionId: T.sessionId,
    interruptions: T.interruptions,
  };
  const phase = T.phase;
  const next = nextPhaseAfter(phase);
  let nextSet = T.setIndex;
  if (phase !== "focus") {
    const adv = advanceSet();
    nextSet = adv == null ? 0 : adv;
  }
  emit("timer:phase-end", { phase, next, session: null, lateMs: 0, completed: false });
  // ★ 건너뛰기는 이미 명시적 사용자 행동이다 — finishPhase() 의 "조용한 자동 시작을
  //   막기 위한 유예(timer:hold)" 도, auto_start_focus/break 설정 자체도 여기선
  //   필요 없다. 사용자가 방금 "다음으로 넘어가겠다"고 눌렀으니 항상 바로 시작한다.
  setPhase(next, { autoStart: true, setIndex: nextSet });
  emit("timer:skipped", { phase, next });
}

export function undoSkip() {
  const snap = state.timer.lastSkipped;
  if (!snap) return false;
  const T = state.timer;
  Object.assign(T, {
    phase: snap.phase,
    plannedMs: snap.plannedMs,
    remainingMs: snap.remainingMs,
    phaseStartedAt: snap.phaseStartedAt,
    setIndex: snap.setIndex,
    sessionId: snap.sessionId,
    interruptions: snap.interruptions,
    endsAt: null,
    lastSkipped: null,
  });
  setStatus(snap.status === "running" ? "paused" : "idle");
  emit("timer:phase-start", { phase: T.phase, plannedMs: T.plannedMs, autoStarted: false });
  emit("timer:tick", tickDetail());
  saveTimer();
  return true;
}

/** 현재 구간을 처음으로 되돌린다 (구간 종류·사이클은 유지). */
export function resetPhase() {
  const T = state.timer;
  T.endsAt = null;
  T.plannedMs = phaseSeconds(T.phase) * 1000;
  T.remainingMs = T.plannedMs;
  T.phaseStartedAt = null;
  T.sessionId = null;
  T.interruptions = 0;
  T.warnedSoon = false;
  setStatus("idle");
  saveTimer();
  emit("timer:tick", tickDetail());
  emit("timer:reset", { phase: T.phase });
}

/** 계획 전체를 처음(1세트 집중)으로 되돌린다. */
export function resetAll() {
  state.timer.setIndex = 0;
  state.timer.completedFocus = 0;
  setPhase("focus", { autoStart: false, setIndex: 0 });
}

/** 설정에서 구간 길이가 바뀌었을 때. 돌고 있는 타이머는 건드리지 않는다. */
export function applyDurationChange() {
  const T = state.timer;
  if (T.status === "running") return;
  const ms = phaseSeconds(T.phase) * 1000;
  T.plannedMs = ms;
  T.remainingMs = ms;
  saveTimer();
  emit("timer:tick", tickDetail());
}

// ── 중단 복구 ────────────────────────────────────────────────────────────────

/**
 * 복구 모달의 선택을 반영한다.
 *   record   — 계획대로 완료한 것으로 기록 (ended_at 은 endsAt, Date.now() 가 아니다)
 *   discard  — 기록하지 않고 이 구간을 처음부터
 *   continue — 남은 시간을 그대로 두고 이어서 (갭을 없던 일로)
 */
export function resolveGap(choice) {
  const T = state.timer;
  const gap = T.pendingGap;
  T.pendingGap = null;
  if (!gap) return;

  if (choice === "record") {
    T.phase = gap.phase;
    T.plannedMs = gap.plannedMs;
    T.phaseStartedAt = gap.startedAt;
    T.sessionId = gap.sessionId;
    T.interruptions = gap.interruptions;
    finishPhase({ endedAt: gap.endsAt, completed: true, actualMs: gap.plannedMs });
    return;
  }
  if (choice === "continue") {
    T.phase = gap.phase;
    T.plannedMs = gap.plannedMs;
    T.remainingMs = gap.remainingAtSleep ?? gap.plannedMs;
    T.phaseStartedAt = gap.startedAt;
    T.sessionId = gap.sessionId;
    T.interruptions = gap.interruptions + 1;
    setStatus("paused");
    saveTimer();
    emit("timer:tick", tickDetail());
    return;
  }
  // discard
  T.phase = gap.phase;
  T.setIndex = gap.setIndex ?? 0;
  resetPhase();
}

/** 절전·탭 정지로 큰 시간 공백이 생겼는지 판단하고, 필요하면 사용자에게 묻는다. */
function handleOverrun(now) {
  const T = state.timer;
  const overshoot = now - T.endsAt;
  const policy = state.settings.timer.interruption_policy;

  // 정상 오버슛 — 틱 간격이나 짧은 백그라운드. 그냥 완료 처리한다.
  if (overshoot <= GRACE_MS || policy === "ignore") {
    finishPhase({ endedAt: T.endsAt, completed: true });
    return;
  }

  if (policy === "extend") {
    // 잠든 시간만큼 뒤로 밀어 이어서 진행
    T.endsAt = now + T.plannedMs;
    T.phaseStartedAt = now;
    T.interruptions += 1;
    saveTimer();
    emit("timer:tick", tickDetail(now));
    return;
  }

  // policy === "ask"
  T.pendingGap = {
    gapMs: overshoot,
    phase: T.phase,
    endsAt: T.endsAt,
    plannedMs: T.plannedMs,
    startedAt: T.phaseStartedAt,
    setIndex: T.setIndex,
    sessionId: T.sessionId,
    interruptions: T.interruptions,
    remainingAtSleep: 0,
  };
  T.endsAt = null;
  T.remainingMs = 0;
  setStatus("interrupted");
  saveTimer();
  emit("timer:gap", { ...T.pendingGap });
}

// ── 틱 루프 ──────────────────────────────────────────────────────────────────

function tick() {
  const T = state.timer;
  if (T.status !== "running" || T.endsAt == null) return;
  const now = Date.now();
  T.lastTickAt = now;

  if (now >= T.endsAt) {
    handleOverrun(now);
    return;
  }
  T.remainingMs = T.endsAt - now;

  // ★ 종료 30초 전 조용한 예고. "휴식 침식"(자동 시작된 휴식의 앞부분을 마무리에
  //   쓰는 것)의 직접 해법이다 — 문장 중간에 잘리지 않게 미리 알린다.
  if (!T.warnedSoon && T.remainingMs <= PREWARN_MS && T.plannedMs > PREWARN_MS * 2) {
    T.warnedSoon = true;
    emit("timer:ending-soon", { phase: T.phase, remainingMs: T.remainingMs });
  }

  emit("timer:tick", tickDetail(now));
}

function startLoop() {
  if (tickHandle != null) return;
  tickHandle = setInterval(tick, TICK_MS);
}

export function stopLoop() {
  if (tickHandle != null) {
    clearInterval(tickHandle);
    tickHandle = null;
  }
}

// ── 초기화 / 복원 ────────────────────────────────────────────────────────────

export function restoreTimer() {
  const saved = lsGet(LS.TIMER, null);
  const T = state.timer;

  if (!saved || typeof saved !== "object") {
    setPhase("focus", { autoStart: false });
    return;
  }

  T.phase = saved.phase === "focus" ? "focus" : "break";   // 옛 short/long_break 흡수
  T.setIndex = Math.min(saved.setIndex ?? 0, setCount() - 1);
  T.completedFocus = saved.completedFocus ?? 0;
  T.plannedMs = saved.plannedMs ?? phaseSeconds(T.phase) * 1000;
  const settingsMs = phaseSeconds(T.phase) * 1000;
  T.sessionId = saved.sessionId ?? null;
  T.interruptions = saved.interruptions ?? 0;
  T.phaseStartedAt = saved.phaseStartedAt ?? null;

  if (saved.status === "running" && typeof saved.endsAt === "number") {
    T.endsAt = saved.endsAt;
    T.remainingMs = Math.max(0, saved.endsAt - Date.now());
    setStatus("running");
    emit("timer:phase-start", { phase: T.phase, plannedMs: T.plannedMs, autoStarted: false });
    // 이미 끝났어야 할 시각이면 즉시 판정 (GRACE 이내면 자동 진행, 아니면 복구 모달)
    if (Date.now() >= T.endsAt) handleOverrun(Date.now());
    else emit("timer:tick", tickDetail());
    startLoop();
    return;
  }

  // ★ 돌고 있지 않은 타이머는 **현재 설정**을 따른다. 저장된 plannedMs 를 그대로 쓰면
  //   설정에서 시간을 바꾼 뒤 새로고침했을 때 옛 길이가 그대로 남는다.
  //   (돌고 있는 타이머는 위에서 이미 반환했다 — 진행 중인 세션의 길이를 소급해
  //    바꾸면 안 되기 때문이다.)
  T.endsAt = null;
  if (saved.status === "paused") {
    T.plannedMs = settingsMs;
    // 일시정지 중이면 남은 시간은 지키되 새 구간 길이를 넘지 않게 자른다
    T.remainingMs = Math.min(saved.remainingMs ?? settingsMs, settingsMs);
    setStatus("paused");
  } else {
    T.plannedMs = settingsMs;
    T.remainingMs = settingsMs;
    setStatus("idle");
  }
  emit("timer:phase-start", { phase: T.phase, plannedMs: T.plannedMs, autoStarted: false });
  emit("timer:tick", tickDetail());
}

export function initTimer() {
  restoreTimer();

  // 탭이 다시 보이게 되면 즉시 재계산한다. 백그라운드에서 스로틀된 동안 지나간
  // 마감을 곧바로 처리하기 위함 — 사용자가 화면을 보는 순간 이미 정확해야 한다.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") tick();
  });
  window.addEventListener("focus", tick);
  window.addEventListener("beforeunload", saveTimer);
}

/** 테스트/디버그용 — 저장된 타이머 상태를 지운다. */
export function clearSavedTimer() {
  lsRemove(LS.TIMER);
}
