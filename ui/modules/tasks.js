// 오늘 할 일 — 목록 · 활성 작업.
//
// ★ timer.js 는 작업을 모른다. 세션에 작업을 붙이는 일은 main.js(배선 계층)가
//   withActiveTask() 를 호출해서 한다.

import { API } from "./api.js";
import { LS, emit, lsGet, lsSet, state } from "./state.js";
import { $, confirmModal, el, fmtDuration, localDateStr, showToast } from "./utils.js";

// ── 로드 ─────────────────────────────────────────────────────────────────────

export async function loadTasks({ force = false, days = 1 } = {}) {
  if (!force && Date.now() - state.tasks.loadedAt < 1500) return state.tasks.items;
  try {
    const data = await API.getTasks(days);
    state.tasks.items = data.tasks ?? [];
    state.tasks.activeId = data.active_task_id ?? null;
    state.tasks.totals = data.totals ?? state.tasks.totals;
    state.tasks.loadedAt = Date.now();
    state.tasks.stale = false;
    lsSet(LS.TASKS, { items: state.tasks.items, activeId: state.tasks.activeId,
                      totals: state.tasks.totals });
  } catch {
    const cached = lsGet(LS.TASKS, null);
    if (cached) {
      state.tasks.items = cached.items ?? [];
      state.tasks.activeId = cached.activeId ?? null;
      state.tasks.totals = cached.totals ?? state.tasks.totals;
    }
    state.tasks.stale = true;
  }
  emit("tasks:changed", { activeId: state.tasks.activeId, count: state.tasks.items.length });
  return state.tasks.items;
}

// ★ 핸들러에서 작업 객체도 인덱스도 붙잡지 않는다. 목록은 서버 응답으로 통째로
//   교체되고(§3.6), 인덱스는 재정렬로 바뀐다. id 만 붙잡고 이벤트 시점에 다시 찾는다.
const byId = (id) => state.tasks.items.find((t) => t.id === id) ?? null;

export function activeTask() {
  return state.tasks.activeId ? byId(state.tasks.activeId) : null;
}

// ── 타이머와의 이음매 ────────────────────────────────────────────────────────

/**
 * 세션 레코드에 활성 작업을 붙인다.
 * 활성 작업이 없으면 세션을 **그대로** 돌려준다 — 작업 없이 집중하는 것은 합법이고
 * 그 기록도 온전히 남아야 한다.
 */
export function withActiveTask(session) {
  const t = activeTask();
  return t ? { ...session, task_id: t.id, task_name: t.name } : session;
}

/** 낙관적 2/4 → 3/4. 서버 왕복을 기다리지 않는다. */
export function localApplyTaskPomodoro(session) {
  if (!session?.task_id || !session.completed || session.phase !== "focus") return;
  const t = byId(session.task_id);
  if (!t) return;
  t.done_pomodoros = (t.done_pomodoros ?? 0) + 1;
  t.done_today = (t.done_today ?? 0) + 1;
  renderTaskList();
}

// ── 변경 (전부 낙관적 → 실패 시 되돌린다) ───────────────────────────────────

async function mutate(fn, { optimistic = null } = {}) {
  optimistic?.();
  renderTaskList();
  try {
    await fn();
  } catch (e) {
    showToast(e?.message ?? "저장하지 못했습니다.", { kind: "warn" });
  }
  await loadTasks({ force: true });
  renderTaskList();
  renderActiveTaskLabel();
}

export async function addTask(name, est) {
  const clean = String(name ?? "").trim();
  if (!clean) return;
  await mutate(() => API.createTask(clean, est));
}

export async function patchTask(id, patch) {
  await mutate(() => API.patchTask(id, patch));
}

export async function toggleDone(id) {
  const t = byId(id);
  if (!t) return;
  // ★ optimistic 이 mutate() 안에서 fn() 보다 먼저 실행된다. 둘 다 t.completed 를
  //   지연 평가로 뒤집으면 fn() 이 이중부정된 원래 값을 보내 토글이 무효화된다 —
  //   목표값을 먼저 캡처해 둔다.
  const next = !t.completed;
  await mutate(() => API.patchTask(id, { completed: next }),
               { optimistic: () => { t.completed = next; } });
}

export async function setActiveTask(id) {
  // 같은 작업을 다시 누르면 해제 — 작업 없이 집중하는 상태로 돌아갈 길이 있어야 한다
  const next = state.tasks.activeId === id ? null : id;
  state.tasks.activeId = next;
  renderTaskList();
  renderActiveTaskLabel();
  try {
    await API.setActiveTask(next);
  } catch (e) {
    showToast(e?.message ?? "선택하지 못했습니다.", { kind: "warn" });
    await loadTasks({ force: true });
  }
  emit("tasks:changed", { activeId: next, count: state.tasks.items.length });
}

