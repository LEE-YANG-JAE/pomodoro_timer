// 백색 소음 / 주변음 합성 (Web Audio).
//
// 차임과 같은 이유로 파일을 쓰지 않는다 — 0바이트, 404 불가, 라이선스 문제 없음,
// 오프라인 동작. 게다가 소음은 원래 길게 재생되므로 파일로 하면 수십 MB가 된다.
//
// ── 왜 소음이 도움이 되는가 (정직하게) ─────────────────────────────────────
// "백색 소음이 알파파를 유도한다" 같은 주장은 근거가 약하다. 실제로 뒷받침되는 기제는
// **에너지 마스킹**이다: 넓은 대역의 정상 소음이 주변의 말소리·간헐적 소음을 덮어
// 주의를 빼앗기지 않게 한다. 집중을 방해하는 가장 큰 요인이 "가사·말소리"라는 것과
// 같은 이야기다(irrelevant-speech effect). 그래서 이 앱은 소음을 "집중력 향상 장치"가
// 아니라 **소리 환경을 고르게 만드는 도구**로 다룬다.
// 또한 백색 소음은 고역이 강해 오래 들으면 피로하므로, 기본값은 저역이 강조된
// **브라운 노이즈**이고 음량 기본값도 낮게 잡는다.

const BUFFER_SECONDS = 8;
const XFADE_SECONDS = 0.05;   // 루프 이음매를 없애기 위한 등파워 크로스페이드

export const NOISE_TYPES = {
  brown: {
    id: "brown",
    name_ko: "브라운 노이즈",
    desc_ko: "저역이 강조된 부드러운 소음. 오래 들어도 덜 피로합니다.",
    base: "brown",
    build: (ctx) => {
      const lp = ctx.createBiquadFilter();
      lp.type = "lowpass";
      lp.frequency.value = 1600;
      return { chain: [lp] };
    },
  },
  pink: {
    id: "pink",
    name_ko: "핑크 노이즈",
    desc_ko: "자연에 가까운 균형 잡힌 소음. 말소리를 고르게 덮어 줍니다.",
    base: "pink",
    build: () => ({ chain: [] }),
  },
  white: {
    id: "white",
    name_ko: "백색 소음",
    desc_ko: "모든 대역이 균일한 고전적인 백색 소음. 마스킹 효과가 가장 강합니다.",
    base: "white",
    build: () => ({ chain: [] }),
  },
  rain: {
    id: "rain",
    name_ko: "빗소리",
    desc_ko: "창밖에 비가 내리는 듯한 소리.",
    base: "white",
    build: (ctx) => {
      const bp = ctx.createBiquadFilter();
      bp.type = "bandpass";
      bp.frequency.value = 1400;
      bp.Q.value = 0.35;
      const shelf = ctx.createBiquadFilter();
      shelf.type = "lowshelf";
      shelf.frequency.value = 220;
      shelf.gain.value = 8;          // 낮은 빗물 웅웅거림
      // 빗발이 굵어졌다 가늘어지는 아주 느린 변화
      return { chain: [bp, shelf], lfo: { rate: 0.05, depth: 0.12 } };
    },
  },
  waves: {
    id: "waves",
    name_ko: "파도 소리",
    desc_ko: "천천히 밀려왔다 빠지는 파도. 호흡을 늦추는 데 좋습니다.",
    base: "brown",
    build: (ctx) => {
      const lp = ctx.createBiquadFilter();
      lp.type = "lowpass";
      lp.frequency.value = 700;
      // 파도의 핵심은 느린 진폭 변화다 — 12초 남짓 주기가 사람 호흡과 비슷하다
      return { chain: [lp], lfo: { rate: 0.085, depth: 0.55, filter: lp, filterDepth: 500 } };
    },
  },
  fan: {
    id: "fan",
    name_ko: "선풍기 소리",
    desc_ko: "일정한 팬 소음. 변화가 거의 없어 배경으로 사라집니다.",
    base: "brown",
    build: (ctx) => {
      const lp = ctx.createBiquadFilter();
      lp.type = "lowpass";
      lp.frequency.value = 420;
      const hum = ctx.createBiquadFilter();
      hum.type = "peaking";
      hum.frequency.value = 118;     // 모터 웅웅거림
      hum.Q.value = 6;
      hum.gain.value = 7;
      return { chain: [lp, hum] };
    },
  },
};

