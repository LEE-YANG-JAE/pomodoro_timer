// 진입점 — 모듈 배선 + 초기화 순서.

import { PHASE_LABEL, on, state } from "./modules/state.js";
import { $, fmtDuration, flashUndoToast, initTheme, showToast } from "./modules/utils.js";
import {
  applyDurationChange, extendPhase, initTimer, pauseTimer, phaseSeconds,
  resetPhase, skipPhase, startTimer, toggleTimer, undoSkip,
} from "./modules/timer.js";
import {
  ensureAudio, initAudioLifecycle, nextTrack, pauseMusic, prevTrack, resumeMusic,
  runPhaseTransition, playPlaylist, stopMusic, toggleMute, clearFailures,
  effectiveVolumes, setDeviceVolume, syncNoise, stopNoise, setNoiseVolume, previewNoise,
  toggleMusicPause, isMusicPaused, setShuffle, isShuffle, isAudioReady,
  initAudioPersistence, restorePlayback, saveAudioState, toggleNoiseNow,
} from "./modules/audio.js";
import {
  acquireWakeLock, initNotify, notifyPhase, releaseWakeLock, requestNotifyPermission,
  updateFavicon, updateMediaSession, updateTitle,
} from "./modules/notify.js";
import {
  announce, initTimerView, openRecoveryModal, openShortcutsHelp,
  renderCycleDots, renderNowPlaying, renderPhase, renderTimer, setFocusMode,
  switchView, toggleFocusMode,
} from "./modules/ui.js";
import {
  flushQueue, initRecordsView, initStatsSync, loadSessionList, loadStats,
  localApplySession, recordSession, renderStatsView,
} from "./modules/stats.js";
import {
  initSettingsView, loadSettings, renderNoiseControls, renderSetList, renderSettingsView,
  updateSetting,
} from "./modules/settings.js";
import {
  initPlaylistView, loadTracks, pollDownloadStatus, renderCredits, renderNowPlayingMini,
  renderPlaylistView, updateNowPlayingRow,
} from "./modules/playlist.js";
import {
  initTasksView, loadTasks, localApplyTaskPomodoro, renderActiveTaskLabel,
  renderTaskList, withActiveTask,
} from "./modules/tasks.js";
import { initSearchView } from "./modules/search.js";
import { initShortcuts } from "./modules/shortcuts.js";

window.addEventListener("DOMContentLoaded", async () => {
  initTheme();
  initTimerView();
  initNotify();
  initAudioLifecycle();
  initStatsSync();
  initRecordsView();
  initAudioUnlock();

  // 1) 설정이 먼저다 — 구간 길이를 모르면 타이머를 복원할 수 없다.
  await loadSettings();
  initSettingsView();

  // 2) ★ 이벤트 배선이 initTimer() 보다 먼저다.
  //    initTimer() 는 복원 즉시 timer:phase-start / timer:tick 을 쏘는데, 그때 구독자가
  //    없으면 첫 화면이 그려지지 않는다 (사이클 점이 비고, 저장된 구간 길이가 반영되지 않음).
  wireEvents();
  wireControls();

  // 3) 타이머 복원. 여기서 중단 복구 모달이 즉시 뜰 수 있다.
  initTimer();

  // 4) 나머지는 타이머를 막지 않고 병렬로.
  initAudioPersistence();
  loadTracks().then(() => { renderFirstRun(); return resumePlaybackIfRunning(); });
  loadStats();
  pollDownloadStatus();
  initPlaylistView();
  initSearchView();
  initTasksView();
  loadTasks().then(() => {
    renderTaskList();
    renderActiveTaskLabel();
  });
  flushQueue();

  switchView("timer");

  window.__pomoReady = true;          // 스모크 테스트 훅
});

/**
 * ★ 브라우저 자동재생 정책을 배너 없이 넘긴다.
 *
 * 브라우저는 첫 사용자 제스처 전까지 오디오를 막는다. 예전에는 "소리가 차단되었습니다 —
 * 소리 켜기" 배너를 띄웠지만, 그건 사용자에게 브라우저 정책을 대신 설명하고 클릭을
 * 하나 더 요구하는 것이다. 대신 **아무 조작이나 한 번 일어나면 조용히 열고 이어서 재생한다.**
 * 대부분의 경우 시작 버튼 클릭이 그 제스처가 되므로 사용자는 아무것도 눈치채지 못한다.
 */
