import { describe, expect, it } from "vitest";
import { CLIP_THRESHOLD, dbFromPeak, peakOf } from "../src/audio/meter";

describe("peakOf", () => {
  it("находит максимум по модулю", () => {
    expect(peakOf(new Float32Array([0.1, -0.8, 0.3]))).toBeCloseTo(0.8, 5);
  });

  it("пустой кадр даёт ноль", () => {
    expect(peakOf(new Float32Array(0))).toBe(0);
  });

  it("тишина даёт ноль", () => {
    expect(peakOf(new Float32Array([0, 0, 0]))).toBe(0);
  });
});

describe("dbFromPeak", () => {
  it("единица соответствует нулю децибел", () => {
    expect(dbFromPeak(1)).toBeCloseTo(0, 5);
  });

  it("половина соответствует примерно минус шести", () => {
    expect(dbFromPeak(0.5)).toBeCloseTo(-6.02, 1);
  });

  it("тишина не даёт минус бесконечность", () => {
    expect(Number.isFinite(dbFromPeak(0))).toBe(true);
    expect(dbFromPeak(0)).toBeLessThanOrEqual(-100);
  });
});

describe("CLIP_THRESHOLD", () => {
  it("срабатывает до полной шкалы, чтобы успеть предупредить", () => {
    expect(CLIP_THRESHOLD).toBeGreaterThan(0.9);
    expect(CLIP_THRESHOLD).toBeLessThan(1);
  });
});
