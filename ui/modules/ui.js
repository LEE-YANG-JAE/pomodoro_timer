// 화면 렌더링 — 타이머 뷰 · 집중 모드 · 복구 모달 · 접근성 안내.

import { PHASE_LABEL, state } from "./state.js";
import {
  $, $$, createLevelVisualizer, el, fmtClock, fmtClockSr, fmtDuration, openModal, closeModal,
} from "./utils.js";
import { resolveGap } from "./timer.js";
import { getLevels } from "./audio.js";

const nowPlayingViz = createLevelVisualizer("tv-visualizer", getLevels);

// 진행 링의 원주 (r=130 → 2πr)
const RING_R = 130;
const RING_C = 2 * Math.PI * RING_R;

let lastSrAt = 0;
let lastLabelAt = 0;
const announced = new Set();   // 이번 구간에서 이미 알린 마일스톤

// ── 스크린리더 안내 ──────────────────────────────────────────────────────────

/**
 * ★ 카운트다운 자체에는 aria-live 를 붙이지 않는다. 매초 읽히면 스크린리더가
 *   쓸모없어진다. 대신 의미 있는 순간에만 이 함수로 문장을 내보낸다.
 */
export function announce(text) {
  const node = $("#timer-sr");
  if (!node || !text) return;
  // 같은 문자열을 연속으로 넣으면 일부 리더가 읽지 않는다 — 제로폭 문자를 토글한다
  const marker = state.ui.srLast === text ? "​" : "";
  state.ui.srLast = text;
  node.textContent = text + marker;
  lastSrAt = Date.now();
}

function announceMilestones(remainingMs) {
  const mins = Math.round(remainingMs / 60000);
  for (const m of [10, 5, 1]) {
    if (mins === m && !announced.has(m) && remainingMs % 60000 < 1500) {
      announced.add(m);
      announce(`${m}분 남음`);
      return;
    }
  }
}

// ── 타이머 뷰 ────────────────────────────────────────────────────────────────

export function initTimerView() {
  const ring = $("#ring-progress");
  if (ring) {
    ring.setAttribute("stroke-dasharray", String(RING_C));
    ring.setAttribute("stroke-dashoffset", String(RING_C));
  }
}

export function renderTimer(d) {
  const countdown = $("#countdown");
  if (countdown) {
    const text = fmtClock(d.remainingMs);
    if (countdown.textContent !== text) countdown.textContent = text;
    // aria-label 은 15초마다만 갱신 — 포커스했을 때 정확하면 충분하다
    const now = Date.now();
    if (now - lastLabelAt > 15000) {
      lastLabelAt = now;
      countdown.setAttribute("aria-label", `${PHASE_LABEL[d.phase]} ${fmtClockSr(d.remainingMs)}`);
    }
  }

  const ring = $("#ring-progress");
  if (ring) ring.setAttribute("stroke-dashoffset", String(RING_C * (1 - d.progress)));

  document.body.dataset.status = d.status;
  document.body.dataset.phase = d.phase;

  const primary = $("#btn-primary");
  if (primary) {
    const running = d.status === "running";
    primary.textContent = running ? "일시정지" : (d.status === "paused" ? "이어서" : "시작");
    primary.setAttribute("aria-label", running ? "일시정지" : "시작");
  }

  if (d.status === "running") announceMilestones(d.remainingMs);
}

export function renderPhase(phase) {
  announced.clear();
  const label = $("#phase-label");
  if (label) label.textContent = PHASE_LABEL[phase] ?? phase;
  renderCycleDots();
}

/**
 * 세트 진행 표시. 점 하나 = 세트 하나.
 * 지나온 세트는 채우고, 현재 세트는 테두리를 강조한다.
 */
export function renderCycleDots() {
  const host = $("#cycle-dots");
  if (!host) return;
  const list = state.settings.timer.sets ?? [];
  const total = Math.max(1, list.length);
  const cur = Math.min(state.timer.setIndex ?? 0, total - 1);

  host.replaceChildren(
    ...Array.from({ length: total }, (_, i) => {
      const cls = i < cur ? "dot dot-done" : (i === cur ? "dot dot-current" : "dot");
      const set = list[i];
      const dot = el("span", { class: cls, "aria-hidden": "true" });
      if (set) {
        dot.title =
          `${i + 1}세트 · 집중 ${Math.round(set.focus_seconds / 60)}분 / ` +
          `휴식 ${Math.round(set.break_seconds / 60)}분`;
      }
      return dot;
    }),
  );
  host.setAttribute("aria-label", `${total}세트 중 ${cur + 1}번째 세트`);
}

