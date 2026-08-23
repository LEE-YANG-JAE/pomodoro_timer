// 설정 화면 — 서버 저장 + 기기별 음량 override.

import { API } from "./api.js";
import { LS, lsGet, lsSet, state } from "./state.js";
import { $, $$, debounce, el, fmtDuration, showToast, applyTheme } from "./utils.js";
import { applyDurationChange } from "./timer.js";
import {
  CHIMES, effectiveVolumes, previewChime, previewNoise, setDeviceVolume, syncNoise,
} from "./audio.js";
import { NOISE_TYPES } from "./noise.js";
import { notifySupport, requestNotifyPermission } from "./notify.js";
import { renderCycleDots } from "./ui.js";

export async function loadSettings() {
  try {
    const data = await API.getSettings();
    state.settings = data;
    lsSet(LS.SETTINGS, data);
  } catch {
    const cached = lsGet(LS.SETTINGS, null);
    if (cached) state.settings = cached;
    showToast("설정을 불러오지 못해 마지막 값을 사용합니다.", { kind: "warn" });
  }
  applyTheme(state.settings.ui.theme || "auto");
  return state.settings;
}

async function pushGroup(group) {
  try {
    const res = await API.putSettings({ [group]: state.settings[group] });
    // ★ 응답 전체로 교체하면 안 된다 — 디바운스 중 다른 그룹을 즉시(immediate) 저장하면
    //   그 응답(아직 이 그룹의 변경을 모르는 서버 상태)이 방금 반영한 값을 되돌려 버린다.
    //   PUT 은 그룹 단위 교체이므로 클라이언트도 보낸 그룹만 반영한다.
    state.settings[group] = res.settings[group];
    lsSet(LS.SETTINGS, state.settings);
    // 교체된 값으로 다시 그려 화면과 서버를 일치시킨다
    if (group === "timer") renderSetList();
  } catch (e) {
    showToast(e?.message ?? "설정을 저장하지 못했습니다.", { kind: "warn" });
  }
}

const pushGroupDebounced = debounce(pushGroup, 400);

/** 값을 즉시 화면에 반영하고 서버 저장은 디바운스한다 (낙관적). */
export function updateSetting(group, key, value, { immediate = false } = {}) {
  state.settings[group][key] = value;
  if (immediate) pushGroup(group);
  else pushGroupDebounced(group);
}

// ── 렌더 ─────────────────────────────────────────────────────────────────────

/**
 * 자동 시작 두 개의 불리언을 하나의 선택지로 묶는다.
 * 체크박스 2개보다 "휴식만 자동 / 모두 자동 / 직접 시작" 3지선다가 이해하기 쉽다.
 * (false, true) 조합은 의미가 없어 노출하지 않는다.
 */
function autoStartMode(timer) {
  if (timer.auto_start_break && timer.auto_start_focus) return "all";
  if (timer.auto_start_break) return "break";
  return "none";
}

function applyAutoStartMode(mode) {
  const t = state.settings.timer;
  t.auto_start_break = mode !== "none";
  t.auto_start_focus = mode === "all";
}

const MIN_MINUTES = 1;      // UI 에서 허용하는 최소 길이 (API 하한은 테스트를 위해 1초)

export function renderSettingsView() {
  const s = state.settings;
  const set = (sel, value) => { const n = $(sel); if (n) n.value = value; };
  const check = (sel, value) => { const n = $(sel); if (n) n.checked = Boolean(value); };

  renderSetList();
  check("#set-repeat", s.timer.repeat);
  set("#set-autostart", autoStartMode(s.timer));
  set("#set-interruption", s.timer.interruption_policy);

  const vols = effectiveVolumes();
  set("#set-chime-vol", vols.chime);
  check("#set-silent", s.audio.silent_mode);
  set("#set-crossfade", s.audio.crossfade_seconds);
  // 알림음 끄기는 별도 체크박스가 아니라 종류 목록의 한 항목이다 (컨트롤 하나 절약)
  set("#set-chime-variant", s.audio.chime_enabled ? s.audio.chime_variant : "off");
  set("#set-noise-phases", s.audio.noise_phases);

  set("#set-day-start", s.records.day_start_hour);
  set("#set-goal", s.records.daily_goal);
  set("#set-theme", s.ui.theme);
  check("#set-notifications", s.ui.notifications);
  check("#set-wakelock", s.ui.wake_lock);
  check("#set-auto-focus-mode", s.ui.auto_focus_mode);
  check("#set-favicon", s.ui.dynamic_favicon);

  // ★ http:// 로 LAN 접속하면 secure context 가 아니라 알림·Wake Lock 이 동작하지 않는다.
  //   토글을 비활성화하고 이유를 밝힌다 — 조용히 실패하면 사용자는 버그로 여긴다.
  const sup = notifySupport();
  for (const [sel, ok] of [["#set-notifications", sup.notification], ["#set-wakelock", sup.wakeLock]]) {
    const n = $(sel);
    if (!n) continue;
    n.disabled = !ok;
    const hint = n.closest(".field")?.querySelector(".field-hint");
    if (hint && !ok) {
      hint.textContent = "이 기능은 https 또는 localhost 접속에서만 동작합니다.";
      hint.dataset.warn = "1";
    }
  }
}

