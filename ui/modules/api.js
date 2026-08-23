// 백엔드 REST 클라이언트. 오류는 여기서 한국어 메시지로 정규화한다.

import { errorMessage, localDateStr, toLocalISO } from "./utils.js";

class ApiError extends Error {
  constructor(status, detail) {
    super(errorMessage(status, detail));
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, { method = "GET", body, signal } = {}) {
  let resp;
  try {
    resp = await fetch(path, {
      method,
      signal,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    if (e?.name === "AbortError") throw e;
    throw new ApiError(0, null);   // 네트워크 자체가 실패 (서버 꺼짐 / 오프라인)
  }
  if (!resp.ok) {
    let detail = null;
    try {
      detail = (await resp.json())?.detail ?? null;
    } catch {
      /* 본문이 JSON 이 아닐 수 있다 */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return null;
  return resp.json();
}

export const API = {
  // 설정
  getSettings: () => request("/api/settings"),
  putSettings: (patch) => request("/api/settings", { method: "PUT", body: patch }),
  resetSettings: () => request("/api/settings/reset", { method: "POST", body: { confirm: true } }),

  // 기록 — ★ today 를 반드시 클라이언트 로컬 날짜로 보낸다.
  //   서버가 자기 시계로 "오늘"을 정하면 LAN 접속·시차 상황에서 하루가 어긋난다.
  getStats: (days = 14) =>
    request(`/api/stats/summary?days=${days}&today=${localDateStr()}`),
  postSession: (session) => request("/api/stats/sessions", { method: "POST", body: session }),
  getSessions: (limit = 50, offset = 0) =>
    request(`/api/stats/sessions?limit=${limit}&offset=${offset}`),
  getSessionsByDate: (date, limit = 200) =>
    request(`/api/stats/sessions?date=${date}&limit=${limit}`),
  resetStats: () => request("/api/stats/reset", { method: "POST", body: { confirm: true } }),

  // 작업 — today 는 클라이언트 로컬 날짜를 보낸다 (서버 시계로 정하면 어긋난다)
  getTasks: (days = 1) =>
    request(`/api/tasks?today=${localDateStr()}&days=${days}`),
  createTask: (name_ko, est) =>
    request("/api/tasks", { method: "POST",
      body: { name: name_ko, est_pomodoros: est, created_at: toLocalISO() } }),
  patchTask: (id, patch) =>
    request(`/api/tasks/${encodeURIComponent(id)}`,
            { method: "PATCH", body: { ...patch, at: toLocalISO() } }),
  deleteTask: (id) => request(`/api/tasks/${encodeURIComponent(id)}`, { method: "DELETE" }),
  reorderTasks: (ids) => request("/api/tasks/order", { method: "PUT", body: { ids } }),
  setActiveTask: (task_id) =>
    request("/api/tasks/active", { method: "PUT", body: { task_id } }),
  clearCompletedTasks: (today) =>
    request("/api/tasks/clear-completed", { method: "POST", body: { confirm: true, today } }),

  // 음원
  getTracks: () => request("/api/media/tracks"),
  getCatalog: () => request("/api/media/catalog"),
  getCredits: () => request("/api/media/credits"),
  getDownloadStatus: () => request("/api/media/status"),
  startDownload: (payload = { tier: "core" }) =>
    request("/api/media/download", { method: "POST", body: payload }),
  cancelDownload: () => request("/api/media/download/cancel", { method: "POST", body: {} }),
  patchTrack: (id, patch) =>
    request(`/api/media/tracks/${encodeURIComponent(id)}`, { method: "PATCH", body: patch }),
  deleteTrack: (id) =>
    request(`/api/media/tracks/${encodeURIComponent(id)}`, { method: "DELETE" }),
  listDir: (path) =>
    request(`/api/dirs${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  scanFolder: (path) =>
    request(`/api/media/scan-folder?path=${encodeURIComponent(path)}`),
  importFolder: (folder, names) =>
    request("/api/media/import-folder", { method: "POST", body: { folder, names } }),

  // 음원 검색 (archive.org)
  getSearchPresets: () => request("/api/media/search/presets"),
  searchMedia: (q, { page = 1, preset = null, signal } = {}) =>
    request(`/api/media/search?q=${encodeURIComponent(q)}&page=${page}`
            + (preset ? `&preset=${encodeURIComponent(preset)}` : ""), { signal }),
  getSearchItem: (identifier) =>
    request(`/api/media/search/item/${encodeURIComponent(identifier)}`),
  addSearchTracks: (payload) =>
    request("/api/media/search/add", { method: "POST", body: payload }),

  // 재생목록
  getPlaylists: () => request("/api/playlists"),
  getPlaylist: (pid) => request(`/api/playlists/${encodeURIComponent(pid)}`),
  createPlaylist: (name_ko) => request("/api/playlists", { method: "POST", body: { name_ko } }),
  updatePlaylist: (pid, patch) =>
    request(`/api/playlists/${encodeURIComponent(pid)}`, { method: "PUT", body: patch }),
  deletePlaylist: (pid) =>
    request(`/api/playlists/${encodeURIComponent(pid)}`, { method: "DELETE" }),

  /** 업로드는 진행률이 필요해 XHR 을 쓴다 (fetch 는 업로드 진행률을 주지 않는다). */
  uploadTrack(file, onProgress) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("file", file);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/media/upload");
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) onProgress?.(e.loaded / e.total);
      });
      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch {
            reject(new ApiError(500, null));
          }
        } else {
          let detail = null;
          try {
            detail = JSON.parse(xhr.responseText)?.detail ?? null;
          } catch { /* 무시 */ }
          reject(new ApiError(xhr.status, detail));
        }
      });
      xhr.addEventListener("error", () => reject(new ApiError(0, null)));
      xhr.send(form);
    });
  },
};

export { ApiError };
