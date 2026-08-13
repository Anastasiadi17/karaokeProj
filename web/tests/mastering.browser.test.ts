import { describe, expect, it } from "vitest";
import { TARGET_DBFS, master, measureLoudness } from "../src/audio/mastering";

const SR = 44100;

/** Синус заданной амплитуды: ровный сигнал, на котором громкость считается
 *  предсказуемо. */
function tone(amplitude: number, seconds = 1, channels = 2): AudioBuffer {
  const ctx = new OfflineAudioContext(channels, SR * seconds, SR);
  const buffer = ctx.createBuffer(channels, SR * seconds, SR);
  for (let ch = 0; ch < channels; ch += 1) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i += 1) {
      data[i] = amplitude * Math.sin((2 * Math.PI * 440 * i) / SR);
    }
  }
  return buffer;
}

function peak(buffer: AudioBuffer): number {
  let max = 0;
  for (let ch = 0; ch < buffer.numberOfChannels; ch += 1) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i += 1) {
      max = Math.max(max, Math.abs(data[i]));
    }
  }
  return max;
}

describe("measureLoudness", () => {
  it("синус амплитудой 1 даёт около −3 dBFS", () => {
    // RMS синуса — амплитуда / √2, то есть −3,01 dBFS. Число известно
    // заранее, поэтому проверка ловит ошибку в самой формуле.
    expect(measureLoudness(tone(1))).toBeCloseTo(-3.01, 1);
  });

  it("вдвое тише — на 6 дБ ниже", () => {
    expect(measureLoudness(tone(0.5))).toBeCloseTo(-9.03, 1);
  });

  it("тишина не ломает шкалу", () => {
    expect(measureLoudness(tone(0))).toBe(-Infinity);
  });
});

describe("master", () => {
  it("тихий вход становится громче", async () => {
    const quiet = tone(0.02);
    const before = measureLoudness(quiet);

    const mastered = await master(quiet);

    expect(measureLoudness(mastered)).toBeGreaterThan(before + 10);
  });

  it("подтягивает громкость к цели", async () => {
    const mastered = await master(tone(0.05));

    // Компрессия и срез низов немного уводят от цели — важно, что мы рядом,
    // а не что попали в точку.
    expect(measureLoudness(mastered)).toBeGreaterThan(TARGET_DBFS - 6);
    expect(measureLoudness(mastered)).toBeLessThan(TARGET_DBFS + 6);
  });

  it("громкий вход не выходит за шкалу", async () => {
    const mastered = await master(tone(0.95));

    expect(peak(mastered)).toBeLessThanOrEqual(1);
  });

  it("сохраняет длину и каналы", async () => {
    const source = tone(0.3, 2, 2);

    const mastered = await master(source);

    expect(mastered.length).toBe(source.length);
    expect(mastered.numberOfChannels).toBe(2);
    expect(mastered.sampleRate).toBe(SR);
  });

  it("тишину отдаёт как есть, а не как NaN", async () => {
    const silence = tone(0);

    const mastered = await master(silence);

    expect(Number.isNaN(mastered.getChannelData(0)[100])).toBe(false);
  });
});
