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

// ★ 종류 간 체감 구분이 흐릿하다는 피드백으로 다시 설계했다. 6 종이 전부 브라운/핑크/
//   화이트 세 원시 버퍼(`base`) 중 하나를 공유하므로(캐시 키가 base 단위), 필터 컷오프만
//   살짝 다르면 "다른 소리"가 아니라 "같은 소리를 조금 다르게 자른 것"으로 들린다.
//   그래서 종류마다 ①스펙트럼을 뚜렷이 벌리고 ②고유한 움직임(LFO 패턴)을 준다 —
//   사람 귀는 정적인 스펙트럼 차이보다 시간에 따른 변화 패턴으로 소리를 더 잘 구분한다.
// ★ `makeup` — 필터를 많이 거칠수록(대역폭이 좁을수록) 같은 피크에서도 실제 체감 음량
//   (RMS)이 낮아진다. 전부 피크 0.7 로만 정규화하면 필터가 강한 종류(빗소리·선풍기)가
//   "그냥 더 작은 브라운 노이즈"처럼 들려 차이가 더 안 느껴진다. `renderNoiseOffline()` 로
//   6종 전부의 RMS 를 재서 공통 목표치(≈0.16, 6종 자연 평균과 가까워 보정폭이 가장
//   작다) 에 맞춘 보정값이다 — 필터 상수를 바꾸면 다시 재야 한다.
export const NOISE_TYPES = {
  brown: {
    id: "brown",
    name_ko: "브라운 노이즈",
    desc_ko: "깊고 둔중한 저음 소음. 오래 들어도 덜 피로합니다.",
    base: "brown",
    makeup: 1.0,
    build: (ctx) => {
      const lp = ctx.createBiquadFilter();
      lp.type = "lowpass";
      lp.frequency.value = 350;   // 확실히 어둡게 — 예전 1600Hz는 브라운 고유 롤오프에 묻혀 체감이 안 됐다
      return { chain: [lp] };
    },
  },
  pink: {
    id: "pink",
    name_ko: "핑크 노이즈",
    desc_ko: "자연에 가까운 균형 잡힌 소음. 말소리를 고르게 덮어 줍니다.",
    base: "pink",
    makeup: 1.0,
    build: () => ({ chain: [] }),
  },
  white: {
    id: "white",
    name_ko: "백색 소음",
    desc_ko: "모든 대역이 균일한 밝고 또렷한 소음. 마스킹 효과가 가장 강합니다.",
    base: "white",
    makeup: 0.37,
    build: (ctx) => {
      const hs = ctx.createBiquadFilter();
      hs.type = "highshelf";
      hs.frequency.value = 3500;
      hs.gain.value = 4;          // 핑크와 나란히 들었을 때 "밝다"는 게 분명히 느껴지게
      return { chain: [hs] };
    },
  },
  rain: {
    id: "rain",
    name_ko: "빗소리",
    desc_ko: "후두둑 떨어지는 빗방울 소리. 굵어졌다 가늘어지기를 반복합니다.",
    base: "white",
    makeup: 1.64,
    build: (ctx) => {
      const bp = ctx.createBiquadFilter();
      bp.type = "bandpass";
      bp.frequency.value = 2200;
      bp.Q.value = 0.6;
      const shelf = ctx.createBiquadFilter();
      shelf.type = "lowshelf";
      shelf.frequency.value = 220;
      shelf.gain.value = 6;          // 낮은 빗물 웅웅거림
      return {
        chain: [bp, shelf],
        // 느린 LFO = 소나기↔이슬비 강약, 빠른 LFO = 빗방울이 후두둑 떨어지는 잔떨림.
        // 두 개를 겹쳐야 "규칙적인 필터음"이 아니라 "빗소리"로 들린다.
        lfos: [
          { rate: 0.045, depth: 0.22 },
          { rate: 3.4, depth: 0.20 },
        ],
      };
    },
  },
  waves: {
    id: "waves",
    name_ko: "파도 소리",
    desc_ko: "천천히 밀려왔다 빠지는 파도. 호흡을 늦추는 데 좋습니다.",
    base: "brown",
    makeup: 1.19,
    build: (ctx) => {
      const lp = ctx.createBiquadFilter();
      lp.type = "lowpass";
      lp.frequency.value = 900;
      // 파도의 핵심은 느린 진폭 변화다 — 12초 남짓 주기가 사람 호흡과 비슷하다
      return { chain: [lp], lfos: [{ rate: 0.085, depth: 0.55, filter: lp, filterDepth: 500 }] };
    },
  },
  fan: {
    id: "fan",
    name_ko: "선풍기 소리",
    desc_ko: "또렷한 모터 웅웅거림이 있는 일정한 팬 소음. 변화가 거의 없어 배경으로 사라집니다.",
    base: "brown",
    makeup: 0.77,
    build: (ctx) => {
      const lp = ctx.createBiquadFilter();
      lp.type = "lowpass";
      lp.frequency.value = 380;
      const hum = ctx.createBiquadFilter();
      hum.type = "peaking";
      hum.frequency.value = 118;     // 모터 웅웅거림 기본 음
      hum.Q.value = 8;
      hum.gain.value = 11;
      const hum2 = ctx.createBiquadFilter();
      hum2.type = "peaking";
      hum2.frequency.value = 236;    // 2차 배음 — "기계가 돈다"는 느낌은 배음이 만든다
      hum2.Q.value = 8;
      hum2.gain.value = 6;
      return { chain: [lp, hum, hum2] };
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

  const { chain = [], lfos = [] } = spec.build(ctx) ?? {};
  const makeup = ctx.createGain();
  makeup.gain.value = spec.makeup ?? 1;
  const out = ctx.createGain();
  out.gain.value = 1;

  let node = src;
  for (const f of chain) {
    node.connect(f);
    node = f;
  }
  node.connect(makeup);
  makeup.connect(out);

  // 종류마다 고유한 움직임(빗발 강약, 파도 진폭, 후두둑 잔떨림) — 없으면 전부 똑같이
  // 정적인 소음으로 들린다. 여러 LFO 를 겹칠 수 있어 빗소리처럼 느린 강약 + 빠른
  // 잔떨림을 함께 줄 수 있다. 한 LFO 가 진폭(depth)과 필터 컷오프(filterDepth)를
  // 동시에 흔들 수도 있다 (파도 — 커지는 소리와 밝아지는 소리가 같이 온다).
  const ampDepthSum = lfos.reduce((sum, l) => sum + (l.depth ?? 0), 0);
  out.gain.value = Math.max(0, 1 - ampDepthSum);   // LFO 들이 더해지므로 기준값을 낮춘다
  const lfoOscs = [];
  const lfoGains = [];
  for (const lfo of lfos) {
    const osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.value = lfo.rate;
    lfoOscs.push(osc);

    if (lfo.depth) {
      const ampGain = ctx.createGain();
      ampGain.gain.value = lfo.depth;
      osc.connect(ampGain);
      ampGain.connect(out.gain);
      lfoGains.push(ampGain);
    }
    if (lfo.filter && lfo.filterDepth) {
      const filterGain = ctx.createGain();
      filterGain.gain.value = lfo.filterDepth;
      osc.connect(filterGain);
      filterGain.connect(lfo.filter.frequency);
      lfoGains.push(filterGain);
    }
  }

  return {
    output: out,
    start(when = 0) {
      src.start(when);
      for (const osc of lfoOscs) osc.start(when);
    },
    stop(when = 0) {
      try {
        src.stop(when);
      } catch { /* 이미 멈춤 */ }
      for (const osc of lfoOscs) {
        try {
          osc.stop(when);
        } catch { /* 무시 */ }
      }
      // 페이드아웃이 끝난 뒤 연결을 끊어 그래프가 쌓이지 않게 한다
      const delayMs = Math.max(0, (when - ctx.currentTime) * 1000) + 200;
      setTimeout(() => {
        try {
          out.disconnect();
          makeup.disconnect();
          for (const f of chain) f.disconnect();
          for (const g of lfoGains) g.disconnect();
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
