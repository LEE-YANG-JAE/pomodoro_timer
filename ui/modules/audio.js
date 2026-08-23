// 음악 재생 엔진.
//
// 그래프:
//     deck0 ─┐
//            ├─ musicBus ─┐
//     deck1 ─┘            │
//     noise ── noiseBus ──┼─ master → destination
//     chime ──────────────┘
//
// 소음(noise)은 음악과 **독립된 레이어**다. 음악 없이 소음만 틀 수도 있고, 클래식 위에
// 얹어 주변 말소리를 덮을 수도 있다. 볼륨도 따로 가진다.
//
// <audio>.volume 램프 대신 Web Audio GainNode 를 쓰는 이유: 크로스페이드·더킹·마스터
// 음소거를 한 그래프에서 샘플 정확도로 다룰 수 있다. 음원이 같은 오리진이라 CORS 문제는
// 없다. ★ <audio> 에 crossOrigin 을 설정하지 않는다 — 설정하면 더 엄격해질 뿐이다.

import { LS, emit, lsGet, lsSet, state } from "./state.js";
import { CHIMES, playChime } from "./chime.js";
import { createNoiseSource } from "./noise.js";
import { showToast } from "./utils.js";

let ctx = null;
let master = null;
let musicBus = null;
let chimeBus = null;
let noiseBus = null;
let noiseSource = null;      // 현재 재생 중인 소음 그래프
const decks = [];          // { audio, gain, source }
let duckTimer = null;

const FLOOR = 0.0001;

/** 슬라이더 0~100 → 인지 음량에 가까운 게인 (제곱 곡선). */
function volToGain(pct) {
  const v = Math.min(100, Math.max(0, Number(pct) || 0)) / 100;
  return v * v;
}

/** 이 기기에 저장된 음량 override 가 있으면 서버 기본값보다 우선한다. */
export function effectiveVolumes() {
  const s = state.settings.audio;
  const override = lsGet(LS.VOLUME, null) || {};
  return {
    music: override.music ?? s.music_volume,
    chime: override.chime ?? s.chime_volume,
    noise: override.noise ?? s.noise_volume,
  };
}

export function setDeviceVolume(kind, pct) {
  const override = lsGet(LS.VOLUME, null) || {};
  override[kind] = pct;
  lsSet(LS.VOLUME, override);
  if (kind === "music") applyMusicVolume();
  if (kind === "noise") applyNoiseVolume();
}

function applyMusicVolume() {
  if (!musicBus) return;
  const { music } = effectiveVolumes();
  musicBus.gain.value = state.audio.muted ? 0 : volToGain(music);
}

function applyNoiseVolume() {
  if (!noiseBus) return;
  const { noise } = effectiveVolumes();
  noiseBus.gain.value = state.audio.muted ? 0 : volToGain(noise);
}

// ── 언락 ─────────────────────────────────────────────────────────────────────

/**
 * ★ 반드시 사용자 제스처(시작 버튼) 안에서 호출해야 한다.
 *
 * 브라우저는 제스처 전까지 오디오를 막고 AudioContext 는 suspended 로 생성된다.
 * 여기서 한 번 열어 두면 이후의 모든 자동 위상 전환은 제스처 없이 재생된다.
 */
export async function ensureAudio() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) {
      state.audio.supported = false;
      return false;
    }
    ctx = new AC();

    master = ctx.createGain();
    master.gain.value = 1;
    master.connect(ctx.destination);

    musicBus = ctx.createGain();
    musicBus.connect(master);

    chimeBus = ctx.createGain();
    chimeBus.gain.value = 1;
    chimeBus.connect(master);

    noiseBus = ctx.createGain();
    noiseBus.gain.value = 0;
    noiseBus.connect(master);

    for (let i = 0; i < 2; i += 1) {
      const audio = new Audio();
      audio.preload = "auto";
      audio.crossOrigin = null;      // 같은 오리진 — 설정하면 더 엄격해지기만 한다
      const gain = ctx.createGain();
      gain.gain.value = 0;
      const source = ctx.createMediaElementSource(audio);
      source.connect(gain);
      gain.connect(musicBus);
      audio.addEventListener("ended", () => onTrackEnded(i));
      audio.addEventListener("error", () => onTrackError(i));
      decks.push({ audio, gain, source });
    }
    applyMusicVolume();
    applyNoiseVolume();
  }

  try {
    if (ctx.state !== "running") await ctx.resume();
  } catch {
    /* 아래에서 상태로 판정한다 */
  }
  state.audio.ctxState = ctx.state;
  state.audio.unlocked = ctx.state === "running";
  if (!state.audio.unlocked) {
    state.audio.blocked = true;
    emit("audio:blocked", {});
  } else if (state.audio.blocked) {
    state.audio.blocked = false;
    emit("audio:unblocked", {});
  }
  return state.audio.unlocked;
}