function initAudioUnlock() {
  const unlock = async () => {
    await ensureAudio();
    if (!isAudioReady()) return;              // 아직 못 열었으면 다음 조작에서 다시 시도
    detach();
    if (state.timer.status !== "running") return;
    if (state.settings.audio.silent_mode) return;
    const r = await restorePlayback();        // 끊긴 지점부터 — 처음부터 다시 틀지 않는다
    if (r !== "resumed") playPlaylist(state.timer.phase);
    syncNoise();
    renderPlayerControls();
  };
  const detach = () => {
    for (const ev of ["pointerdown", "keydown", "touchstart"]) {
      document.removeEventListener(ev, unlock);
    }
  };
  for (const ev of ["pointerdown", "keydown", "touchstart"]) {
    document.addEventListener(ev, unlock);
  }
}

/**
 * 새로고침 전에 재생 중이었다면 그 곡의 그 위치부터 이어서 재생한다.
 *
 * 타이머가 돌고 있을 때만 되살린다 — 멈춰 있는데 음악만 흐르면 이상하다.
 * 브라우저 자동재생 정책에 막히면 "소리 켜기" 배너를 띄운다 (한 번 누르면 이어진다).
 */
async function resumePlaybackIfRunning() {
  if (state.timer.status !== "running") return;
  if (state.settings.audio.silent_mode) return;
  const result = await restorePlayback();
  if (result === "resumed") {
    syncNoise();
    renderPlayerControls();
  }
  // "blocked" 여도 아무것도 띄우지 않는다 — initAudioUnlock() 이 다음 조작에서
  // 자동으로 열고 이어서 재생한다.
}

// ── 휴식 시작 유예 ──────────────────────────────────────────────────────────
// 자동 시작이 켜져 있어도 곧바로 넘기지 않는다. 0:00 에 휴식이 시작되면 그 앞부분을
// 하던 일 마무리에 쓰게 되어 5분 휴식이 실제로는 2분이 된다.
let holdTimer = null;

function clearHold() {
  clearInterval(holdTimer);
  holdTimer = null;
  const bar = $("#hold-bar");
  if (bar) bar.hidden = true;
}

function startHold(seconds) {
  clearHold();
  const bar = $("#hold-bar");
  const label = $("#hold-label");
  if (!bar || !label) { startTimer(); return; }

  let left = seconds;
  const phaseLabel = PHASE_LABEL[state.timer.phase] ?? state.timer.phase;
  const tick = () => {
    label.textContent = `${left}초 후 ${phaseLabel} 시작`;
    if (left <= 0) { clearHold(); startTimer(); return; }
    left -= 1;
  };
  bar.hidden = false;
  tick();
  holdTimer = setInterval(tick, 1000);
}

/** 음원이 없을 때만 보이는 인라인 안내. 마법사를 띄우지 않는다. */
function renderFirstRun() {
  const node = $("#first-run");
  if (!node) return;
  const anyReady = state.tracks.some((t) => t.ready);
  node.hidden = anyReady || state.settings.audio.silent_mode;
}

function renderExtendButton() {
  const btn = $("#btn-extend");
  if (btn) btn.hidden = state.timer.status !== "running";
}

/** 재생/일시정지·순서 버튼의 모양을 현재 상태에 맞춘다. */
function renderPlayerControls() {
  const pause = $("#btn-music-toggle");
  if (pause) {
    const paused = isMusicPaused();
    pause.textContent = paused ? "▶" : "⏸";
    pause.setAttribute("aria-pressed", paused ? "true" : "false");
    pause.setAttribute("aria-label", paused ? "음악 재생 (P)" : "음악 일시정지 (P)");
  }
  const shuffle = $("#btn-shuffle");
  if (shuffle) {
    const on = isShuffle();
    shuffle.textContent = on ? "🔀" : "🔁";
    shuffle.setAttribute("aria-pressed", on ? "true" : "false");
    shuffle.title = on ? "무작위 재생 중" : "순서대로 재생 중";
  }
}

// ── 이벤트 버스 배선 ─────────────────────────────────────────────────────────

