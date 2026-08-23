// 알림음 합성 (Web Audio).
//
// 파일을 쓰지 않고 합성하는 이유: 뽀모도로 앱에서 종이 안 울리면 앱이 고장난 것이다.
// 합성하면 0바이트, 404 불가, 라이선스 문제 0, 오프라인에서도 항상 동작한다.
//
// 설득력 있는 종소리에는 **비조화(inharmonic) 배음**이 필요하다. 관종(tubular bell)의
// 모드비는 대략 1 : 2.76 : 5.40 : 8.93 이고, 높은 배음일수록 빨리 사라진다.
// 사인 오실레이터 몇 개에 각자 다른 감쇠를 주면 그럴듯해진다.

const BELL_PARTIALS = [
  // [배음비, 진폭, 감쇠계수(클수록 빨리 사라짐)]
  [1.0, 1.0, 1.0],
  [2.76, 0.55, 1.7],
  [5.4, 0.3, 2.6],
  [8.93, 0.17, 3.6],
  [11.2, 0.09, 4.8],
];

export const CHIMES = {
  bell: {
    id: "bell",
    name_ko: "종",
    // 완전5도로 두 번 — 차분하게 "끝났다"는 신호가 된다
    notes: [
      { freq: 660, at: 0.0, dur: 3.2, gain: 1.0 },
      { freq: 880, at: 0.42, dur: 3.6, gain: 0.85 },
    ],
    partials: BELL_PARTIALS,
    lowpass: 6000,
  },
  soft: {
    id: "soft",
    name_ko: "부드러운 종",
    notes: [
      { freq: 523.25, at: 0.0, dur: 3.6, gain: 0.9 },
      { freq: 659.25, at: 0.3, dur: 3.8, gain: 0.7 },
      { freq: 783.99, at: 0.6, dur: 4.0, gain: 0.55 },
    ],
    partials: [
      [1.0, 1.0, 0.8],
      [2.0, 0.28, 1.6],
      [2.76, 0.16, 2.4],
    ],
    lowpass: 3200,
  },
  digital: {
    id: "digital",
    name_ko: "디지털 알림",
    notes: [
      { freq: 880, at: 0.0, dur: 0.5, gain: 0.9 },
      { freq: 1174.66, at: 0.16, dur: 0.6, gain: 0.9 },
    ],
    partials: [
      [1.0, 1.0, 2.0],
      [2.0, 0.35, 3.0],
      [3.0, 0.12, 4.5],
    ],
    lowpass: 9000,
  },
};

export const CHIME_IDS = Object.keys(CHIMES);

/** 미리듣기/스모크 테스트가 총 길이를 알 수 있도록. */
export function chimeDuration(variantId) {
  const spec = CHIMES[variantId] ?? CHIMES.bell;
  return Math.max(...spec.notes.map((n) => n.at + n.dur)) + 0.1;
}

/**
 * 하나의 음을 그래프에 스케줄한다.
 *
 * 주의할 점 세 가지 — 전부 실제로 소리를 망가뜨린다:
 *   1. exponentialRampToValueAtTime 은 목표가 0 이면 예외를 던진다 → 0.0001 로 램프
 *   2. 어택을 즉시 주면 클릭음이 난다 → 약 4ms 램프
 *   3. currentTime 에 바로 스케줄하면 이미 지나간 시각이 될 수 있다 → 약간 뒤로
 */
function normalizers(spec) {
  // 배음들은 서로 다른 주파수지만 위상이 겹치는 순간이 있어 진폭이 그대로 더해진다.
  // 음(note)들도 잔향 구간에서 겹친다. 정규화하지 않으면 합이 1.0 을 넘어 하드 클리핑이
  // 나고, "차분한 종소리"가 아니라 거친 디지털 왜곡이 된다.
  const partialSum = spec.partials.reduce((a, [, amp]) => a + amp, 0) || 1;
  const noteSum = spec.notes.reduce((a, n) => a + n.gain, 0) || 1;
  return { partialSum, noteSum };
}

function scheduleNote(ctx, dest, note, partials, lowpassHz, when, masterGain) {
  const filter = ctx.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = lowpassHz;
  filter.connect(dest);

  const ATTACK = 0.004;
  const FLOOR = 0.0001;

  for (const [ratio, amp, decayMul] of partials) {
    const freq = note.freq * ratio;
    if (freq > (ctx.sampleRate / 2) * 0.95) continue;   // 나이퀴스트 넘으면 에일리어싱

    const osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.value = freq;

    const g = ctx.createGain();
    const peak = Math.max(FLOOR, amp * note.gain * masterGain);
    const dur = note.dur / decayMul;

    g.gain.setValueAtTime(FLOOR, when);
    g.gain.linearRampToValueAtTime(peak, when + ATTACK);
    g.gain.exponentialRampToValueAtTime(FLOOR, when + Math.max(0.05, dur));

    osc.connect(g);
    g.connect(filter);
    osc.start(when);
    osc.stop(when + Math.max(0.06, dur) + 0.05);
  }
}

/**
 * 알림음을 재생한다.
 * @returns {number} 총 길이(초) — 호출측이 더킹 복구 타이밍에 쓴다.
 */
export function playChime(ctx, dest, variantId = "bell", { gain = 1, delay = 0.02 } = {}) {
  const spec = CHIMES[variantId] ?? CHIMES.bell;
  const { partialSum, noteSum } = normalizers(spec);
  // HEADROOM: 겹침이 이론적 최악보다 항상 작으므로 약간 되살려 준다. 이 값은
  // renderChimeOffline() 로 peak < 1.0 을 확인하며 정한 것이다 (qa/smoke_audio.mjs).
  const HEADROOM = 1.75;
  const norm = Math.min(1, HEADROOM / (partialSum * noteSum));
  const t0 = ctx.currentTime + delay;
  for (const note of spec.notes) {
    scheduleNote(ctx, dest, note, spec.partials, spec.lowpass, t0 + note.at, gain * norm);
  }
  return chimeDuration(spec.id);
}

/**
 * ★ OfflineAudioContext 로 렌더링해 실제 파형을 돌려준다.
 *
 * 스모크 테스트가 "정말 소리가 나는가"를 결정론적으로 검증할 수 있게 하기 위한 것이다.
 * 스피커 출력을 관찰할 수 없는 헤드리스 환경에서도 RMS 를 재면 무음 회귀를 잡는다.
 */
export async function renderChimeOffline(variantId = "bell", { sampleRate = 44100 } = {}) {
  const seconds = Math.ceil(chimeDuration(variantId)) + 1;
  const OfflineCtx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  const ctx = new OfflineCtx(1, sampleRate * seconds, sampleRate);
  playChime(ctx, ctx.destination, variantId, { gain: 1, delay: 0 });
  const buffer = await ctx.startRendering();
  const data = buffer.getChannelData(0);
  let sum = 0;
  let peak = 0;
  for (let i = 0; i < data.length; i += 1) {
    sum += data[i] * data[i];
    const a = Math.abs(data[i]);
    if (a > peak) peak = a;
  }
  return { rms: Math.sqrt(sum / data.length), peak, length: data.length, sampleRate };
}