export function isAudioReady() {
  return Boolean(ctx) && ctx.state === "running";
}

export function getContext() {
  return ctx;
}

/** iOS 는 백그라운드에서 컨텍스트를 interrupted 로 떨어뜨린다 — 돌아오면 되살린다. */
export function initAudioLifecycle() {
  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState !== "visible" || !ctx) return;
    if (ctx.state !== "running") {
      try {
        await ctx.resume();
      } catch { /* 무시 */ }
      state.audio.ctxState = ctx.state;
      if (ctx.state === "running" && state.audio.blocked) {
        state.audio.blocked = false;
        emit("audio:unblocked", {});
      }
    }
  });
}

// ── 재생목록 ─────────────────────────────────────────────────────────────────

/** 위상 → 사용할 재생목록 키 ("focus" | "break") */
function listKeyFor(phase) {
  return phase === "focus" ? "focus" : "break";
}

function playlistIdFor(key) {
  const a = state.settings.audio;
  if (key === "focus") return a.focus_playlist_id;
  return a.break_playlist_id;
}

function shuffled(arr) {
  const out = arr.slice();
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/** 재생목록 → 실제 재생 가능한(ready) 트랙 id 순서를 만든다. */
export function rebuildOrder(key, { keepIfSame = false } = {}) {
  const pid = playlistIdFor(key);
  const pl = state.playlists.find((p) => p.id === pid);
  const readyIds = new Set(state.tracks.filter((t) => t.ready).map((t) => t.id));
  let ids = (pl?.track_ids ?? []).filter(
    (id) => readyIds.has(id) && !state.audio.failed.has(id));

  const shuffle =
    key === "focus" ? state.settings.audio.shuffle_focus : state.settings.audio.shuffle_break;
  if (shuffle) ids = shuffled(ids);

  // 복원된 순서가 같은 곡 집합이면 그대로 둔다 — 새로고침 때마다 셔플이 다시
  // 섞이면 "이어듣기" 가 아니라 매번 새 재생이 된다.
  const prev = state.audio.order[key] ?? [];
  if (keepIfSame && prev.length === ids.length
      && new Set(prev).size === new Set([...prev, ...ids]).size) {
    return prev;
  }
  state.audio.order[key] = ids;
  if (state.audio.index[key] >= ids.length) state.audio.index[key] = 0;
  return ids;
}

function trackById(id) {
  return state.tracks.find((t) => t.id === id) ?? null;
}

// ── 덱 재생 ──────────────────────────────────────────────────────────────────

function idleDeck() {
  return state.audio.activeDeck === 0 ? 1 : 0;
}

function fade(gainNode, to, seconds) {
  if (!ctx) return;
  const now = ctx.currentTime;
  const g = gainNode.gain;
  g.cancelScheduledValues(now);
  g.setValueAtTime(Math.max(FLOOR, g.value), now);
  if (seconds <= 0) {
    g.setValueAtTime(to, now);
  } else {
    g.linearRampToValueAtTime(to, now + seconds);
  }
}

async function playOnDeck(deckIndex, track, { fadeSec, startAt = 0 }) {
  const deck = decks[deckIndex];
  if (!deck || !track?.url) return false;
  deck.audio.src = track.url;
  // ★ 메타데이터가 오기 전에 currentTime 을 넣으면 무시된다. loadedmetadata 를 기다린다.
  if (startAt > 0) {
    await new Promise((res) => {
      const done = () => { deck.audio.removeEventListener("loadedmetadata", done); res(); };
      deck.audio.addEventListener("loadedmetadata", done, { once: true });
      setTimeout(done, 3000);      // 못 받아도 재생은 진행한다
    });
    try {
      if (Number.isFinite(deck.audio.duration) && startAt < deck.audio.duration - 1) {
        deck.audio.currentTime = startAt;
      }
    } catch { /* 무시 */ }
  } else {
    deck.audio.currentTime = 0;
  }
  try {
    await deck.audio.play();
  } catch (e) {
    // ★ play() 는 자동재생 차단 말고도 여러 이유로 reject 한다. 전부 "차단" 으로
    //   처리하면 소리가 멀쩡히 나는데도 차단 배너가 뜬다.
    //   - AbortError    : 크로스페이드로 src 를 바꾸거나 pause() 해서 이전 play() 가 취소됨
    //                     → 정상 동작이다. 무시한다.
    //   - NotAllowedError: 진짜 자동재생 차단. 단, AudioContext 가 running 이면
    //                     이미 언락된 것이므로 차단이 아니다.
    //   - 그 외          : 디코드 실패 / 404 → 트랙 실패로 처리해 다음 곡으로 넘어간다.
    const name = e?.name;
    if (name === "AbortError") return false;
    if (name === "NotAllowedError" && !isAudioReady()) {
      state.audio.blocked = true;
      emit("audio:blocked", {});
    } else if (name !== "NotAllowedError") {
      onTrackError(deckIndex);
    }
    return false;
  }
  // ★ 재생시간이 비어 있는 트랙(업로드·폴더 가져오기·검색에서 값이 의심스러운 경우)은
  //   브라우저가 아는 값을 서버에 돌려준다. 세 경로가 같은 구멍을 쓰므로 여기 한 곳에서 닫는다.
  if (track.duration_seconds == null) backfillDuration(deck.audio, track);

  // 실제로 재생이 시작됐다 — 차단 상태였다면 해제한다
  if (state.audio.blocked) {
    state.audio.blocked = false;
    emit("audio:unblocked", {});
  }
  fade(deck.gain, 1, fadeSec);
  state.audio.activeDeck = deckIndex;
  state.audio.current = track;
  emit("audio:track", track);
  saveAudioState();
  return true;
}

/** loadedmetadata 의 duration 을 서버에 PATCH 한다. 실패해도 재생에는 영향이 없다.
 * ★ 카탈로그 트랙은 대상에서 뺀다 — 서버가 "사용자가 추가한 음원만 수정 가능"으로 항상
 *   404 를 낸다(server/routes/media.py:patch_track). 실측 결과 카탈로그 곡을 재생할 때마다
 *   콘솔에 404 가 쌓이는 원인이 이것이었다 — 실패는 조용히 삼켜지지만 매번 헛된 요청이 나갔다. */
function backfillDuration(audio, track) {
  if (track.origin === "catalog") return;
  const send = async () => {
    const d = audio.duration;
    if (!Number.isFinite(d) || d < 1) return;
    const secs = Math.round(d);
    try {
      const { API } = await import("./api.js");
      await API.patchTrack(track.id, { duration_seconds: secs });
      const t = state.tracks.find((x) => x.id === track.id);
      if (t) t.duration_seconds = secs;
    } catch {
      /* 재생시간은 부가 정보다 — 조용히 넘긴다 */
    }
  };
  if (Number.isFinite(audio.duration) && audio.duration > 0) send();
  else audio.addEventListener("loadedmetadata", send, { once: true });
}

function stopDeck(deckIndex, fadeSec) {
  const deck = decks[deckIndex];
  if (!deck) return;
  fade(deck.gain, FLOOR, fadeSec);
  const ms = Math.max(0, fadeSec * 1000);
  setTimeout(() => {
    try {
      deck.audio.pause();
      deck.audio.removeAttribute("src");
      deck.audio.load();
    } catch { /* 무시 */ }
  }, ms + 60);
}

function currentKey() {
  return listKeyFor(state.timer.phase);
}

function onTrackEnded() {
  nextTrack({ auto: true });
}

function onTrackError(deckIndex) {
  const cur = state.audio.current;
  if (cur) state.audio.failed.add(cur.id);
  // ★ 실패마다 토스트를 띄우면 전 트랙이 깨졌을 때 화면이 토스트로 뒤덮인다.
  //   한 번만 알리고 조용히 다음 곡으로 넘어간다.
  if (!state.audio.playlistDead) {
    const key = currentKey();
    const remaining = (state.audio.order[key] ?? []).filter(
      (id) => !state.audio.failed.has(id));
    if (remaining.length === 0) {
      state.audio.playlistDead = true;
      showToast("재생할 수 있는 음원이 없습니다. 타이머는 계속 진행됩니다.", { kind: "warn" });
      return;
    }
  }
  nextTrack({ auto: true });
}

// ── 공개 조작 ────────────────────────────────────────────────────────────────

export async function playPlaylist(phase, { restart = false, fadeSec = null, force = false } = {}) {
  if (state.settings.audio.silent_mode) return false;
  // 사용자가 음악을 직접 멈춰 뒀다면 자동 재생으로 되살리지 않는다
  if (state.audio.userPaused && !force) return false;
  if (!isAudioReady()) return false;

  const key = listKeyFor(phase);
  const ids = rebuildOrder(key);
  if (!ids.length) {
    // ★ 음원이 없어도 앱은 완전히 동작해야 한다. 조용히 넘어간다.
    if (!state.audio.warnedEmpty) {
      state.audio.warnedEmpty = true;
      emit("audio:empty", { key });
    }
    return false;
  }
  if (restart) state.audio.index[key] = 0;

  const fadeS = fadeSec ?? state.settings.audio.crossfade_seconds;
  const id = ids[state.audio.index[key] % ids.length];
  const track = trackById(id);
  if (!track) return false;

  const prev = state.audio.activeDeck;
  const target = idleDeck();
  const ok = await playOnDeck(target, track, { fadeSec: fadeS });
  if (ok && prev !== target) stopDeck(prev, fadeS);
  return ok;
}

/**
 * 목록에서 고른 곡을 바로 재생한다.
 *
 * 현재 위상의 목록에 그 곡이 있으면 인덱스를 맞춰 두어, 곡이 끝난 뒤 자동으로
 * 그 다음 곡으로 이어지게 한다. 목록에 없으면(다른 목록의 곡) 한 곡만 재생한다.
 */
export async function playTrackById(trackId, { fadeSec = 0.4 } = {}) {
  await ensureAudio();
  if (!isAudioReady()) return false;

  const track = trackById(trackId);
  if (!track?.ready || !track.url) return false;

  const key = currentKey();
  const ids = state.audio.order[key] ?? [];
  const at = ids.indexOf(trackId);
  if (at >= 0) {
    state.audio.index[key] = at;
  } else {
    // 현재 목록 밖의 곡 — 임시로 맨 앞에 끼워 넣어 이어듣기가 자연스럽게 되도록
    state.audio.order[key] = [trackId, ...ids];
    state.audio.index[key] = 0;
  }

  state.audio.userPaused = false;
  const prev = state.audio.activeDeck;
  const target = idleDeck();
  const ok = await playOnDeck(target, track, { fadeSec });
  if (ok && prev !== target) stopDeck(prev, fadeSec);
  return ok;
}

/**
 * 사용자가 직접 음악만 일시정지 / 재개한다 (타이머와 무관).
 *
 * ★ userPaused 플래그가 필요한 이유: 이 플래그가 없으면 다음 위상 전환이나 틱에서
 *   playPlaylist() 가 다시 재생을 시작해 사용자의 선택이 무시된다.
 */
export async function toggleMusicPause() {
  if (state.audio.userPaused) {
    state.audio.userPaused = false;
    if (!state.audio.current) await playPlaylist(state.timer.phase);
    else await resumeMusic();
  } else {
    state.audio.userPaused = true;
    pauseMusic();
  }
  saveAudioState();
  emit("audio:paused", { paused: state.audio.userPaused });
  return state.audio.userPaused;
}

export function isMusicPaused() {
  return Boolean(state.audio.userPaused);
}

/** 재생 순서 — 순서대로 / 무작위. 두 목록에 함께 적용한다. */
export function setShuffle(on) {
  state.settings.audio.shuffle_focus = on;
  state.settings.audio.shuffle_break = on;
  const cur = currentKey();
  const playing = state.audio.current?.id;
  rebuildOrder("focus");
  rebuildOrder("break");
  // 지금 듣고 있는 곡은 유지한다 — 순서를 바꿨다고 곡이 끊기면 당황스럽다
  if (playing) {
    const at = (state.audio.order[cur] ?? []).indexOf(playing);
    if (at >= 0) state.audio.index[cur] = at;
  }
  saveAudioState();
  emit("audio:shuffle", { shuffle: on });
  return on;
}

export function isShuffle() {
  return Boolean(state.settings.audio.shuffle_focus);
}

// ── 재생 위치 영속화 ─────────────────────────────────────────────────────────
//
// 새로고침해도 듣던 곡의 그 지점부터 이어지게 한다. 셔플 순서까지 함께 저장해야
// 다음 곡이 달라지지 않는다.

let persistTimer = null;

export function saveAudioState() {
  const deck = decks[state.audio.activeDeck];
  let pos = 0;
  try {
    pos = deck?.audio?.currentTime ?? 0;
  } catch { /* 무시 */ }
  lsSet(LS.AUDIO, {
    trackId: state.audio.current?.id ?? null,
    positionSec: pos,
    index: state.audio.index,
    order: state.audio.order,
    // ★ userPaused 는 저장하지 않는다. "지금 잠깐 음악만 멈춤" 은 그 순간의 상태이지
    //   설정이 아니다. 새로고침하면 **기본값인 재생 상태**로 돌아온다.
    savedAt: Date.now(),
  });
}

export function initAudioPersistence() {
  clearInterval(persistTimer);
  persistTimer = setInterval(() => {
    if (state.audio.current) saveAudioState();
  }, 5000);
  window.addEventListener("beforeunload", saveAudioState);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") saveAudioState();
  });
}

