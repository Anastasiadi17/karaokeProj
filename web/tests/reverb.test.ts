import { describe, expect, it } from "vitest";
import { generateImpulse } from "../src/audio/reverb";

function rms(data: Float32Array, from: number, to: number): number {
  let sum = 0;
  for (let i = from; i < to; i += 1) sum += data[i] * data[i];
  return Math.sqrt(sum / (to - from));
}

describe("generateImpulse", () => {
  it("отдаёт два канала нужной длины", () => {
    const channels = generateImpulse(44100, 2.0, 2.0);
    expect(channels).toHaveLength(2);
    expect(channels[0].length).toBe(88200);
    expect(channels[1].length).toBe(88200);
  });

  it("затухает: конец тише начала", () => {
    const [left] = generateImpulse(44100, 2.0, 2.0);
    const head = rms(left, 0, 4410);
    const tail = rms(left, left.length - 4410, left.length);
    expect(tail).toBeLessThan(head * 0.5);
  });

  it("каналы различаются, иначе реверб звучит моно", () => {
    const [left, right] = generateImpulse(44100, 1.0, 2.0);
    let identical = 0;
    for (let i = 0; i < left.length; i += 1) {
      if (left[i] === right[i]) identical += 1;
    }
    expect(identical).toBeLessThan(left.length * 0.01);
  });

  it("остаётся в допустимом диапазоне", () => {
    const [left] = generateImpulse(44100, 1.0, 2.0);
    let outOfRange = 0;
    for (let i = 0; i < left.length; i += 1) {
      if (Math.abs(left[i]) > 1) outOfRange += 1;
    }
    expect(outOfRange).toBe(0);
  });

  it("большее значение decay даёт более быстрое затухание", () => {
    const [slow] = generateImpulse(44100, 2.0, 1.0);
    const [fast] = generateImpulse(44100, 2.0, 6.0);
    const at = slow.length - 4410;
    expect(rms(fast, at, fast.length)).toBeLessThan(rms(slow, at, slow.length));
  });
});
