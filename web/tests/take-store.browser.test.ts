import { beforeEach, describe, expect, it } from "vitest";
import { clearTake, loadTake, saveTake } from "../src/audio/takeStore";

const SR = 44100;

function channels(value: number, length = 1000) {
  return [0, 1].map(() => new Float32Array(length).fill(value));
}

describe("takeStore", () => {
  beforeEach(async () => {
    await clearTake("t1");
    await clearTake("t2");
  });

  it("дубль переживает перезагрузку", async () => {
    await saveTake("t1", channels(0.5), SR);

    const restored = await loadTake("t1");

    expect(restored).not.toBeNull();
    expect(restored!.sampleRate).toBe(SR);
    expect(restored!.channels).toHaveLength(2);
    expect(restored!.channels[0][0]).toBeCloseTo(0.5, 5);
    expect(restored!.channels[0].length).toBe(1000);
  });

  it("чужой трек своего дубля не отдаёт", async () => {
    await saveTake("t1", channels(0.5), SR);

    expect(await loadTake("t2")).toBeNull();
  });

  it("незнакомый трек — это null, а не ошибка", async () => {
    expect(await loadTake("не сохраняли")).toBeNull();
  });

  it("новый дубль заменяет прежний, а не копится рядом", async () => {
    // Два дубля на трек — это история дублей, её никто не просил, а место
    // она ест мегабайтами.
    await saveTake("t1", channels(0.1), SR);
    await saveTake("t1", channels(0.9), SR);

    const restored = await loadTake("t1");

    expect(restored!.channels[0][0]).toBeCloseTo(0.9, 5);
  });

  it("после очистки дубля нет", async () => {
    await saveTake("t1", channels(0.5), SR);

    await clearTake("t1");

    expect(await loadTake("t1")).toBeNull();
  });

  it("очистка несуществующего не считается сбоем", async () => {
    await expect(clearTake("нет такого")).resolves.toBeUndefined();
  });
});
