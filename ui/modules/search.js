// archive.org 음원 검색 — 앨범 찾기 → 곡 선택 → 추가.
//
// playlist.js 는 이미 커서 분리한다. 의존은 단방향이다 (search.js → playlist.js).

import { API } from "./api.js";
import { $, $$, debounce, el, showToast } from "./utils.js";
import { loadTracks, pollDownloadStatus, renderCredits } from "./playlist.js";

const details = new Map();     // identifier → 상세 (한 번 펼치면 다시 안 부른다)
let inflight = null;
let lastQuery = "";

// ── 검색 ─────────────────────────────────────────────────────────────────────

async function runSearch(q, preset = null) {
  inflight?.abort();
  inflight = new AbortController();
  const notice = $("#ia-notice");
  if (notice) notice.textContent = "찾는 중…";
  try {
    const res = await API.searchMedia(q, { preset, signal: inflight.signal });
    lastQuery = q;
    renderResults(res);
  } catch (e) {
    if (e?.name === "AbortError") return;      // 뒤이은 검색이 취소한 것 — 조용히
    if (notice) notice.textContent = "";
    showToast(e?.message ?? "검색하지 못했습니다.", { kind: "warn" });
  }
}

const debounced = debounce((q) => { if (q.trim().length >= 2) runSearch(q); }, 400);

// ── 렌더 ─────────────────────────────────────────────────────────────────────

function renderResults(res) {
  const notice = $("#ia-notice");
  if (notice) notice.textContent = res.query?.notice_ko ?? "";

  const host = $("#ia-results");
  if (!host) return;
  details.clear();

  if (!res.items?.length) {
    host.replaceChildren(el("p", { class: "empty",
      text: "결과가 없습니다. 다른 검색어나 아래 추천 검색을 눌러 보세요." }));
    return;
  }

  host.replaceChildren(...res.items.map((item, idx) => {
    const bodyId = `ia-body-${idx}`;
    const body = el("div", { class: "ia-body", id: bodyId, hidden: true });

    const head = el("button", {
      class: "ia-head", type: "button",
      "aria-expanded": "false", "aria-controls": bodyId,
      onclick: () => toggleItem(item, head, body),
    },
      el("span", { class: "ia-caret", text: "▶" }),
      el("span", { class: "ia-title", text: item.title }),
      el("span", { class: "ia-meta", text:
        [item.creator, `파일 ${item.files_count}개`, item.size_text_ko,
         `내려받기 ${item.downloads.toLocaleString()}회`].filter(Boolean).join(" · ") }),
      el("span", {
        class: `lic${["by", "by-sa"].includes(item.license_kind) ? " lic-attr" : ""}`,
        text: item.license_badge_ko }));

    return el("div", { class: "ia-item" }, head, body);
  }));
}

async function toggleItem(item, head, body) {
  const open = head.getAttribute("aria-expanded") === "true";
  head.setAttribute("aria-expanded", open ? "false" : "true");
  head.querySelector(".ia-caret").textContent = open ? "▶" : "▼";
  body.hidden = open;
  if (open || details.has(item.identifier)) return;

  body.replaceChildren(el("p", { class: "muted", text: "곡 목록을 가져오는 중…" }));
  let d;
  try {
    d = await API.getSearchItem(item.identifier);
  } catch (e) {
    body.replaceChildren(el("p", { class: "notice", text: e?.message ?? "가져오지 못했습니다." }));
    return;
  }
  details.set(item.identifier, d);
  renderTracks(d, body);
}

function renderTracks(d, body) {
  const checks = [];
  const count = el("span", { class: "muted", text: "0곡 선택" });

  const sync = () => {
    const n = checks.filter((x) => x.checked).length;
    count.textContent = `${n}곡 선택`;
  };

  const list = el("div", { class: "ia-tracks" },
    ...d.tracks.map((t) => {
      const box = el("input", {
        type: "checkbox", disabled: !d.addable, onchange: sync,
      });
      box.dataset.name = t.name;
      checks.push(box);
      return el("label", { class: "ia-track" }, box,
        el("span", { class: "ia-track-title", text: t.title_ko }),
        el("span", { class: "ia-track-meta", text:
          [t.size_text_ko, t.duration_text ? (t.duration_suspect ? `약 ${t.duration_text}` : t.duration_text) : null]
            .filter(Boolean).join(" · ") }));
    }));

  const add = async (playlist) => {
    const names = checks.filter((x) => x.checked).map((x) => x.dataset.name);
    if (!names.length) { showToast("곡을 선택해 주세요.", { ms: 1800 }); return; }
    try {
      const res = await API.addSearchTracks({
        identifier: d.identifier, names, playlists: [playlist], download: true,
      });
      showToast(res.message_ko, { kind: "ok", ms: 4000 });
      checks.forEach((x) => { x.checked = false; });
      sync();
      await loadTracks();
      renderCredits();
      pollDownloadStatus();
    } catch (e) {
      showToast(e?.message ?? "추가하지 못했습니다.", { kind: "warn", ms: 4000 });
    }
  };

  body.replaceChildren(
    // ★ 못 쓰는 항목이어도 곡 목록은 보여준다 — 왜 못 쓰는지 납득할 수 있어야 한다
    d.addable ? null : el("p", { class: "notice", text: d.reason_ko }),
    el("p", { class: "muted", text:
      `사용 가능한 MP3 ${d.usable_count}곡 · 전체 파일 ${d.total_files}개` }),
    list,
    el("div", { class: "row-actions" },
      el("button", {
        class: "btn btn-small", type: "button", disabled: !d.addable,
        onclick: () => { checks.forEach((x) => { x.checked = true; }); sync(); },
      }, "전체 선택"),
      count,
      el("button", { class: "btn btn-primary btn-small", type: "button",
                     disabled: !d.addable, onclick: () => add("focus") }, "집중에 추가"),
      el("button", { class: "btn btn-primary btn-small", type: "button",
                     disabled: !d.addable, onclick: () => add("break") }, "휴식에 추가"),
      el("a", { class: "muted", href: d.details_url, target: "_blank",
                rel: "noopener noreferrer" }, "archive.org 에서 보기")));
}

// ── 초기화 ───────────────────────────────────────────────────────────────────

export async function initSearchView() {
  const input = $("#ia-q");
  input?.addEventListener("input", (e) => debounced(e.target.value));
  input?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); runSearch(input.value); }
  });
  $("#btn-ia-search")?.addEventListener("click", () => runSearch(input?.value ?? ""));

  // 추천 검색 칩 — 한국어 재작성을 거치지 않고 서버가 영어 검색어를 직접 들고 있다
  const chips = $("#ia-chips");
  if (chips && !chips.children.length) {
    try {
      const presets = await API.getSearchPresets();
      chips.replaceChildren(...presets.map((p) =>
        el("button", { class: "btn btn-small", type: "button", title: p.hint_ko,
                       onclick: () => { if (input) input.value = ""; runSearch("", p.id); } },
           p.label_ko)));
    } catch {
      /* 추천 검색은 부가 기능 — 실패해도 검색창은 쓸 수 있다 */
    }
  }
}