/**
 * 새로고침 전에 듣던 곡을 그 위치부터 다시 재생한다.
 *
 * @returns {"resumed"|"blocked"|"none"}
 *   blocked 는 브라우저 자동재생 정책에 막힌 것 — 호출측이 "소리 켜기" 배너를 띄운다.
 */
export async function restorePlayback() {
  const saved = lsGet(LS.AUDIO, null);
  if (!saved?.trackId) return "none";

  // 순서를 그대로 되살려야 이어지는 곡이 바뀌지 않는다
  if (saved.order && typeof saved.order === "object") state.audio.order = saved.order;
  if (saved.index && typeof saved.index === "object") state.audio.index = saved.index;
  state.audio.userPaused = false;      // 새로고침하면 기본값(재생)으로 돌아온다

  const track = trackById(saved.trackId);
  if (!track?.ready) return "none";

  await ensureAudio();
  if (!isAudioReady()) return "blocked";

  const ok = await playOnDeck(idleDeck(), track, {
    fadeSec: 0.8,
    startAt: Math.max(0, Number(saved.positionSec) || 0),
  });
  return ok ? "resumed" : "blocked";
}

export function nextTrack({ auto = false } = {}) {
  const key = currentKey();
  const ids = state.audio.order[key] ?? [];
  if (!ids.length) return;
  state.audio.index[key] = (state.audio.index[key] + 1) % ids.length;
  playPlaylist(state.timer.phase, { fadeSec: auto ? state.settings.audio.crossfade_seconds : 0.4 });
}

