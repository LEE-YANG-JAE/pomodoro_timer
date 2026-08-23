// 음악 화면 — 트랙 목록 · 집중/휴식 배정 · 순서 변경 · 업로드 · 폴더 가져오기 · 크레딧.

import { API } from "./api.js";
import { state } from "./state.js";
import { $, $$, confirmModal, el, fmtDuration, showToast } from "./utils.js";
import { playTrackById, rebuildOrder } from "./audio.js";

let pollTimer = null;
let currentFilter = "all";   // all | focus | break

// ── 로드 ─────────────────────────────────────────────────────────────────────

export async function loadTracks() {
  try {
    const [tracks, playlists] = await Promise.all([API.getTracks(), API.getPlaylists()]);
    state.tracks = tracks;
    state.playlists = playlists;
    rebuildOrder("focus", { keepIfSame: true });
    rebuildOrder("break", { keepIfSame: true });
  } catch {
    state.tracks = [];
    state.playlists = [];
  }
  renderPlaylistView();
}

function playlistOf(kind) {
  const id = kind === "focus"
    ? state.settings.audio.focus_playlist_id
    : state.settings.audio.break_playlist_id;
  return state.playlists.find((p) => p.id === id) ?? null;
}

function isIn(kind, trackId) {
  return Boolean(playlistOf(kind)?.track_ids?.includes(trackId));
}

// ── 배정 / 순서 ──────────────────────────────────────────────────────────────

export async function assignTrack(trackId, kind, on) {
  const pl = playlistOf(kind);
  if (!pl) return;
  const ids = pl.track_ids.slice();
  const at = ids.indexOf(trackId);
  if (on && at === -1) ids.push(trackId);
  else if (!on && at !== -1) ids.splice(at, 1);
  else return;

  pl.track_ids = ids;      // 낙관적 반영
  renderPlaylistView();
  try {
    const updated = await API.updatePlaylist(pl.id, { track_ids: ids });
    Object.assign(pl, updated);
  } catch (e) {
    showToast(e?.message ?? "재생목록을 저장하지 못했습니다.", { kind: "warn" });
    await loadTracks();
    return;
  }
  rebuildOrder(kind);
  renderPlaylistView();
}