export const NOISE_IDS = Object.keys(NOISE_TYPES);

// ── 버퍼 생성 ────────────────────────────────────────────────────────────────

function fillWhite(out) {
  for (let i = 0; i < out.length; i += 1) out[i] = Math.random() * 2 - 1;
}

/** Paul Kellet 의 refined pink noise 근사 — IIR 6단. */
function fillPink(out) {
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
  for (let i = 0; i < out.length; i += 1) {
    const w = Math.random() * 2 - 1;
    b0 = 0.99886 * b0 + w * 0.0555179;
    b1 = 0.99332 * b1 + w * 0.0750759;
    b2 = 0.96900 * b2 + w * 0.1538520;
    b3 = 0.86650 * b3 + w * 0.3104856;
    b4 = 0.55000 * b4 + w * 0.5329522;
    b5 = -0.7616 * b5 - w * 0.0168980;
    out[i] = b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362;
    b6 = w * 0.115926;
  }
}

/** 적분형 브라운(레드) 노이즈. 누적이라 DC 가 생기므로 뒤에서 제거한다. */
function fillBrown(out) {
  let last = 0;
  for (let i = 0; i < out.length; i += 1) {
    const w = Math.random() * 2 - 1;
    last = (last + 0.02 * w) / 1.02;
    out[i] = last;
  }
}

function removeDc(out) {
  let mean = 0;
  for (let i = 0; i < out.length; i += 1) mean += out[i];
  mean /= out.length;
  for (let i = 0; i < out.length; i += 1) out[i] -= mean;
}

function normalize(out, target = 0.7) {
  let peak = 0;
  for (let i = 0; i < out.length; i += 1) {
    const a = Math.abs(out[i]);
    if (a > peak) peak = a;
  }
  if (peak < 1e-9) return;
  const g = target / peak;
  for (let i = 0; i < out.length; i += 1) out[i] *= g;
}

/**
 * 루프 이음매 제거.
 *
 * 브라운 노이즈처럼 저역이 강한 신호는 버퍼 끝과 시작의 값 차이가 그대로 "툭" 하는
 * 클릭으로 들린다. 여분으로 만든 뒤쪽 구간을 앞쪽에 등파워로 겹쳐 이어 붙인다.
 * (선형 크로스페이드는 무작위 신호의 RMS 를 중간에서 3dB 떨어뜨리므로 sqrt 를 쓴다.)
 */
function seamlessLoop(raw, n, xfade) {
  for (let i = 0; i < xfade; i += 1) {
    const t = i / xfade;
    const a = Math.sqrt(t);
    const b = Math.sqrt(1 - t);
    raw[i] = raw[i] * a + raw[n + i] * b;
  }
  return raw.subarray(0, n);
}

const _cache = new Map();   // `${base}:${sampleRate}` → AudioBuffer

/**
 * 지정한 종류의 잡음 버퍼를 만든다. 스테레오 두 채널을 서로 다른 난수로 채워
 * 공간감을 준다 (모노 소음은 머릿속에서 울리는 느낌이 든다).
 */
export function makeNoiseBuffer(ctx, base) {
  const key = `${base}:${ctx.sampleRate}`;
  const hit = _cache.get(key);
  if (hit) return hit;

  const sr = ctx.sampleRate;
  const n = Math.floor(sr * BUFFER_SECONDS);
  const xfade = Math.floor(sr * XFADE_SECONDS);
  const buffer = ctx.createBuffer(2, n, sr);

  for (let ch = 0; ch < 2; ch += 1) {
    const raw = new Float32Array(n + xfade);
    if (base === "pink") fillPink(raw);
    else if (base === "brown") fillBrown(raw);
    else fillWhite(raw);

    removeDc(raw);
    const looped = seamlessLoop(raw, n, xfade);
    normalize(looped, 0.7);
    buffer.copyToChannel(looped, ch);
  }

  _cache.set(key, buffer);
  return buffer;
}