export function prevTrack() {
  const key = currentKey();
  const ids = state.audio.order[key] ?? [];
  if (!ids.length) return;
  state.audio.index[key] = (state.audio.index[key] - 1 + ids.length) % ids.length;
  playPlaylist(state.timer.phase, { fadeSec: 0.4 });
}

export function pauseMusic() {
  for (const d of decks) {
    try { d.audio.pause(); } catch { /* 무시 */ }
  }
}

export async function resumeMusic() {
  if (state.settings.audio.silent_mode || !isAudioReady()) return;
  if (state.audio.userPaused) return;
  const deck = decks[state.audio.activeDeck];
  if (deck?.audio.src) {
    try { await deck.audio.play(); } catch { /* 무시 */ }
  }
}

export function stopMusic({ fadeSec = 0.6 } = {}) {
  for (let i = 0; i < decks.length; i += 1) stopDeck(i, fadeSec);
  state.audio.current = null;
  emit("audio:track", null);
}

export function toggleMute() {
  state.audio.muted = !state.audio.muted;
  applyMusicVolume();
  applyNoiseVolume();
  return state.audio.muted;
}

export function setMusicVolume(pct) {
  setDeviceVolume("music", pct);
}

export function setChimeVolume(pct) {
  setDeviceVolume("chime", pct);
}