function bindNumber(sel, group, key, { scale = 1, min = 1, max = 999 } = {}) {
  const node = $(sel);
  if (!node) return;
  node.addEventListener("change", () => {
    let v = Number(node.value);
    if (!Number.isFinite(v)) v = min;
    v = Math.min(max, Math.max(min, Math.round(v)));
    node.value = v;
    updateSetting(group, key, v * scale);
    if (group === "timer") {
      applyDurationChange();
      renderCycleDots();
    }
  });
}

function bindCheck(sel, group, key, after = null) {
  const node = $(sel);
  if (!node) return;
  node.addEventListener("change", () => {
    updateSetting(group, key, node.checked, { immediate: true });
    after?.(node.checked);
  });
}

function bindSelect(sel, group, key, after = null) {
  const node = $(sel);
  if (!node) return;
  node.addEventListener("change", () => {
    const v = node.value === "" ? null : node.value;
    updateSetting(group, key, key === "day_start_hour" ? Number(v) : v, { immediate: true });
    after?.(v);
  });
}

// ── 사이클 세트 편집 ─────────────────────────────────────────────────────────

function pushSets() {
  updateSetting("timer", "sets", state.settings.timer.sets, { immediate: true });
  applyDurationChange();
  renderSetList();
  renderCycleDots();
}

/** 분 단위 입력 → 초. 사용자가 빈칸이나 이상한 값을 넣어도 안전하게. */
function minutesToSeconds(raw, fallbackSec, maxMinutes = 180) {
  const v = Number(raw);
  if (!Number.isFinite(v) || v < MIN_MINUTES) return fallbackSec;
  return Math.min(maxMinutes, Math.round(v)) * 60;
}

