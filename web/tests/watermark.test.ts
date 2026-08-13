import { describe, expect, it } from "vitest";
import {
  WATERMARK_GAIN,
  WATERMARK_INTERVAL_SEC,
  generateWatermark,
} from "../src/audio/watermark";

function peak(data: Float32Array, from: number, to: number): number {
  let max = 0;
  for (let i = from; i < Math.min(to, data.length); i += 1) {
    max = Math.max(max, Math.abs(data[i]));
  }
  return max;
}

describe("generateWatermark", () => {
  it("длина совпадает с длиной микса", () => {
    expect(generateWatermark(44100, 44100 * 10).length).toBe(441000);
  });

  it("ставит маркер в начале", () => {
    const track = generateWatermark(44100, 44100 * 5);
    expect(peak(track, 0, 4410)).toBeGreaterThan(0);
  });

  it("повторяет маркер через заданный интервал", () => {
    const sr = 44100;
    const track = generateWatermark(sr, sr * 70, { intervalSec: 30 });
    expect(peak(track, sr * 30, sr * 30 + 4410)).toBeGreaterThan(0);
    expect(peak(track, sr * 60, sr * 60 + 4410)).toBeGreaterThan(0);
  });

  it("между маркерами тишина", () => {
    const sr = 44100;
    const track = generateWatermark(sr, sr * 40, { intervalSec: 30 });
    expect(peak(track, sr * 10, sr * 20)).toBe(0);
  });

  it("не превышает заданную громкость", () => {
    const track = generateWatermark(44100, 44100 * 5, { gain: WATERMARK_GAIN });
    expect(peak(track, 0, track.length)).toBeLessThanOrEqual(WATERMARK_GAIN);
  });

  it("умолчание интервала соответствует константе", () => {
    const sr = 8000;
    const track = generateWatermark(sr, sr * (WATERMARK_INTERVAL_SEC + 2));
    const at = sr * WATERMARK_INTERVAL_SEC;
    expect(peak(track, at, at + 800)).toBeGreaterThan(0);
  });

  it("не выходит за границу буфера у самого конца", () => {
    const sr = 44100;
    expect(() =>
      generateWatermark(sr, sr * 30 + 10, { intervalSec: 30 }),
    ).not.toThrow();
  });
});