// ── 백색 소음 ────────────────────────────────────────────────────────────────

/** 현재 위상에서 소음을 틀어야 하는가. */
export function noiseWantedFor(phase) {
  const a = state.settings.audio;
  if (!a.noise_enabled) return false;
  return a.noise_phases === "all" ? true : phase === "focus";
}

/**
 * 소음을 켠다. 이미 같은 종류가 돌고 있으면 아무것도 하지 않는다
 * (매 틱·매 전환마다 재생성하면 끊기고 그래프가 쌓인다).
 */
export function startNoise({ fadeSec = 1.5 } = {}) {
  if (!isAudioReady() || !noiseBus) return false;
  const typeId = state.settings.audio.noise_type;
  if (noiseSource && noiseSource.typeId === typeId) {
    applyNoiseVolume();
    state.audio.noisePlaying = true;
    return true;
  }
  stopNoise({ fadeSec: 0.4 });

  const src = createNoiseSource(ctx, typeId);
  src.typeId = typeId;
  const envelope = ctx.createGain();
  envelope.gain.value = FLOOR;
  src.output.connect(envelope);
  envelope.connect(noiseBus);
  src.envelope = envelope;
  src.start(ctx.currentTime + 0.02);
  fade(envelope, 1, fadeSec);        // 갑자기 "쉬-" 하고 시작하지 않게

  if (state.audio.blocked) {
    state.audio.blocked = false;
    emit("audio:unblocked", {});
  }
  noiseSource = src;
  state.audio.noisePlaying = true;
  state.audio.noiseType = typeId;
  applyNoiseVolume();
  emit("audio:noise", { playing: true, type: typeId });
  return true;
}

