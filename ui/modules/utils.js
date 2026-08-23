// DOM 헬퍼 · 포맷터 · 토스트 · 모달 · 테마.
// 형제 프로젝트 llm_wiki 의 ui/modules/utils.js 관례를 계승한다.

import { LS, lsSet, state } from "./state.js";

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export const escapeHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (m) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));

// ── 시간 포맷 ────────────────────────────────────────────────────────────────

/** 1500000 -> "25:00", 3600000 이상 -> "1:00:00" */
export function fmtClock(ms) {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/** 1500 -> "25분", 5400 -> "1시간 30분", 45 -> "45초" */
export function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec}초`;
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  if (h && m) return `${h}시간 ${m}분`;
  if (h) return `${h}시간`;
  return `${m}분`;
}

/** 스크린리더용 — "25분 0초 남음" */
export function fmtClockSr(ms) {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}분 ${s}초 남음` : `${s}초 남음`;
}

/** "2026-08-22" -> "8월 22일 (금)" */
export function fmtDateKo(iso, { weekday = true } = {}) {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  const days = ["일", "월", "화", "수", "목", "금", "토"];
  const base = `${d.getMonth() + 1}월 ${d.getDate()}일`;
  return weekday ? `${base} (${days[d.getDay()]})` : base;
}

/**
 * ★ 로컬 오프셋이 붙은 ISO 문자열.
 *
 * `Date#toISOString()` 은 항상 UTC(`Z`)를 낸다. 서버는 받은 타임스탬프의 오프셋으로
 * "그 세션이 어느 날짜에 속하는지"를 파생하므로, UTC 를 보내면 자정 근처 세션이
 * 하루 어긋난다 — KST 8/23 00:06 은 UTC 로 8/22 다.
 * 그래서 로컬 오프셋(+09:00)을 직접 붙여 보낸다.
 */
export function toLocalISO(date = new Date()) {
  const pad = (n, w = 2) => String(Math.abs(n)).padStart(w, "0");
  const off = -date.getTimezoneOffset();            // 분 단위, KST 면 +540
  const sign = off >= 0 ? "+" : "-";
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
    `.${pad(date.getMilliseconds(), 3)}` +
    `${sign}${pad(Math.floor(Math.abs(off) / 60))}:${pad(Math.abs(off) % 60)}`
  );
}