/**
 * 재생 그래프를 만든다. 반환된 stop() 을 부르면 필요한 노드가 정리된다.
 *
 * @returns {{ output: AudioNode, start: (when:number)=>void, stop: (when:number)=>void }}
 */
export function createNoiseSource(ctx, typeId) {
  const spec = NOISE_TYPES[typeId] ?? NOISE_TYPES.brown;
  const src = ctx.createBufferSource();
  src.buffer = makeNoiseBuffer(ctx, spec.base);
  src.loop = true;

  const { chain = [], lfo = null } = spec.build(ctx) ?? {};
  const out = ctx.createGain();
  out.gain.value = 1;

  let node = src;
  for (const f of chain) {
    node.connect(f);
    node = f;
  }
  node.connect(out);

  // 느린 흔들림 (빗발·파도) — 없으면 완전히 정적인 소음이 된다
  let lfoOsc = null;
  let lfoGain = null;
  let lfoFilterGain = null;
  if (lfo) {
    lfoOsc = ctx.createOscillator();
    lfoOsc.type = "sine";
    lfoOsc.frequency.value = lfo.rate;

    lfoGain = ctx.createGain();
    lfoGain.gain.value = lfo.depth;
    lfoOsc.connect(lfoGain);
    lfoGain.connect(out.gain);
    out.gain.value = 1 - lfo.depth;      // LFO 가 더해지므로 기준값을 낮춘다

    if (lfo.filter && lfo.filterDepth) {
      lfoFilterGain = ctx.createGain();
      lfoFilterGain.gain.value = lfo.filterDepth;
      lfoOsc.connect(lfoFilterGain);
      lfoFilterGain.connect(lfo.filter.frequency);
    }
  }

  return {
    output: out,
    start(when = 0) {
      src.start(when);
      lfoOsc?.start(when);
    },
    stop(when = 0) {
      try {
        src.stop(when);
      } catch { /* 이미 멈춤 */ }
      try {
        lfoOsc?.stop(when);
      } catch { /* 무시 */ }
      // 페이드아웃이 끝난 뒤 연결을 끊어 그래프가 쌓이지 않게 한다
      const delayMs = Math.max(0, (when - ctx.currentTime) * 1000) + 200;
      setTimeout(() => {
        try {
          out.disconnect();
          for (const f of chain) f.disconnect();
          lfoGain?.disconnect();
          lfoFilterGain?.disconnect();
        } catch { /* 무시 */ }
      }, delayMs);
    },
  };
}

/**
 * ★ OfflineAudioContext 로 렌더링해 실제 파형을 돌려준다 (차임과 같은 취지).
 * 스모크 테스트가 "정말 소리가 나는가 / 클리핑하지 않는가"를 결정론적으로 검증한다.
 */
export async function renderNoiseOffline(typeId = "brown", { seconds = 2, sampleRate = 44100 } = {}) {
  const OfflineCtx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  const ctx = new OfflineCtx(2, Math.floor(sampleRate * seconds), sampleRate);
  const src = createNoiseSource(ctx, typeId);
  src.output.connect(ctx.destination);
  src.start(0);
  const buffer = await ctx.startRendering();

  const data = buffer.getChannelData(0);
  let sum = 0;
  let peak = 0;
  for (let i = 0; i < data.length; i += 1) {
    sum += data[i] * data[i];
    const a = Math.abs(data[i]);
    if (a > peak) peak = a;
  }
  // 좌우 채널이 실제로 다른지 (모노로 뭉개지지 않았는지) 확인
  const right = buffer.getChannelData(1);
  let diff = 0;
  for (let i = 0; i < Math.min(2000, data.length); i += 1) diff += Math.abs(data[i] - right[i]);

  return {
    rms: Math.sqrt(sum / data.length),
    peak,
    stereoDiff: diff / Math.min(2000, data.length),
    seconds: buffer.duration,
  };
}