export function stopNoise({ fadeSec = 1.0 } = {}) {
  if (!noiseSource) {
    state.audio.noisePlaying = false;
    return;
  }
  const src = noiseSource;
  noiseSource = null;
  state.audio.noisePlaying = false;
  if (src.envelope) fade(src.envelope, FLOOR, fadeSec);
  src.stop(ctx.currentTime + fadeSec + 0.05);
  emit("audio:noise", { playing: false, type: src.typeId });
}

/**
 * 위상이 바뀌거나 설정이 바뀔 때 호출 — 켤지 끌지 알아서 판단한다.
 *
 * ★ 타이머가 "running" 일 때만 트는 조건은 두지 않는다. 소음은 주변음이라
 *   타이머를 시작하기 전에 켜 보는 게 자연스럽고, 그때 아무 소리도 안 나면
 *   사용자는 기능이 고장난 줄 안다. 다만 **일시정지 중에는 멈춘다** —
 *   잠깐 자리를 비운 것이므로.
 */
export function syncNoise(phase = state.timer.phase, { fadeSec = 1.5 } = {}) {
  if (!isAudioReady()) return;
  const paused = state.timer.status === "paused" || state.timer.status === "interrupted";
  if (noiseWantedFor(phase) && !paused) {
    startNoise({ fadeSec });
  } else {
    stopNoise({ fadeSec: 0.8 });
  }
}

/**
 * 사용자가 소음 버튼을 직접 눌렀을 때.
 * 지금 구간이 noise_phases 범위 밖이면 그 사실을 알려 준다 —
 * 버튼만 켜지고 소리가 안 나면 고장으로 오해한다.
 * @returns {"on"|"off"|"on-later"}
 */
export async function toggleNoiseNow() {
  await ensureAudio();
  const a = state.settings.audio;
  a.noise_enabled = !a.noise_enabled;

  if (!a.noise_enabled) {
    stopNoise({ fadeSec: 0.6 });
    return "off";
  }
  if (noiseWantedFor(state.timer.phase)) {
    startNoise({ fadeSec: 0.8 });
    return "on";
  }
  return "on-later";      // 켜지긴 했지만 지금 구간에는 재생되지 않는다
}

export function setNoiseVolume(pct) {
  setDeviceVolume("noise", pct);
}

/** 설정 화면에서 종류를 바꿨을 때 — 돌고 있으면 부드럽게 갈아끼운다. */
export async function previewNoise(typeId) {
  await ensureAudio();
  if (!isAudioReady()) return;
  state.settings.audio.noise_type = typeId;
  // 이미 돌고 있으면 부드럽게 갈아끼우고, 켜져 있는데 안 돌고 있으면 시작해 들려준다
  if (noiseSource || state.settings.audio.noise_enabled) startNoise({ fadeSec: 0.6 });
}

