// 기록 화면 — 오늘 요약 · 최근 7일 막대그래프 · 연속 달성 일수 · 오프라인 큐.
//
// 차트는 차트 라이브러리 없이 인라인 SVG 로 직접 그린다 (npm 의존성 0 원칙).

import { API } from "./api.js";
import { LS, lsGet, lsSet, state } from "./state.js";
import { $, $$, el, fmtDateKo, fmtDuration, fmtTimeKo, localDateStr, showToast } from "./utils.js";

const CHART_DAYS = 7;
const HISTORY_DAYS = 14;
const QUEUE_CAP = 200;
const MAX_TRIES = 8;

let flushTimer = null;

// ── 로드 ─────────────────────────────────────────────────────────────────────

export async function loadStats({ force = false } = {}) {
  if (!force && Date.now() - state.stats.loadedAt < 2000) return;
  try {
    const data = await API.getStats(HISTORY_DAYS);
    state.stats.today = data.today;
    state.stats.series = data.series;
    state.stats.streak = data.streak;
    state.stats.daily_goal = data.daily_goal ?? 8;
    state.stats.loadedAt = Date.now();
    state.stats.stale = false;
    lsSet(LS.STATS, {
      today: data.today, series: data.series, streak: data.streak,
      daily_goal: data.daily_goal,
    });
    flushQueue();
  } catch {
    const cached = lsGet(LS.STATS, null);
    if (cached) Object.assign(state.stats, cached);
    state.stats.stale = true;
  }
  renderStatsView();
}

// ── 세션 기록 ────────────────────────────────────────────────────────────────

/** 서버 왕복을 기다리지 않고 화면을 먼저 갱신한다 — 끝내자마자 숫자가 오르는 게 보여야 한다. */
export function localApplySession(session) {
  if (session.phase !== "focus") return;
  const today = state.stats.today;
  if (session.completed) {
    today.pomodoro_count = (today.pomodoro_count ?? 0) + 1;
    today.focus_seconds = (today.focus_seconds ?? 0) + session.actual_seconds;
    const last = state.stats.series[state.stats.series.length - 1];
    if (last) {
      last.pomodoro_count += 1;
      last.focus_seconds += session.actual_seconds;
    }
    if (!state.stats.streak.includes_today) {
      state.stats.streak.includes_today = true;
      state.stats.streak.current = (state.stats.streak.current ?? 0) + 1;
    }
  } else {
    today.aborted_count = (today.aborted_count ?? 0) + 1;
    today.aborted_seconds = (today.aborted_seconds ?? 0) + session.actual_seconds;
  }
  renderStatsView();
}

export async function recordSession(session) {
  try {
    await API.postSession(session);
    await loadStats({ force: true });   // 연속일수의 진실원은 서버다
  } catch (e) {
    // ★ 4xx 는 재시도하지 않는다 — 잘못된 바디를 영원히 재전송하게 된다.
    //   (408 요청 시간초과, 429 과다요청은 일시적이므로 예외)
    const s = e?.status ?? 0;
    if (s >= 400 && s < 500 && s !== 408 && s !== 429) {
      console.warn("세션이 거부되어 재시도하지 않습니다", e);
      return;
    }
    enqueue(session);
  }
}

// ── 오프라인 큐 ──────────────────────────────────────────────────────────────
//
// 25분을 버틴 사람에게 빨간 오류 토스트를 띄우는 건 사기를 꺾는다. 조용히 큐에 넣고
// 기록 화면에 작은 배지로만 알린다. client_id 가 멱등키라 재전송이 안전하다.

function loadQueue() {
  if (!state.queue.length) state.queue = lsGet(LS.QUEUE, []) || [];
  return state.queue;
}

function saveQueue() {
  lsSet(LS.QUEUE, state.queue);
  renderQueueBadge();
}

function enqueue(session) {
  loadQueue();
  state.queue.push({ ...session, tries: 0, nextAt: Date.now() });
  if (state.queue.length > QUEUE_CAP) state.queue = state.queue.slice(-QUEUE_CAP);
  saveQueue();
}