export async function reorderTrack(kind, trackId, dir) {
  const pl = playlistOf(kind);
  if (!pl) return;
  const ids = pl.track_ids.slice();
  const i = ids.indexOf(trackId);
  const j = i + dir;
  if (i === -1 || j < 0 || j >= ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  pl.track_ids = ids;
  renderPlaylistView();
  try {
    await API.updatePlaylist(pl.id, { track_ids: ids });
  } catch (e) {
    showToast(e?.message ?? "순서를 저장하지 못했습니다.", { kind: "warn" });
    await loadTracks();
    return;
  }
  rebuildOrder(kind);
}

// ── 업로드 / 가져오기 ────────────────────────────────────────────────────────

export async function uploadTracks(fileList) {
  const files = Array.from(fileList ?? []);
  if (!files.length) return;
  const status = $("#upload-status");
  let done = 0;
  let failed = 0;
  for (const file of files) {
    if (status) status.textContent = `업로드 중 ${done + 1}/${files.length} — ${file.name}`;
    try {
      await API.uploadTrack(file, (p) => {
        if (status) {
          status.textContent =
            `업로드 중 ${done + 1}/${files.length} — ${file.name} (${Math.round(p * 100)}%)`;
        }
      });
      done += 1;
    } catch (e) {
      failed += 1;
      showToast(`${file.name}: ${e?.message ?? "업로드 실패"}`, { kind: "warn" });
    }
  }
  if (status) status.textContent = "";
  await loadTracks();
  showToast(failed
    ? `${done}개 추가, ${failed}개 실패`
    : `${done}개의 음원을 추가했습니다.`);
}

export async function deleteTrack(trackId) {
  const t = state.tracks.find((x) => x.id === trackId);
  if (!await confirmModal("음원 삭제",
    `"${t?.title_ko ?? trackId}" 을(를) 삭제할까요? 파일이 지워집니다.`, { danger: true })) return;
  try {
    await API.deleteTrack(trackId);
  } catch (e) {
    showToast(e?.message ?? "삭제하지 못했습니다.", { kind: "warn" });
    return;
  }
  await loadTracks();
  showToast("삭제했습니다.");
}

// ── 다운로드 진행률 ──────────────────────────────────────────────────────────

export async function pollDownloadStatus() {
  try {
    const s = await API.getDownloadStatus();
    state.download = {
      active: Boolean(s.active),
      done: s.job?.done ?? 0,
      total: s.job?.total ?? 0,
      failed: s.job?.failed ?? 0,
      message: s.message_ko ?? "",
      readyCount: s.ready_count ?? 0,
    };
    renderDownloadStatus();

    if (s.active) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(pollDownloadStatus, 3000);
    } else if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
      await loadTracks();
      if ((s.ready_count ?? 0) > 0) {
        showToast("음원 준비가 끝났습니다.", { kind: "ok" });
      }
    }
  } catch {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function renderDownloadStatus() {
  const host = $("#download-status");
  if (!host) return;
  const d = state.download;
  host.hidden = !d.active && !d.message;
  const label = $("#download-label");
  if (label) label.textContent = d.message || "";
  const fill = $("#download-fill");
  if (fill) fill.style.width = d.total ? `${(d.done / d.total) * 100}%` : "0%";
  const cancel = $("#btn-cancel-download");
  if (cancel) cancel.hidden = !d.active;
}

export async function startDownload(tier = "core") {
  try {
    await API.startDownload({ tier });
    showToast("음원 내려받기를 시작했습니다.");
    pollDownloadStatus();
  } catch (e) {
    showToast(e?.message ?? "내려받기를 시작하지 못했습니다.", { kind: "warn" });
  }
}

// ── 렌더 ─────────────────────────────────────────────────────────────────────

export function renderNowPlayingMini() {
  const node = $("#pl-now");
  if (!node) return;
  const t = state.audio.current;
  node.textContent = t ? `재생 중: ${t.title_ko}` : "";
}

export function renderPlaylistView() {
  const body = $("#track-list");
  if (!body) return;

  const rows = state.tracks.filter((t) => {
    if (currentFilter === "all") return true;
    return isIn(currentFilter, t.id);
  });

  const summary = $("#track-summary");
  if (summary) {
    const ready = state.tracks.filter((t) => t.ready).length;
    summary.textContent =
      `전체 ${state.tracks.length}곡 · 재생 가능 ${ready}곡 · ` +
      `집중 ${playlistOf("focus")?.ready_count ?? 0}곡 · 휴식 ${playlistOf("break")?.ready_count ?? 0}곡`;
  }

  if (!rows.length) {
    body.replaceChildren(el("p", { class: "empty" },
      state.tracks.length
        ? "이 목록에 담긴 곡이 없습니다. 아래 표에서 집중/휴식에 체크해 주세요."
        : "아직 음원이 없습니다. 내려받거나 직접 추가해 주세요. 음악이 없어도 타이머는 정상 동작합니다."));
    return;
  }

  const orderable = currentFilter !== "all";
  body.replaceChildren(...rows.map((t, idx) => {
    const focusOn = isIn("focus", t.id);
    const breakOn = isIn("break", t.id);
    const playing = state.audio.current?.id === t.id;
    return el("div", {
      class: `track-row${t.ready ? "" : " track-pending"}${playing ? " track-playing" : ""}`,
    },
      // 제목을 누르면 그 곡을 바로 재생한다
      el("button", {
        class: "track-main", type: "button", disabled: !t.ready,
        "aria-label": `${t.title_ko} 재생`,
        onclick: async () => {
          const ok = await playTrackById(t.id);
          if (!ok) showToast("이 곡을 재생할 수 없습니다.", { kind: "warn" });
          renderPlaylistView();
        },
      },
        el("div", { class: "track-title", text: t.title_ko ?? t.title_orig ?? t.id }),
        el("div", { class: "track-meta", text:
          [playing ? "재생 중" : null, t.performer_ko,
           t.duration_seconds ? fmtDuration(t.duration_seconds) : null,
           t.ready ? null : "내려받는 중"].filter(Boolean).join(" · ") })),
      el("label", { class: "track-assign" },
        el("input", {
          type: "checkbox", checked: focusOn,
          onchange: (e) => assignTrack(t.id, "focus", e.target.checked),
          "aria-label": `${t.title_ko} 집중 목록에 넣기`,
        }), "집중"),
      el("label", { class: "track-assign" },
        el("input", {
          type: "checkbox", checked: breakOn,
          onchange: (e) => assignTrack(t.id, "break", e.target.checked),
          "aria-label": `${t.title_ko} 휴식 목록에 넣기`,
        }), "휴식"),
      el("div", { class: "track-actions" },
        el("button", {
          class: "icon-btn", type: "button", title: "위로", disabled: !orderable || idx === 0,
          "aria-label": `${t.title_ko} 위로`,
          onclick: () => reorderTrack(currentFilter, t.id, -1),
        }, "▲"),
        el("button", {
          class: "icon-btn", type: "button", title: "아래로",
          disabled: !orderable || idx === rows.length - 1,
          "aria-label": `${t.title_ko} 아래로`,
          onclick: () => reorderTrack(currentFilter, t.id, 1),
        }, "▼"),
        t.origin === "catalog" ? null : el("button", {
          class: "icon-btn icon-danger", type: "button", title: "삭제",
          "aria-label": `${t.title_ko} 삭제`,
          onclick: () => deleteTrack(t.id),
        }, "✕")));
  }));
}

// ── 크레딧 ───────────────────────────────────────────────────────────────────

/**
 * 확장 세트에는 CC BY-SA 음원이 포함된다 — 저작자 표시가 **의무**다.
 * CC0/PD 음원은 의무가 없지만 연주자를 밝히는 게 예의라 함께 보여준다.
 */
export async function renderCredits() {
  const host = $("#credits-list");
  if (!host) return;
  let items = [];
  try {
    items = await API.getCredits();
  } catch {
    host.replaceChildren(el("p", { class: "empty", text: "출처 정보를 불러오지 못했습니다." }));
    return;
  }
  if (!items.length) {
    host.replaceChildren(el("p", { class: "empty", text: "아직 내려받은 음원이 없습니다." }));
    return;
  }
  host.replaceChildren(...items.map((c) =>
    el("div", { class: "credit-row" },
      el("div", { class: "credit-title", text: c.album_ko ?? c.source_id }),
      el("div", { class: "credit-meta" },
        c.performer_ko ? el("span", { text: c.performer_ko }) : null,
        el("span", { class: `lic${c.requires_attribution ? " lic-attr" : ""}`, text: c.license ?? "" }),
        c.details_url
          ? el("a", { href: c.details_url, target: "_blank", rel: "noopener noreferrer" }, "출처")
          : null))));
}

// ── 초기화 ───────────────────────────────────────────────────────────────────

export function initPlaylistView() {
  for (const btn of $$("[data-filter]")) {
    btn.addEventListener("click", () => {
      currentFilter = btn.dataset.filter;
      for (const b of $$("[data-filter]")) {
        b.setAttribute("aria-pressed", b === btn ? "true" : "false");
      }
      renderPlaylistView();
    });
  }

  $("#file-input")?.addEventListener("change", (e) => {
    uploadTracks(e.target.files);
    e.target.value = "";
  });
  $("#btn-upload")?.addEventListener("click", () => $("#file-input")?.click());
  $("#btn-download-core")?.addEventListener("click", () => startDownload("core"));
  $("#btn-download-all")?.addEventListener("click", () => startDownload("all"));
  $("#btn-cancel-download")?.addEventListener("click", async () => {
    try {
      await API.cancelDownload();
      showToast("내려받기를 중단했습니다. 받던 파일은 다음에 이어받습니다.");
    } catch { /* 무시 */ }
    pollDownloadStatus();
  });
  $("#btn-import-folder")?.addEventListener("click", openFolderPicker);
}

// ── 폴더 가져오기 ────────────────────────────────────────────────────────────

async function openFolderPicker(startPath = null) {
  const { openModal, closeModal } = await import("./utils.js");
  let listing;
  try {
    listing = await API.listDir(startPath);
  } catch (e) {
    showToast(e?.message ?? "폴더를 열 수 없습니다.", { kind: "warn" });
    return;
  }

  const body = el("div", { class: "dir-picker" });
  const pathLine = el("div", { class: "dir-path", text: listing.path || "내 컴퓨터" });
  const list = el("div", { class: "dir-list" });
  const filesInfo = el("p", { class: "muted", text: "" });

  const renderListing = (data) => {
    pathLine.textContent = data.path || "내 컴퓨터";
    const entries = [];
    if (data.parent) {
      entries.push(el("button", {
        class: "dir-entry", type: "button",
        onclick: () => refresh(data.parent),
      }, "⬆ 상위 폴더"));
    }
    for (const d of data.drives ?? []) {
      entries.push(el("button", {
        class: "dir-entry", type: "button", onclick: () => refresh(d),
      }, `💾 ${d}`));
    }
    for (const d of data.entries ?? []) {
      entries.push(el("button", {
        class: "dir-entry", type: "button", onclick: () => refresh(d.path),
      }, `📁 ${d.name}`));
    }
    list.replaceChildren(...entries);
    const n = data.audio_count ?? 0;
    filesInfo.textContent = n
      ? `이 폴더에 오디오 파일 ${n}개가 있습니다.`
      : "이 폴더에는 오디오 파일이 없습니다.";
    filesInfo.dataset.path = data.path ?? "";
    filesInfo.dataset.count = String(n);
  };

  const refresh = async (path) => {
    try {
      renderListing(await API.listDir(path));
    } catch (e) {
      showToast(e?.message ?? "폴더를 열 수 없습니다.", { kind: "warn" });
    }
  };

  renderListing(listing);
  body.append(pathLine, list, filesInfo);

  openModal({
    title: "폴더에서 음원 가져오기",
    body,
    actions: [
      { label: "취소", onClick: ({ close }) => close() },
      {
        label: "이 폴더 가져오기",
        variant: "primary",
        onClick: async ({ close }) => {
          const path = filesInfo.dataset.path;
          if (!path || filesInfo.dataset.count === "0") {
            showToast("가져올 오디오 파일이 없습니다.", { kind: "warn" });
            return;
          }
          close();
          try {
            const scan = await API.scanFolder(path);
            const names = scan.files.map((f) => f.name);
            const res = await API.importFolder(path, names);
            await loadTracks();
            showToast(`${res.imported}개를 가져왔습니다.` +
              (res.skipped ? ` (${res.skipped}개 건너뜀)` : ""));
          } catch (e) {
            showToast(e?.message ?? "가져오지 못했습니다.", { kind: "warn" });
          }
        },
      },
    ],
  });
}
