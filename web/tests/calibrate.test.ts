import { describe, expect, it } from "vitest";
import { findOnset, measureOffset } from "../src/audio/calibrate";

const SR = 44100;

/** Запись: тишина, слабый шум, затем щелчок в заданном месте. */
function recording(clickAt: number, options: { noise?: number; amplitude?: number } = {}) {
  const { noise = 0.001, amplitude = 0.6 } = options;
  const data = new Float32Array(SR);
  for (let i = 0; i < data.length; i += 1) {
    data[i] = (Math.sin(i * 12.9898) * 43758.5453 % 1) * noise;
  }
  for (let i = 0; i < 64; i += 1) {
    data[clickAt + i] = amplitude * (1 - i / 64);
  }
  return data;
}

describe("findOnset", () => {
  it("находит начало щелчка", () => {
    expect(findOnset(recording(5000))).toBeCloseTo(5000, -2);
  });

  it("в тишине щелчка нет", () => {
    expect(findOnset(new Float32Array(SR))).toBe(-1);
  });

  it("шум без щелчка не считается щелчком", () => {
    const quiet = recording(5000, { amplitude: 0.005, noise: 0.004 });
    expect(findOnset(quiet)).toBe(-1);
  });
});

describe("measureOffset", () => {
  it("меряет круг от издания до возврата", () => {
    // Щелчок издан на 1000-м сэмпле, вернулся на 5410-м: 100 мс.
    const result = measureOffset(recording(5410), 1000, SR);

    expect(result).not.toBeNull();
    expect(result!.offsetSec).toBeCloseTo(0.1, 3);
  });

  it("тишина в ответ — это null, а не ноль", () => {
    // Наушники вместо динамиков: щелчка микрофон не слышит. Соврать числом
    // хуже, чем признаться.
    expect(measureOffset(new Float32Array(SR), 1000, SR)).toBeNull();
  });

  it("щелчок раньше момента издания не считается ответом", () => {
    expect(measureOffset(recording(500), 1000, SR)).toBeNull();
  });

  it("уверенность выше на чистой записи, чем на шумной", () => {
    const clean = measureOffset(recording(5410, { noise: 0.0005 }), 1000, SR);
    const noisy = measureOffset(recording(5410, { noise: 0.05 }), 1000, SR);

    expect(clean!.confidence).toBeGreaterThan(noisy!.confidence);
  });
});