export function renderNowPlaying() {
  const host = $("#now-playing");
  if (!host) return;
  const t = state.audio.current;
  if (!t) {
    host.textContent = state.settings.audio.silent_mode ? "무음 모드" : "재생 중인 곡 없음";
    host.removeAttribute("title");
    nowPlayingViz.stop();
    return;
  }
  const who = t.performer_ko || t.composer_ko || "";
  host.textContent = who ? `${t.title_ko} — ${who}` : t.title_ko;
  host.title = `${t.title_orig ?? t.title_ko}${who ? ` / ${who}` : ""}`;
  nowPlayingViz.start();
}

// ── 집중 모드 ────────────────────────────────────────────────────────────────

/** 집중 세션 중에는 앱 자신이 방해 요소가 되지 않도록 UI 를 최소화한다. */
export function setFocusMode(on) {
  state.ui.focusMode = Boolean(on);
  document.body.dataset.focus = on ? "on" : "";
  const btn = $("#btn-focus-mode");
  if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false");

  // ★ 진짜 전체화면 — 탭·주소창까지 사라져야 앱 자신이 방해가 되지 않는다.
  //   거부되더라도(사용자 제스처 없음 등) 크롬 디밍은 이미 적용됐으므로 조용히 넘긴다.
  try {
    if (on && !document.fullscreenElement) {
      document.documentElement.requestFullscreen?.().catch(() => {});
    } else if (!on && document.fullscreenElement) {
      document.exitFullscreen?.().catch(() => {});
    }
  } catch {
    /* 전체화면은 부가 기능이다 */
  }
}

export function toggleFocusMode() {
  setFocusMode(!state.ui.focusMode);
}

// ── 뷰 전환 ──────────────────────────────────────────────────────────────────

export function switchView(view) {
  state.ui.view = view;
  for (const panel of $$("[data-view-panel]")) {
    panel.hidden = panel.dataset.viewPanel !== view;
  }
  for (const tab of $$("[data-view-tab]")) {
    const active = tab.dataset.viewTab === view;
    tab.setAttribute("aria-selected", active ? "true" : "false");
    tab.tabIndex = active ? 0 : -1;
  }
  document.dispatchEvent(new CustomEvent("view:changed", { detail: { view } }));
}

// ── 중단 복구 모달 ───────────────────────────────────────────────────────────

/**
 * 노트북 절전 등으로 오래 끊겼을 때. persistent 로 열어 백드롭 클릭으로 닫히지 않게 한다
 * — 실수로 닫으면 방금 한 집중 세션의 기록이 사라진다.
 */
export function openRecoveryModal(gap) {
  const mins = Math.round(gap.gapMs / 60000);
  const gapText = mins >= 1 ? `약 ${fmtDuration(mins * 60)}` : `약 ${Math.round(gap.gapMs / 1000)}초`;
  const phaseLabel = PHASE_LABEL[gap.phase] ?? gap.phase;

  announce("세션이 중단되었습니다. 복구 창이 열렸습니다.");

  openModal({
    title: "세션이 중단되었습니다",
    persistent: true,
    body: el("div", {},
      el("p", {
        text: `${phaseLabel} 타이머가 끝난 뒤 ${gapText} 동안 화면이 꺼져 있었습니다. ` +
              `절전 모드였거나 탭이 멈춰 있었을 수 있습니다.`,
      }),
      el("p", { class: "muted", text: "이 시간을 어떻게 처리할까요?" })),
    actions: [
      {
        label: "기록하지 않고 새로 시작",
        onClick: ({ close }) => { resolveGap("discard"); close(); },
      },
      {
        label: "이어서 계속",
        onClick: ({ close }) => { resolveGap("continue"); close(); },
      },
      {
        label: "완료로 기록",
        variant: "primary",
        onClick: ({ close }) => { resolveGap("record"); close(); },
      },
    ],
  });
}

// ── 도움말 ───────────────────────────────────────────────────────────────────

export function openShortcutsHelp() {
  const rows = [
    ["Space", "시작 / 일시정지"],
    ["S", "현재 구간 건너뛰기"],
    ["E", "5분 연장"],
    ["R", "현재 구간 처음으로"],
    ["F", "집중 모드 켜기 / 끄기"],
    ["M", "음소거"],
    ["N", "다음 곡"],
    ["P", "음악 일시정지 / 재생"],
    ["O", "재생 순서 (순서대로 / 무작위)"],
    ["W", "백색 소음 켜기 / 끄기"],
    ["Esc", "집중 모드 해제 / 창 닫기"],
    ["1 2 3 4", "타이머 / 기록 / 음악 / 설정"],
    ["?", "이 도움말"],
  ];
  openModal({
    title: "키보드 단축키",
    body: el("dl", { class: "shortcut-list" },
      ...rows.flatMap(([k, v]) => [
        el("dt", {}, el("kbd", { text: k })),
        el("dd", { text: v }),
      ])),
    actions: [{ label: "닫기", variant: "primary", onClick: ({ close }) => close() }],
  });
}

export { closeModal };