export async function flushQueue() {
  loadQueue();
  if (!state.queue.length) return;
  const now = Date.now();
  const remaining = [];
  for (const item of state.queue) {
    if (item.nextAt > now || item.tries > MAX_TRIES) {
      remaining.push(item);
      continue;
    }
    const { tries, nextAt, ...session } = item;
    try {
      await API.postSession(session);
    } catch (e) {
      const s = e?.status ?? 0;
      if (s >= 400 && s < 500 && s !== 408 && s !== 429) continue;   // 영구 실패 → 폐기
      remaining.push({
        ...item,
        tries: tries + 1,
        nextAt: now + Math.min(60_000 * 2 ** tries, 15 * 60_000),
      });
    }
  }
  const changed = remaining.length !== state.queue.length;
  state.queue = remaining;
  saveQueue();
  if (changed) loadStats({ force: true });
}

export function renderQueueBadge() {
  const badge = $("#queue-badge");
  if (!badge) return;
  const n = state.queue.length;
  badge.hidden = n === 0;
  const label = $("#queue-badge-label");
  if (label) label.textContent = `기록 저장 대기 중 (${n}건)`;
}

export function initStatsSync() {
  loadQueue();
  renderQueueBadge();
  window.addEventListener("online", flushQueue);
  clearInterval(flushTimer);
  flushTimer = setInterval(flushQueue, 60_000);
  const retry = $("#queue-retry");
  if (retry) {
    retry.addEventListener("click", async () => {
      for (const item of state.queue) item.nextAt = 0;
      await flushQueue();
      showToast(state.queue.length ? "아직 저장하지 못했습니다." : "기록을 저장했습니다.");
    });
  }
}

// ── 렌더 ─────────────────────────────────────────────────────────────────────

export function renderStatsView() {
  const t = state.stats.today ?? {};
  const setText = (sel, text) => { const n = $(sel); if (n) n.textContent = text; };

  setText("#stat-count", String(t.pomodoro_count ?? 0));
  setText("#stat-focus", fmtDuration(t.focus_seconds ?? 0));

  const streak = state.stats.streak ?? {};
  setText("#stat-streak", `연속 ${streak.current ?? 0}일`);
  const note = $("#stat-streak-note");
  if (note) {
    // ★ 오늘 아직 못 한 상태와 0일을 구분해서 보여준다. 오전 9시에 12일 연속이
    //   0 으로 보이면 틀렸고 사기를 꺾는다.
    note.textContent = (streak.current ?? 0) === 0
      ? "오늘 첫 뽀모도로를 시작해 보세요"
      : (streak.includes_today ? `최고 ${streak.best ?? 0}일` : "오늘 아직");
  }

  const goal = state.stats.daily_goal ?? 4;
  const done = t.pomodoro_count ?? 0;
  setText("#stat-goal-tile", `${done} / ${goal}`);
  const bar = $("#goal-bar-fill");
  if (bar) bar.style.width = `${Math.min(100, (done / goal) * 100)}%`;

  const aborted = $("#stat-aborted");
  if (aborted) {
    const n = t.aborted_count ?? 0;
    aborted.hidden = n === 0;
    aborted.textContent = `중단 ${n}회 (${fmtDuration(t.aborted_seconds ?? 0)})`;
  }

  const stale = $("#stats-stale");
  if (stale) stale.hidden = !state.stats.stale;

  renderWeekChart(state.stats.series ?? []);
  renderHistoryTable(state.stats.series ?? []);
  renderQueueBadge();
}

/**
 * 최근 7일 막대그래프 — 인라인 SVG.
 *
 * 접근성: SVG 자체는 aria-hidden 으로 감추고 같은 내용을 문장으로 쓴 설명 요소를 옆에 둔다.
 * 스크린리더가 rect 를 하나씩 읽는 것보다 훨씬 쓸모 있다.
 */