function wireEvents() {
  on("timer:tick", (d) => {
    renderTimer(d);
    updateTitle(d.remainingMs, d.phase, d.status);
    updateFavicon(d.progress, d.phase, d.status);
  });

  on("timer:phase-start", async (d) => {
    clearFailures();
    renderPhase(d.phase);
    if (d.phase === "focus") {
      acquireWakeLock();
      if (state.settings.ui.auto_focus_mode && state.timer.status === "running") {
        setFocusMode(true);
      }
    } else {
      releaseWakeLock();
      setFocusMode(false);
    }
    syncNoise(d.phase);
    renderSetList();          // 현재 세트 강조를 갱신
    renderActiveTaskLabel();
    const total = state.settings.timer.sets?.length ?? 1;
    announce(`${d.setIndex + 1}세트 ${PHASE_LABEL[d.phase]} ` +
             `${fmtDuration(d.plannedMs / 1000)} 시작 (전체 ${total}세트)`);
    flushQueue();          // 매 구간 시작마다 밀린 기록을 다시 시도
  });

  on("timer:phase-end", (d) => {
    if (d.session) {
      // ★ 여기가 작업과 타이머의 유일한 이음매다. timer.js 는 작업을 모른다.
      const session = withActiveTask(d.session);
      localApplySession(session);        // 낙관적 — 끝내자마자 숫자가 오른다
      localApplyTaskPomodoro(session);   // 낙관적 2/4 → 3/4
      recordSession(session);
      // ★ 달력에서 과거 날짜를 보고 있었다면 그 선택을 존중한다 — 오늘로 되돌리지 않는다.
      if (state.ui.view === "records") loadSessionList($("#session-date")?.value || undefined);
    }
    notifyPhase(d.phase, d.next);
    announce(`${PHASE_LABEL[d.phase]} 시간이 끝났습니다. ${PHASE_LABEL[d.next]}을 시작합니다.`);
    // 절전에서 한참 뒤에 깨어난 경우까지 차임을 울리면 뜬금없다 — 30초 이내일 때만.
    runPhaseTransition({ from: d.phase, to: d.next, playChimeNow: d.lateMs < 30_000 });
  });

  on("timer:status", (d) => {
    if (d.status === "running") {
      resumeMusic();
      if (!state.audio.current) playPlaylist(state.timer.phase);
      syncNoise();
      // ★ phase-start 는 구간이 "바뀔 때"만 잠금을 다룬다. 일시정지→재개(스페이스바 등)처럼
      //   구간은 그대로인데 status 만 바뀌는 경로에서도 대칭으로 걸어준다 — 탭 전환으로
      //   브라우저가 잠금을 자동 해제한 뒤 재개해도 다시 걸리게. acquire/release 는 멱등.
      if (state.timer.phase === "focus") acquireWakeLock(); else releaseWakeLock();
    } else if (d.status === "paused" || d.status === "interrupted") {
      pauseMusic();
      stopNoise();          // 멈춘 동안 소음만 계속 나면 이상하다
      setFocusMode(false);
      releaseWakeLock();
    }
    if (d.status === "idle") {
      stopMusic();
      stopNoise();
      releaseWakeLock();
    }
  });

  on("timer:gap", (gap) => openRecoveryModal(gap));
  on("timer:hold", ({ seconds }) => startHold(seconds));
  // 사용자가 직접 시작하거나 구간을 바꾸면 유예는 의미가 없다
  on("timer:phase-start", () => clearHold());
  on("timer:status", (d) => { if (d.status === "running") clearHold(); });

  on("timer:status", () => { renderExtendButton(); });
  on("timer:extended", ({ seconds }) =>
    showToast(`${Math.round(seconds / 60)}분 연장했습니다.`, { ms: 1600 }));

  // ★ 종료 30초 전 조용한 예고 — 문장 중간에 잘리지 않게. 토스트가 아니라 링 색만 바꾼다.
  on("timer:ending-soon", () => {
    document.body.dataset.endingSoon = "1";
    setTimeout(() => { delete document.body.dataset.endingSoon; }, 31_000);
  });

  on("tasks:changed", () => { renderActiveTaskLabel(); });

  on("timer:plan-done", () => {
    showToast("계획한 세트를 모두 마쳤습니다. 수고하셨어요!", { kind: "ok", ms: 6000 });
    announce("계획한 세트를 모두 마쳤습니다.");
  });

  on("audio:paused", renderPlayerControls);
  on("audio:shuffle", renderPlayerControls);

  on("timer:skipped", ({ phase }) => {
    flashUndoToast(`${PHASE_LABEL[phase]}을(를) 건너뛰었습니다. 기록되지 않았습니다.`, () => {
      if (undoSkip()) announce("건너뛰기를 되돌렸습니다.");
    });
  });

  on("timer:reset", () => {
    // resetPhase() 는 phase-start 를 쏘지 않으므로 홀드바를 여기서 직접 정리한다.
    clearHold();
    announce("현재 구간을 처음으로 되돌렸습니다.");
  });

  // 차단돼도 배너를 띄우지 않는다 — 사용자에게 허락을 구하는 대신
  // 다음 조작에서 조용히 열고 이어서 재생한다 (initAudioUnlock).

  on("audio:empty", () => {
    // 음원이 없어도 타이머는 정상이다. 조용히 한 번만 안내한다.
    showToast("재생할 음원이 없습니다. 음악 탭에서 내려받거나 추가할 수 있습니다.", { kind: "info" });
  });

  on("audio:track", (track) => {
    renderNowPlaying();
    renderPlayerControls();
    updateMediaSession(track, state.timer.phase);
    renderNowPlayingMini();
    updateNowPlayingRow();
  });

  document.addEventListener("view:changed", (e) => {
    const v = e.detail.view;
    if (v === "records") { loadStats(); loadSessionList($("#session-date")?.value || undefined); }
    if (v === "music") { renderPlaylistView(); renderCredits(); renderNowPlayingMini(); }
    if (v === "settings") renderSettingsView();
    if (v === "timer") renderNowPlaying();
  });

  window.addEventListener("online", flushQueue);
}