export function isNoisePlaying() {
  return Boolean(noiseSource);
}

/** 차임이 울리는 동안 음악을 낮췄다가 되돌린다. */
export function duck(seconds, depth = 0.15) {
  if (!state.settings.audio.duck_on_chime) return;
  const { music, noise } = effectiveVolumes();
  const muted = state.audio.muted;
  // ★ 소음도 함께 낮춘다. 소음만 틀어 둔 상태에서 차임이 그 위에 묻히면
  //   "구간이 끝났다"는 신호를 놓치게 된다.
  const targets = [
    [musicBus, muted ? 0 : volToGain(music)],
    [noiseBus, muted ? 0 : volToGain(noise)],
  ];
  for (const [bus, normal] of targets) {
    if (!bus) continue;
    fade(bus, Math.max(FLOOR, normal * depth), 0.25);
  }
  clearTimeout(duckTimer);
  duckTimer = setTimeout(() => {
    for (const [bus, normal] of targets) {
      if (bus) fade(bus, normal, 0.8);
    }
  }, seconds * 1000);
}

/**
 * 알림음 재생. 음악이 없어도, 재생목록이 비어도 항상 울려야 한다.
 *
 * ★ 계단식으로 터뜨리지 않고 음악 아래에서 시작해 ~1.2초에 걸쳐 커지게 한다.
 *   이탈 원인 1위가 "알람이 몰입을 끊는 것" 이다. 같은 소리라도 크레셴도로 들어오면
 *   훨씬 덜 놀란다.
 * @param {boolean} insistent 휴식이 끝날 때는 더 또렷하게 — 돌아오는 게 멈추는 것보다 어렵다.
 */
export function ring(variant = null, { insistent = false, rampSec = 1.2 } = {}) {
  if (!isAudioReady() || !state.settings.audio.chime_enabled) return 0;
  const { chime } = effectiveVolumes();
  const id = variant ?? state.settings.audio.chime_variant;
  const target = volToGain(chime) * (insistent ? 1.0 : 0.85);

  // chimeBus 를 낮은 값에서 목표까지 끌어올린다
  const now = ctx.currentTime;
  const g = chimeBus.gain;
  g.cancelScheduledValues(now);
  g.setValueAtTime(Math.max(FLOOR, target * 0.25), now);
  g.linearRampToValueAtTime(1, now + (insistent ? rampSec * 0.5 : rampSec));

  const dur = playChime(ctx, chimeBus, id, { gain: target });
  // 휴식 종료는 한 번 더 — 이어지는 소리가 있어야 자리로 돌아온다
  if (insistent) {
    setTimeout(() => {
      if (isAudioReady()) playChime(ctx, chimeBus, id, { gain: target * 0.9 });
    }, 1400);
  }
  duck(dur + (insistent ? 2.0 : 0.5));
  return dur;
}

export async function previewChime(variant) {
  await ensureAudio();
  if (!isAudioReady()) return;
  const { chime } = effectiveVolumes();
  playChime(ctx, chimeBus, variant, { gain: volToGain(chime) });
}

export function clearFailures() {
  state.audio.failed.clear();
  state.audio.playlistDead = false;
}

/**
 * 위상 전환 시퀀스 — 차임을 울리고, 음악이 그 위를 밟지 않게 잠깐 뒤에 새 목록을 시작한다.
 */
export async function runPhaseTransition({ from, to, playChimeNow = true }) {
  if (!isAudioReady()) return;
  let wait = 0;
  // 휴식이 끝날 때(= 다음이 집중)는 더 또렷하게 울린다
  if (playChimeNow) wait = ring(null, { insistent: to === "focus" });
  const fadeSec = state.settings.audio.crossfade_seconds;
  // 차임의 앞부분(약 1.2초)만 피하면 충분하다 — 잔향은 음악과 겹쳐도 자연스럽다
  const delayMs = Math.min(1200, wait * 300);
  setTimeout(() => {
    playPlaylist(to, { fadeSec });
    syncNoise(to, { fadeSec });
  }, delayMs);
}

export { CHIMES };