/** "14:05" — 예상 완료 시각 표시용. */
export function fmtTimeKo(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** 클라이언트의 로컬 달력 날짜 "YYYY-MM-DD" — 통계 조회에 함께 보낸다. */
export function localDateStr(date = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export const uid = (prefix = "s") =>
  `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;

// ── 토스트 ───────────────────────────────────────────────────────────────────

export function showToast(message, { kind = "info", ms = 3200, action = null } = {}) {
  const stack = $("#toast-stack");
  if (!stack) return null;
  const node = el("div", { class: `toast toast-${kind}`, role: "status" },
    el("span", { class: "toast-msg", text: message }));
  if (action) {
    node.append(el("button", {
      class: "toast-action",
      type: "button",
      onclick: () => { action.onClick(); node.remove(); },
    }, action.label));
  }
  stack.append(node);
  const timer = setTimeout(() => node.remove(), ms);
  node.addEventListener("click", (e) => {
    if (e.target.closest(".toast-action")) return;
    clearTimeout(timer);
    node.remove();
  });
  return node;
}

/** 되돌릴 수 있는 동작에 쓰는 토스트 — 건너뛰기 등. */
export function flashUndoToast(label, onUndo, ms = 6000) {
  return showToast(label, { kind: "info", ms, action: { label: "되돌리기", onClick: onUndo } });
}

// ── 모달 ─────────────────────────────────────────────────────────────────────
// 포커스 트랩 + 트리거 복귀 + 더티 추적. llm_wiki utils.js 와 동일한 계약.

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function openModal({ title, body, actions = [], persistent = false }) {
  const backdrop = $("#modal-backdrop");
  if (!backdrop) return;
  state.ui.modalLastTrigger = document.activeElement;
  state.ui.modalDirty = false;

  const footer = el("div", { class: "modal-actions" });
  for (const a of actions) {
    footer.append(el("button", {
      class: `btn ${a.variant ? `btn-${a.variant}` : ""}`,
      type: "button",
      onclick: () => a.onClick?.({ close: () => closeModal({ force: true }) }),
    }, a.label));
  }

  const dialog = el("div", {
    class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title,
  },
    el("h2", { class: "modal-title", text: title }),
    el("div", { class: "modal-body" }, body),
    actions.length ? footer : null);

  backdrop.replaceChildren(dialog);
  backdrop.hidden = false;
  backdrop.dataset.persistent = persistent ? "1" : "";

  // 더티 추적 — 입력이 있었는데 그냥 닫으면 확인을 받는다
  for (const input of $$("input, textarea, select", dialog)) {
    input.addEventListener("input", () => { state.ui.modalDirty = true; });
    input.addEventListener("change", () => { state.ui.modalDirty = true; });
  }

  const first = $(FOCUSABLE, dialog) || dialog;
  first.focus?.();

  dialog.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const items = $$(FOCUSABLE, dialog);
    if (!items.length) return;
    const firstEl = items[0];
    const lastEl = items[items.length - 1];
    if (e.shiftKey && document.activeElement === firstEl) {
      e.preventDefault(); lastEl.focus();
    } else if (!e.shiftKey && document.activeElement === lastEl) {
      e.preventDefault(); firstEl.focus();
    }
  });
}

export function closeModal({ force = false } = {}) {
  const backdrop = $("#modal-backdrop");
  if (!backdrop || backdrop.hidden) return true;
  // persistent 모달은 명시적 액션으로만 닫힌다 — 복구 모달을 실수로 닫으면 기록이 날아간다
  if (!force && backdrop.dataset.persistent === "1") return false;
  if (!force && state.ui.modalDirty) {
    if (!confirm("변경사항이 저장되지 않았습니다. 닫을까요?")) return false;
  }
  backdrop.hidden = true;
  backdrop.replaceChildren();
  backdrop.dataset.persistent = "";
  state.ui.modalDirty = false;
  state.ui.modalLastTrigger?.focus?.();
  state.ui.modalLastTrigger = null;
  return true;
}

export function confirmModal(title, message, { danger = false } = {}) {
  return new Promise((resolve) => {
    openModal({
      title,
      body: el("p", { text: message }),
      actions: [
        { label: "취소", onClick: ({ close }) => { close(); resolve(false); } },
        {
          label: danger ? "삭제" : "확인",
          variant: danger ? "danger" : "primary",
          onClick: ({ close }) => { close(); resolve(true); },
        },
      ],
    });
  });
}

// ── HTTP 오류 한국어화 ───────────────────────────────────────────────────────

export function errorMessage(status, detail) {
  if (status === 0) return "서버에 연결할 수 없습니다. 실행 중인지 확인해 주세요.";
  if (status === 404) return "요청하신 항목을 찾을 수 없습니다.";
  if (status === 403) return "이 요청은 허용되지 않습니다.";
  if (status === 413) return "파일이 너무 큽니다.";
  if (status === 507) return "저장 공간이 부족합니다.";
  if (status === 400 || status === 422) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0];
      const field = Array.isArray(first?.loc) ? first.loc.slice(1).join(".") : "";
      return field ? `입력값이 올바르지 않습니다 (${field}).` : "입력값이 올바르지 않습니다.";
    }
    return "입력값이 올바르지 않습니다.";
  }
  if (status >= 500) return "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.";
  return "요청을 처리하지 못했습니다.";
}

// ── 테마 ─────────────────────────────────────────────────────────────────────

export function applyTheme(pref) {
  const mode =
    pref === "auto"
      ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : pref;
  document.documentElement.dataset.theme = mode;
  document.documentElement.dataset.themePref = pref;
  lsSet(LS.THEME, pref);
}

export function initTheme() {
  applyTheme(state.settings.ui.theme || "auto");
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if ((state.settings.ui.theme || "auto") === "auto") applyTheme("auto");
  });
}