// ── 컨트롤 ───────────────────────────────────────────────────────────────────

function wireControls() {
  // ★ 시작 버튼이 "제스처를 필요로 하는 모든 것"을 소비하는 유일한 지점이다.
  //   여기서 AudioContext 를 열고 알림 권한을 받아 두면, 이후의 자동 위상 전환은
  //   사용자 조작 없이도 소리가 난다.
  $("#btn-primary")?.addEventListener("click", async () => {
    if (state.timer.status === "running") {
      pauseTimer();
      return;
    }
    await ensureAudio();
    if (state.settings.ui.notifications &&
        "Notification" in window && Notification.permission === "default") {
      await requestNotifyPermission();
    }
    startTimer();
    acquireWakeLock();
    if (!state.settings.audio.silent_mode) playPlaylist(state.timer.phase);
    syncNoise();
  });

  // 소음 빠른 컨트롤
  $("#btn-noise")?.addEventListener("click", async () => {
    // ensureAudio 는 toggleNoiseNow 안에서 — 이 클릭이 사용자 제스처다
    const result = await toggleNoiseNow();
    updateSetting("audio", "noise_enabled", state.settings.audio.noise_enabled,
                  { immediate: true });
    renderNoiseControls();
    const mirror = $("#set-noise-enabled");
    if (mirror) mirror.checked = state.settings.audio.noise_enabled;
    if (result === "on") showToast("백색 소음을 켰습니다.", { ms: 1400 });
    else if (result === "off") showToast("백색 소음을 껐습니다.", { ms: 1400 });
    else {
      showToast("소음을 켰습니다. 지금은 휴식 구간이라 집중이 시작되면 재생됩니다.",
                { ms: 3500 });
    }
  });

  $("#noise-type-quick")?.addEventListener("change", async (e) => {
    const v = e.target.value;
    updateSetting("audio", "noise_type", v, { immediate: true });
    const mirror = $("#set-noise-type");
    if (mirror) mirror.value = v;
    await previewNoise(v);
  });

  const nvol = $("#noise-volume");
  if (nvol) {
    nvol.value = effectiveVolumes().noise;
    nvol.addEventListener("input", () => {
      setNoiseVolume(Number(nvol.value));
      const mirror = $("#set-noise-vol");
      if (mirror) mirror.value = nvol.value;
    });
    nvol.addEventListener("change", () =>
      updateSetting("audio", "noise_volume", Number(nvol.value), { immediate: true }));
  }

  $("#btn-hold-now")?.addEventListener("click", () => { clearHold(); startTimer(); });

  $("#btn-first-download")?.addEventListener("click", async () => {
    const { startDownload } = await import("./modules/playlist.js");
    await startDownload("core");
    $("#first-run").hidden = true;
  });
  $("#btn-first-noise")?.addEventListener("click", () => {
    $("#btn-noise")?.click();
    $("#first-run").hidden = true;
  });

  $("#btn-extend")?.addEventListener("click", () => extendPhase(300));

  $("#btn-skip")?.addEventListener("click", () => skipPhase());
  $("#btn-reset")?.addEventListener("click", () => resetPhase());
  $("#btn-focus-mode")?.addEventListener("click", () => toggleFocusMode());
  $("#btn-exit-focus")?.addEventListener("click", () => setFocusMode(false));

  // ★ 집중 모드는 브라우저 UI 까지 지운다. Esc 로 나가는 건 브라우저가 무료로 준다.
  //   전체화면이 거부돼도(권한·정책) 크롬 디밍은 그대로 동작한다 — 실패는 조용히 넘긴다.
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement && state.ui.focusMode) setFocusMode(false);
  });

  $("#btn-music-toggle")?.addEventListener("click", async () => {
    await ensureAudio();
    const paused = await toggleMusicPause();
    renderPlayerControls();
    showToast(paused ? "음악을 멈췄습니다." : "음악을 다시 재생합니다.", { ms: 1400 });
  });

  $("#btn-shuffle")?.addEventListener("click", () => {
    const on = setShuffle(!isShuffle());
    updateSetting("audio", "shuffle_focus", on, { immediate: true });
    updateSetting("audio", "shuffle_break", on, { immediate: true });
    renderPlayerControls();
    showToast(on ? "무작위 재생" : "순서대로 재생", { ms: 1400 });
  });

  $("#btn-prev-track")?.addEventListener("click", () => prevTrack());
  $("#btn-next-track")?.addEventListener("click", () => nextTrack());
  $("#btn-mute")?.addEventListener("click", (e) => {
    const muted = toggleMute();
    e.currentTarget.setAttribute("aria-pressed", muted ? "true" : "false");
    e.currentTarget.textContent = muted ? "🔇" : "🔊";
  });

  const vol = $("#music-volume");
  if (vol) {
    vol.value = effectiveVolumes().music;
    vol.addEventListener("input", () => {
      setDeviceVolume("music", Number(vol.value));
      const mirror = $("#set-music-vol");
      if (mirror) mirror.value = vol.value;
    });
  }

  for (const tab of document.querySelectorAll("[data-view-tab]")) {
    tab.addEventListener("click", () => switchView(tab.dataset.viewTab));
    tab.addEventListener("keydown", (e) => {
      const tabs = Array.from(document.querySelectorAll("[data-view-tab]"));
      const i = tabs.indexOf(tab);
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
        next.focus();
        switchView(next.dataset.viewTab);
      }
    });
  }

  renderPlayerControls();
  renderExtendButton();
  $("#btn-help")?.addEventListener("click", () => openShortcutsHelp());

  initShortcuts({
    toggle: () => toggleTimer(),
    skip: () => skipPhase(),
    reset: () => resetPhase(),
    toggleFocusMode: () => toggleFocusMode(),
    setFocusMode: (v) => setFocusMode(v),
    toggleMute: () => {
      const muted = toggleMute();
      showToast(muted ? "음소거" : "음소거 해제", { ms: 1200 });
    },
    nextTrack: () => nextTrack(),
    toggleNoise: () => $("#btn-noise")?.click(),
    extend: () => { if (state.timer.status === "running") extendPhase(300); },
    toggleMusic: () => $("#btn-music-toggle")?.click(),
    toggleShuffle: () => $("#btn-shuffle")?.click(),
    switchView: (v) => switchView(v),
    help: () => openShortcutsHelp(),
  });
}

// 스모크 테스트에서 오디오 그래프 상태를 들여다볼 수 있게 노출한다.
Object.defineProperty(window, "__pomoAudio", {
  get: () => ({
    ctxState: state.audio.ctxState,
    unlocked: state.audio.unlocked,
    blocked: state.audio.blocked,
    current: state.audio.current,
    order: state.audio.order,
  }),
});
Object.defineProperty(window, "__pomoState", { get: () => state });