export function renderSetList() {
  const host = $("#set-list");
  if (!host) return;
  const list = state.settings.timer.sets ?? [];

  // ★ 핸들러에서 item 객체를 붙잡지 말고 **매번 인덱스로 다시 찾는다.**
  //   설정을 저장하면 서버 응답으로 state.settings 가 통째로 교체되므로, 렌더 시점에
  //   캡처해 둔 객체는 곧 버려진 사본이 된다. 거기에 값을 쓰면 조용히 사라진다
  //   (집중은 바뀌는데 휴식은 안 바뀌던 원인).
  const at = (i) => state.settings.timer.sets[i];

  host.replaceChildren(...list.map((item, i) => {
    const focusMin = Math.max(MIN_MINUTES, Math.round(item.focus_seconds / 60));
    const breakMin = Math.max(MIN_MINUTES, Math.round(item.break_seconds / 60));
    const active = state.timer.setIndex === i;

    return el("div", { class: `set-row${active ? " set-row-active" : ""}` },
      el("span", { class: "set-no", text: `${i + 1}` }),
      el("label", { class: "set-field" },
        el("span", { text: "집중" }),
        el("input", {
          type: "number", min: MIN_MINUTES, max: 180, step: 1, value: focusMin,
          "aria-label": `${i + 1}번째 세트 집중 시간 (분)`,
          onchange: (e) => {
            const cur = at(i);
            if (!cur) return;
            cur.focus_seconds = minutesToSeconds(e.target.value, cur.focus_seconds, 180);
            pushSets();
          },
        }),
        el("span", { class: "unit", text: "분" })),
      el("label", { class: "set-field" },
        el("span", { text: "휴식" }),
        el("input", {
          type: "number", min: MIN_MINUTES, max: 120, step: 1, value: breakMin,
          "aria-label": `${i + 1}번째 세트 휴식 시간 (분)`,
          onchange: (e) => {
            const cur = at(i);
            if (!cur) return;
            cur.break_seconds = minutesToSeconds(e.target.value, cur.break_seconds, 120);
            pushSets();
          },
        }),
        el("span", { class: "unit", text: "분" })),
      el("div", { class: "set-actions" },
        el("button", {
          class: "icon-btn", type: "button", title: "위로", disabled: i === 0,
          "aria-label": `${i + 1}번째 세트 위로`,
          onclick: () => {
            const arr = state.settings.timer.sets;
            [arr[i - 1], arr[i]] = [arr[i], arr[i - 1]];
            pushSets();
          },
        }, "▲"),
        el("button", {
          class: "icon-btn", type: "button", title: "아래로", disabled: i === list.length - 1,
          "aria-label": `${i + 1}번째 세트 아래로`,
          onclick: () => {
            const arr = state.settings.timer.sets;
            [arr[i + 1], arr[i]] = [arr[i], arr[i + 1]];
            pushSets();
          },
        }, "▼"),
        el("button", {
          class: "icon-btn", type: "button", title: "복제",
          "aria-label": `${i + 1}번째 세트 복제`,
          onclick: () => {
            const cur = at(i);
            if (!cur) return;
            state.settings.timer.sets.splice(i + 1, 0, { ...cur });
            pushSets();
          },
        }, "⧉"),
        el("button", {
          // 세트가 하나뿐이면 지울 수 없다 — 계획이 비면 타이머가 돌 수 없다
          class: "icon-btn icon-danger", type: "button", title: "삭제",
          disabled: list.length <= 1,
          "aria-label": `${i + 1}번째 세트 삭제`,
          onclick: () => {
            state.settings.timer.sets.splice(i, 1);
            if (state.timer.setIndex >= state.settings.timer.sets.length) {
              state.timer.setIndex = 0;
            }
            pushSets();
          },
        }, "✕")));
  }));

  const summary = $("#set-summary");
  if (summary) {
    const totalFocus = list.reduce((a, x) => a + x.focus_seconds, 0);
    const totalBreak = list.reduce((a, x) => a + x.break_seconds, 0);
    summary.textContent =
      `${list.length}세트 · 집중 ${fmtDuration(totalFocus)} + 휴식 ${fmtDuration(totalBreak)}` +
      ` = 한 바퀴 ${fmtDuration(totalFocus + totalBreak)}`;
  }
}

/** 타이머 화면의 소음 빠른 컨트롤을 현재 설정에 맞춘다. */
export function renderNoiseControls() {
  const a = state.settings.audio;
  const btn = $("#btn-noise");
  if (btn) {
    btn.setAttribute("aria-pressed", a.noise_enabled ? "true" : "false");
    btn.textContent = a.noise_enabled ? "🌊 소음 켜짐" : "🌊 백색 소음";
  }
  const quick = $("#noise-type-quick");
  if (quick) quick.value = a.noise_type;
  const vol = $("#noise-volume");
  if (vol) vol.value = effectiveVolumes().noise;
  // 꺼져 있으면 종류·음량을 숨긴다 (CSS 가 .noise-bar[data-on] 로 처리)
  const bar = document.querySelector(".noise-bar");
  if (bar) bar.dataset.on = a.noise_enabled ? "1" : "";
}

