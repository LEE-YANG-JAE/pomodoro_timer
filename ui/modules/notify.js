// 탭 제목 · 동적 파비콘 · 웹 알림 · 화면 켜짐 유지(Wake Lock) · MediaSession.
//
// 전부 "있으면 좋은" 기능이다. 하나라도 없거나 거부돼도 타이머는 정상 동작해야 한다.

import { PHASE_LABEL, state } from "./state.js";
import { fmtClock } from "./utils.js";

let wakeLock = null;
let faviconEl = null;
let faviconCanvas = null;
let lastFaviconKey = "";
const BASE_TITLE = "뽀모도로 타이머";

/**
 * 기능 지원 여부.
 * ★ 알림과 Wake Lock 은 secure context 에서만 동작한다. LAN 으로 http:// 접속하면
 *   둘 다 쓸 수 없으므로 설정 화면이 토글을 비활성화하고 이유를 설명해야 한다.
 */
export function notifySupport() {
  return {
    secureContext: window.isSecureContext === true,
    notification: "Notification" in window && window.isSecureContext === true,
    wakeLock: "wakeLock" in navigator && window.isSecureContext === true,
    mediaSession: "mediaSession" in navigator,
  };
}

// ── 탭 제목 ──────────────────────────────────────────────────────────────────

export function updateTitle(remainingMs, phase, status) {
  if (status === "idle") {
    document.title = BASE_TITLE;
    return;
  }
  const label = PHASE_LABEL[phase] ?? phase;
  const paused = status === "paused" ? " (일시정지)" : "";
  document.title = `${fmtClock(remainingMs)} · ${label}${paused}`;
}

// ── 동적 파비콘 ──────────────────────────────────────────────────────────────

function ensureFavicon() {
  if (faviconEl) return true;
  faviconEl = document.querySelector('link[rel="icon"]');
  if (!faviconEl) {
    faviconEl = document.createElement("link");
    faviconEl.rel = "icon";
    document.head.append(faviconEl);
  }
  faviconCanvas = document.createElement("canvas");
  faviconCanvas.width = 64;
  faviconCanvas.height = 64;
  return true;
}

const PHASE_COLOR = {
  focus: "#e05252",
  break: "#3f9e6a",
};

export function updateFavicon(progress, phase, status) {
  if (!state.settings.ui.dynamic_favicon) return;
  try {
    ensureFavicon();
    // 1% 단위로만 다시 그린다 — 매 틱 캔버스를 그리는 건 낭비다
    const key = `${phase}:${status}:${Math.round(progress * 100)}`;
    if (key === lastFaviconKey) return;
    lastFaviconKey = key;

    const c = faviconCanvas.getContext("2d");
    const S = 64;
    const R = 26;
    c.clearRect(0, 0, S, S);

    c.beginPath();
    c.arc(S / 2, S / 2, R, 0, Math.PI * 2);
    c.strokeStyle = "rgba(140,140,140,0.35)";
    c.lineWidth = 9;
    c.stroke();

    if (status !== "idle") {
      c.beginPath();
      c.arc(S / 2, S / 2, R, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * progress);
      c.strokeStyle = PHASE_COLOR[phase] ?? "#e05252";
      c.lineWidth = 9;
      c.lineCap = "round";
      c.stroke();
    }
    faviconEl.href = faviconCanvas.toDataURL("image/png");
  } catch {
    /* 파비콘은 부가 기능 — 실패해도 무시 */
  }
}

// ── 웹 알림 ──────────────────────────────────────────────────────────────────

export async function requestNotifyPermission() {
  if (!notifySupport().notification) return "unsupported";
  try {
    return await Notification.requestPermission();
  } catch {
    return "denied";
  }
}

export function notifyPhase(endedPhase, nextPhase) {
  if (!state.settings.ui.notifications) return;
  if (!notifySupport().notification || Notification.permission !== "granted") return;
  const ended = PHASE_LABEL[endedPhase] ?? endedPhase;
  const next = PHASE_LABEL[nextPhase] ?? nextPhase;
  try {
    const n = new Notification(`${ended} 시간이 끝났습니다`, {
      body: `이제 ${next} 시간입니다.`,
      tag: "pomodoro-phase",      // 알림이 쌓이지 않게 같은 태그로 교체
      renotify: true,
    });
    n.addEventListener("click", () => {
      window.focus();
      n.close();
    });
  } catch {
    /* 무시 */
  }
}

// ── Wake Lock ────────────────────────────────────────────────────────────────

export async function acquireWakeLock() {
  if (!state.settings.ui.wake_lock || !notifySupport().wakeLock) return false;
  if (wakeLock) return true;
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener("release", () => { wakeLock = null; });
    return true;
  } catch {
    wakeLock = null;
    return false;
  }
}

export async function releaseWakeLock() {
  try {
    await wakeLock?.release();
  } catch {
    /* 무시 */
  }
  wakeLock = null;
}

// ── MediaSession (OS 미디어 컨트롤 · 헤드셋 버튼) ────────────────────────────

export function updateMediaSession(track, phase) {
  if (!notifySupport().mediaSession) return;
  try {
    if (!track) {
      navigator.mediaSession.metadata = null;
      return;
    }
    navigator.mediaSession.metadata = new MediaMetadata({
      title: track.title_ko || track.title_orig || "",
      artist: track.performer_ko || track.composer_ko || "",
      album: PHASE_LABEL[phase] ?? "",
    });
  } catch {
    /* 무시 */
  }
}

export function initNotify() {
  // 브라우저는 화면이 꺼졌다 돌아오면 Wake Lock 을 해제한다 — 집중 중이면 다시 잡는다.
  document.addEventListener("visibilitychange", () => {
    if (
      document.visibilityState === "visible" &&
      state.timer.status === "running" &&
      state.timer.phase === "focus"
    ) {
      acquireWakeLock();
    }
  });
}
