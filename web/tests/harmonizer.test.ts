import { describe, expect, it } from "vitest";
import {
  DEFAULT_VOICES,
  MAX_SEMITONES,
  makeHarmony,
  pitchShift,
  resample,
  timeStretch,
} from "../src/audio/harmonizer";

const SR = 44100;

function sine(freq: number, seconds = 0.5): Float32Array<ArrayBuffer> {
  const data = new Float32Array(Math.round(SR * seconds));
  for (let i = 0; i < data.length; i += 1) {
    data[i] = Math.sin((2 * Math.PI * freq * i) / SR);
  }
  return data;
}

/** Частота по переходам через ноль: для чистого тона этого достаточно. */
function frequencyOf(data: Float32Array): number {
  let crossings = 0;
  for (let i = 1; i < data.length; i += 1) {
    if (data[i - 1] <= 0 && data[i] > 0) crossings += 1;
  }
  return (crossings * SR) / data.length;
}

/**
 * Частота по наилучшему совпадению с чистым тоном.
 *
 * Переходов через ноль здесь мало: они врут на долю процента, а слышно уже
 * десяток центов — то есть ими фальшь как раз и не поймать.
 */
function dominantFrequency(data: Float32Array, lo: number, hi: number): number {
  const from = Math.round(SR * 0.05);
  const to = data.length - Math.round(SR * 0.05);
  let best = { freq: lo, tone: -1 };

  for (let freq = lo; freq <= hi; freq += 0.05) {
    let re = 0;
    let im = 0;
    for (let i = from; i < to; i += 1) {
      const angle = (2 * Math.PI * freq * i) / SR;
      re += data[i] * Math.cos(angle);
      im += data[i] * Math.sin(angle);
    }
    const tone = re * re + im * im;
    if (tone > best.tone) best = { freq, tone };
  }

  return best.freq;
}

function centsOff(actual: number, target: number): number {
  return Math.abs(1200 * Math.log2(actual / target));
}

describe("timeStretch", () => {
  it("удлиняет сигнал в заданное число раз", () => {
    expect(timeStretch(sine(440, 0.2), 2).length).toBeCloseTo(
      Math.round(SR * 0.4),
      -2,
    );
  });

  it("не меняет высоту", () => {
    const stretched = timeStretch(sine(440), 1.5);
    expect(frequencyOf(stretched)).toBeGreaterThan(400);
    expect(frequencyOf(stretched)).toBeLessThan(480);
  });
});

describe("resample", () => {
  it("чтение вдвое быстрее поднимает тон на октаву", () => {
    expect(frequencyOf(resample(sine(440), 2))).toBeGreaterThan(800);
  });
});

describe("pitchShift", () => {
  it("сохраняет длину точно", () => {
    // Расхождение даже в сотню сэмплов слышно как расфазировка.
    const source = sine(440);
    expect(pitchShift(source, 4).length).toBe(source.length);
    expect(pitchShift(source, -5).length).toBe(source.length);
  });

  it("КЛЮЧЕВОЙ: попадает в ноту, а не рядом", () => {
    // Подпевка звучит одновременно с голосом, поэтому мимо ноты она не
    // «неточная», а фальшивая: две копии, разошедшиеся на десяток центов,
    // бьются с оригиналом, и это слышно как кваканье, а не как хор.
    // Пять центов — порог, ниже которого расхождение не различает никто.
    const target = 200 * Math.pow(2, 4 / 12);
    const shifted = pitchShift(sine(200), 4);

    expect(centsOff(dominantFrequency(shifted, 240, 265), target))
      .toBeLessThan(5);
  });

  it("КЛЮЧЕВОЙ: попадает в ноту и на квинте вниз", () => {
    const target = 200 * Math.pow(2, -7 / 12);
    const shifted = pitchShift(sine(200), -7);

    expect(centsOff(dominantFrequency(shifted, 125, 145), target))
      .toBeLessThan(5);
  });

  it("поднимает высоту на терцию", () => {
    // 440 Гц + 4 полутона = 554 Гц.
    const shifted = pitchShift(sine(440), 4);
    expect(frequencyOf(shifted)).toBeGreaterThan(510);
    expect(frequencyOf(shifted)).toBeLessThan(600);
  });

  it("опускает высоту", () => {
    const shifted = pitchShift(sine(440), -5);
    expect(frequencyOf(shifted)).toBeLessThan(400);
  });

  it("нулевой сдвиг оставляет высоту на месте", () => {
    expect(frequencyOf(pitchShift(sine(440), 0))).toBeGreaterThan(400);
    expect(frequencyOf(pitchShift(sine(440), 0))).toBeLessThan(480);
  });

  it("зажимает сдвиг по границам диапазона", () => {
    // За пределами диапазона слышен «хор роботов», поэтому вместо отказа —
    // ограничение: результат остаётся музыкальным.
    const wild = pitchShift(sine(220), 40);
    const edge = pitchShift(sine(220), MAX_SEMITONES);
    expect(frequencyOf(wild)).toBeCloseTo(frequencyOf(edge), -1);
  });

  it("тишина остаётся тишиной, а не шумом", () => {
    const silence = new Float32Array(SR * 0.1);
    expect(pitchShift(silence, 4).every((v) => v === 0)).toBe(true);
  });
});

describe("makeHarmony", () => {
  it("длина совпадает с дублем", () => {
    const voice = sine(220);
    expect(makeHarmony(voice).length).toBe(voice.length);
  });

  it("подпевка звучит, но тише голоса", () => {
    const voice = sine(220);
    const harmony = makeHarmony(voice);

    const peak = harmony.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
    expect(peak).toBeGreaterThan(0.1);
    expect(peak).toBeLessThan(1);
  });

  it("голоса расходятся во времени, а не сливаются в унисон", () => {
    // Идеальный унисон звучит как тот же голос, ставший громче.
    const voice = sine(220);
    const harmony = makeHarmony(voice);
    const firstDelay = DEFAULT_VOICES[0].delaySamples;

    expect(harmony.slice(0, firstDelay).every((v) => v === 0)).toBe(true);
  });

  it("пустой список голосов даёт тишину, а не ошибку", () => {
    expect(makeHarmony(sine(220), []).every((v) => v === 0)).toBe(true);
  });
});