export function initSettingsView() {
  // 알림음 종류 — 마지막 항목이 "사용 안 함" (chime_enabled 체크박스를 대체)
  const chimeSel = $("#set-chime-variant");
  if (chimeSel && !chimeSel.options.length) {
    for (const c of Object.values(CHIMES)) chimeSel.append(new Option(c.name_ko, c.id));
    chimeSel.append(new Option("사용 안 함", "off"));
  }

  // 소음 종류 — 타이머 화면 빠른 선택에만 둔다
  const noiseSel = $("#noise-type-quick");
  if (noiseSel && !noiseSel.options.length) {
    for (const n of Object.values(NOISE_TYPES)) noiseSel.append(new Option(n.name_ko, n.id));
  }

  bindNumber("#set-goal", "records", "daily_goal", { min: 1, max: 50 });

  // 세트 추가 — 마지막 세트를 복사해 붙인다 (대개 같은 길이를 이어서 쓰기 때문)
  $("#btn-add-set")?.addEventListener("click", () => {
    const arr = state.settings.timer.sets;
    const last = arr[arr.length - 1] ?? { focus_seconds: 1500, break_seconds: 300 };
    arr.push({ focus_seconds: last.focus_seconds, break_seconds: last.break_seconds, label: null });
    pushSets();
  });
  bindCheck("#set-repeat", "timer", "repeat");
  bindNumber("#set-crossfade", "audio", "crossfade_seconds", { min: 0, max: 10 });

  bindCheck("#set-silent", "audio", "silent_mode");
  bindCheck("#set-auto-focus-mode", "ui", "auto_focus_mode");
  bindCheck("#set-favicon", "ui", "dynamic_favicon");

  $("#set-autostart")?.addEventListener("change", (e) => {
    applyAutoStartMode(e.target.value);
    updateSetting("timer", "auto_start_break", state.settings.timer.auto_start_break,
                  { immediate: true });
  });
  bindCheck("#set-wakelock", "ui", "wake_lock");
  bindCheck("#set-notifications", "ui", "notifications", async (on) => {
    // 권한 요청은 사용자가 방금 클릭한 이 순간에만 가능하다
    if (on && Notification?.permission === "default") {
      const result = await requestNotifyPermission();
      if (result !== "granted") {
        showToast("브라우저에서 알림이 허용되지 않았습니다.", { kind: "warn" });
      }
    }
  });

  bindSelect("#set-interruption", "timer", "interruption_policy");
  $("#set-chime-variant")?.addEventListener("change", (e) => {
    const v = e.target.value;
    const on = v !== "off";
    state.settings.audio.chime_enabled = on;
    if (on) state.settings.audio.chime_variant = v;
    updateSetting("audio", "chime_enabled", on, { immediate: true });
    if (on) previewChime(v);
  });
  bindSelect("#set-day-start", "records", "day_start_hour", () => {
    // 서버가 기존 기록의 날짜를 소급 재계산하므로 통계를 다시 읽어야 한다
    import("./stats.js").then((m) => m.loadStats({ force: true }));
  });
  bindSelect("#set-theme", "ui", "theme", (v) => applyTheme(v || "auto"));
  bindSelect("#set-noise-phases", "audio", "noise_phases", () => syncNoise());

  // 음량은 기기별 override — 노트북 스피커와 헤드폰의 적정 음량이 다르다
  // 음악·소음 음량은 타이머 화면에 있으므로 설정에는 알림음만 둔다
  for (const [sel, kind] of [["#set-chime-vol", "chime"]]) {
    const node = $(sel);
    if (!node) continue;
    node.addEventListener("input", () => setDeviceVolume(kind, Number(node.value)));
    node.addEventListener("change", () => {
      const group = "audio";
      const key = { music: "music_volume", chime: "chime_volume", noise: "noise_volume" }[kind];
      updateSetting(group, key, Number(node.value), { immediate: true });
      if (kind === "chime") previewChime(state.settings.audio.chime_variant);
    });
  }

  $("#btn-preview-chime")?.addEventListener("click", () =>
    previewChime(state.settings.audio.chime_variant));

  $("#btn-reset-settings")?.addEventListener("click", async () => {
    const { confirmModal } = await import("./utils.js");
    if (!await confirmModal("설정 초기화", "모든 설정을 기본값으로 되돌릴까요?")) return;
    state.settings = await API.resetSettings();
    lsSet(LS.SETTINGS, state.settings);
    applyTheme(state.settings.ui.theme);
    renderSettingsView();
    applyDurationChange();
    showToast("설정을 초기화했습니다.");
  });

  $("#btn-reset-stats")?.addEventListener("click", async () => {
    const { confirmModal } = await import("./utils.js");
    if (!await confirmModal("기록 초기화", "지금까지의 모든 기록이 삭제됩니다. 계속할까요?",
      { danger: true })) return;
    const m = await import("./stats.js");
    await m.resetAllStats();
    showToast("기록을 초기화했습니다.");
  });

  renderSettingsView();
  renderNoiseControls();
}