export async function reorderTask(id, dir) {
  // ★ 저장 순서(state.tasks.items)로 움직인다. 화면은 완료 항목을 아래로 내려
  //   보여주므로, 렌더 순서로 계산하면 저장 순서가 뒤엉킨다.
  const ids = state.tasks.items.map((t) => t.id);
  const i = ids.indexOf(id);
  const j = i + dir;
  if (i === -1 || j < 0 || j >= ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  await mutate(() => API.reorderTasks(ids));
}

export async function removeTask(id) {
  const t = byId(id);
  if (!await confirmModal("작업 삭제",
      `"${t?.name ?? id}" 을(를) 삭제할까요? 지금까지의 기록은 그대로 남습니다.`,
      { danger: true })) return;
  await mutate(() => API.deleteTask(id));
}

// ── 렌더 ─────────────────────────────────────────────────────────────────────

export function renderActiveTaskLabel() {
  const node = $("#active-task");
  if (!node) return;
  const t = activeTask();
  if (!t) {
    node.hidden = true;
    node.textContent = "";
    return;
  }
  node.hidden = false;
  node.textContent = t.name;
}

export function renderTaskList() {
  const host = $("#task-list");
  if (!host) return;

  // 완료 항목은 **표시할 때만** 아래로 내린다. 저장 순서는 건드리지 않는다.
  const rows = state.tasks.items.slice()
    .sort((a, b) => Number(a.completed) - Number(b.completed));

  const empty = $("#task-empty");
  if (empty) empty.hidden = rows.length > 0;

  host.replaceChildren(...rows.map((t) => {
    const active = t.id === state.tasks.activeId;
    return el("li", {
      class: `task-row${active ? " task-row-active" : ""}${t.completed ? " task-row-done" : ""}`,
    },
      el("button", {
        class: "task-check", type: "button",
        "aria-pressed": t.completed ? "true" : "false",
        "aria-label": `${t.name} 완료 표시`,
        onclick: () => toggleDone(t.id),
      }, t.completed ? "●" : "○"),
      el("button", {
        class: "task-pick", type: "button",
        "aria-pressed": active ? "true" : "false",
        "aria-label": `${t.name} 선택`,
        onclick: () => setActiveTask(t.id),
      },
        el("span", { class: "task-name", text: t.name })),
      el("div", { class: "task-actions" },
        el("button", { class: "icon-btn", type: "button", "aria-label": `${t.name} 위로`,
                       onclick: () => reorderTask(t.id, -1) }, "▲"),
        el("button", { class: "icon-btn", type: "button", "aria-label": `${t.name} 아래로`,
                       onclick: () => reorderTask(t.id, 1) }, "▼"),
        el("button", { class: "icon-btn", type: "button", "aria-label": `${t.name} 이름 수정`,
                       onclick: (e) => startInlineEdit(t.id, e.currentTarget) }, "✎"),
        el("button", { class: "icon-btn icon-danger", type: "button",
                       "aria-label": `${t.name} 삭제`,
                       onclick: () => removeTask(t.id) }, "✕")));
  }));
}

/** 이름 수정은 인라인 — 이름 하나 바꾸려고 모달을 띄우는 건 마찰이다. */
function startInlineEdit(id, trigger) {
  const row = trigger.closest(".task-row");
  const nameEl = row?.querySelector(".task-name");
  const t = byId(id);
  if (!nameEl || !t) return;

  const input = el("input", {
    class: "task-name-edit", type: "text", value: t.name, maxlength: 120,
    "aria-label": "작업 이름",
  });
  nameEl.replaceWith(input);
  input.focus();
  input.select();

  let done = false;
  const commit = async (save) => {
    if (done) return;
    done = true;
    const next = input.value.trim();
    if (save && next && next !== t.name) await patchTask(id, { name: next });
    else { renderTaskList(); renderActiveTaskLabel(); }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); commit(true); }
    else if (e.key === "Escape") { e.preventDefault(); commit(false); }
  });
  input.addEventListener("blur", () => commit(true));
}

// ── 초기화 ───────────────────────────────────────────────────────────────────

export function initTasksView() {
  const form = $("#task-add");
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const nameEl = $("#task-name");
    const name = nameEl?.value ?? "";
    if (!name.trim()) return;
    if (nameEl) nameEl.value = "";
    await addTask(name, 1);
    nameEl?.focus();
  });

  $("#btn-clear-done")?.addEventListener("click", async () => {
    if (!await confirmModal("완료 항목 정리", "완료한 작업을 목록에서 치울까요?")) return;
    try {
      await API.clearCompletedTasks(localDateStr());
    } catch (e) {
      showToast(e?.message ?? "정리하지 못했습니다.", { kind: "warn" });
    }
    await loadTasks({ force: true });
    renderTaskList();
  });
}