export function renderWeekChart(series) {
  const svg = $("#week-chart");
  if (!svg) return;
  const data = series.slice(-CHART_DAYS);
  const W = 320;
  const H = 132;
  const PAD_B = 22;
  const gap = 8;
  const bw = data.length ? (W - gap * (data.length - 1)) / data.length : W;
  const max = Math.max(1, ...data.map((d) => d.pomodoro_count));

  const NS = "http://www.w3.org/2000/svg";
  const frag = document.createDocumentFragment();
  const todayIso = data.length ? data[data.length - 1].date : "";

  data.forEach((d, i) => {
    const x = i * (bw + gap);
    // 0인 날도 최소 높이 막대를 그린다 — "그 날이 존재한다"는 정보가 보여야 한다
    const h = d.pomodoro_count === 0
      ? 2
      : Math.max(3, ((H - PAD_B) * d.pomodoro_count) / max);
    const y = H - PAD_B - h;

    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("class",
      `bar${d.pomodoro_count === 0 ? " bar-empty" : ""}${d.date === todayIso ? " bar-today" : ""}`);
    rect.setAttribute("x", String(x));
    rect.setAttribute("y", String(y));
    rect.setAttribute("width", String(bw));
    rect.setAttribute("height", String(h));
    rect.setAttribute("rx", "3");

    const title = document.createElementNS(NS, "title");
    title.textContent = `${fmtDateKo(d.date)} — ${d.pomodoro_count}회, ${fmtDuration(d.focus_seconds)}`;
    rect.append(title);
    frag.append(rect);

    const label = document.createElementNS(NS, "text");
    label.setAttribute("class", "bar-label");
    label.setAttribute("x", String(x + bw / 2));
    label.setAttribute("y", String(H - 6));
    label.setAttribute("text-anchor", "middle");
    label.textContent = fmtDateKo(d.date, { weekday: true }).match(/\((.)\)/)?.[1] ?? "";
    frag.append(label);
  });

  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("aria-hidden", "true");
  svg.replaceChildren(frag);

  const desc = $("#week-chart-desc");
  if (desc) {
    desc.textContent = "최근 7일 기록: " + data
      .map((d) => `${fmtDateKo(d.date, { weekday: false })} ${d.pomodoro_count}회`)
      .join(", ");
  }
}

/** 특정 날짜의 세션 — 집계를 기억의 실마리로 바꾼다. 기본은 오늘. */
export async function loadSessionList(date = localDateStr()) {
  const host = $("#session-list");
  if (!host) return;
  let items = [];
  try {
    const data = await API.getSessionsByDate(date, 200);
    items = data.items ?? [];
  } catch {
    items = [];
  }
  const today = date === localDateStr();
  const title = $("#session-title");
  if (title) title.textContent = today ? "오늘의 세션" : `${fmtDateKo(date)}의 세션`;
  const empty = $("#session-empty");
  if (empty) {
    empty.hidden = items.length > 0;
    empty.textContent = today ? "오늘 기록한 세션이 없습니다." : "이 날짜에 기록한 세션이 없습니다.";
  }

  // ★ 로그는 append 순서다 — 최신이 위로 오게 뒤집는다.
  host.replaceChildren(...items.slice().reverse().map((s) => {
    const start = new Date(s.started_at);
    const parts = [
      fmtTimeKo(start),
      fmtDuration(s.actual_seconds ?? 0),
      s.task_name || (s.phase === "focus" ? "작업 없음" : "휴식"),
    ];
    return el("li", { class: `session-row${s.completed ? "" : " session-aborted"}` },
      el("span", { class: "session-main", text: parts.join(" · ") }),
      s.completed ? null : el("span", { class: "muted", text: "중단" }));
  }));
}

/** 달력에서 과거 날짜를 고르면 그 날의 세션 목록을 보여준다 (오늘 기점, 미래는 막는다). */
export function initRecordsView() {
  const dateInput = $("#session-date");
  if (dateInput) {
    const today = localDateStr();
    dateInput.max = today;
    dateInput.value = today;
    dateInput.addEventListener("change", () => {
      loadSessionList(dateInput.value || today);
    });
  }
}

/** ★ 기록이 있는 날만 보여준다 — 0회인 날을 나열하면 그 자체가 잔소리다. */
export function renderHistoryTable(series) {
  const body = $("#history-body");
  if (!body) return;
  const rows = series.slice(-HISTORY_DAYS).slice().reverse().filter((d) => d.pomodoro_count > 0);
  if (!rows.length) {
    body.replaceChildren(el("tr", {}, el("td", { class: "muted", colspan: "3", text: "최근 기록이 없습니다." })));
    return;
  }
  body.replaceChildren(...rows.map((d) =>
    el("tr", {},
      el("td", { text: fmtDateKo(d.date) }),
      el("td", { class: "num", text: `${d.pomodoro_count}회` }),
      el("td", { class: "num", text: d.focus_seconds ? fmtDuration(d.focus_seconds) : "-" }))));
}

export async function resetAllStats() {
  await API.resetStats();
  state.queue = [];
  saveQueue();
  await loadStats({ force: true });
}
