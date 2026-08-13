import { beforeEach, describe, expect, it } from "vitest";
import {
  MAX_OFFSET_SEC,
  MIN_OFFSET_SEC,
  estimateLatencySec,
  loadOffset,
  saveOffset,
  secToSamples,
  shiftSamples,
} from "../src/audio/latency";

class MemoryStorage implements Storage {
  private data = new Map<string, string>();
  get length() {
    return this.data.size;
  }
  clear() {
    this.data.clear();
  }
  getItem(key: string) {
    return this.data.get(key) ?? null;
  }
  key(index: number) {
    return [...this.data.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.data.delete(key);
  }
  setItem(key: string, value: string) {
    this.data.set(key, value);
  }
}

describe("estimateLatencySec", () => {
  it("складывает базовую и выходную задержку", () => {
    expect(estimateLatencySec({ baseLatency: 0.01, outputLatency: 0.04 })).toBe(
      0.05,
    );
  });

  it("переживает отсутствие полей", () => {
    expect(estimateLatencySec({})).toBe(0);
  });
});

describe("secToSamples", () => {
  it("переводит секунды в сэмплы", () => {
    expect(secToSamples(0.1, 44100)).toBe(4410);
  });

  it("округляет до целого", () => {
    expect(Number.isInteger(secToSamples(0.0333, 44100))).toBe(true);
  });
});

describe("shiftSamples", () => {
  it("сдвигает содержимое назад, сохраняя длину", () => {
    const source = new Float32Array([0, 0, 1, 2, 3]);
    const result = shiftSamples(source, 2);
    expect(result.length).toBe(5);
    expect(Array.from(result.slice(0, 3))).toEqual([1, 2, 3]);
  });

  it("дополняет хвост нулями", () => {
    const result = shiftSamples(new Float32Array([1, 2, 3, 4]), 2);
    expect(Array.from(result)).toEqual([3, 4, 0, 0]);
  });

  it("сдвигает вперёд при отрицательном смещении", () => {
    const result = shiftSamples(new Float32Array([1, 2, 3, 4]), -2);
    expect(Array.from(result)).toEqual([0, 0, 1, 2]);
  });

  it("нулевое смещение ничего не меняет", () => {
    expect(Array.from(shiftSamples(new Float32Array([1, 2, 3]), 0))).toEqual([
      1, 2, 3,
    ]);
  });

  it("сдвиг длиннее буфера даёт тишину", () => {
    expect(Array.from(shiftSamples(new Float32Array([1, 2]), 10))).toEqual([
      0, 0,
    ]);
  });
});

describe("хранение смещения", () => {
  let storage: MemoryStorage;
  beforeEach(() => {
    storage = new MemoryStorage();
  });

  it("возвращает ноль, если ничего не сохранено", () => {
    expect(loadOffset(storage)).toBe(0);
  });

  it("переживает круговой рейс", () => {
    saveOffset(storage, 0.075);
    expect(loadOffset(storage)).toBeCloseTo(0.075, 5);
  });

  it("зажимает значение по границам", () => {
    saveOffset(storage, 5);
    expect(loadOffset(storage)).toBe(MAX_OFFSET_SEC);
    saveOffset(storage, -5);
    expect(loadOffset(storage)).toBe(MIN_OFFSET_SEC);
  });

  it("игнорирует испорченное значение", () => {
    storage.setItem("karaoke.latencyOffsetSec", "не число");
    expect(loadOffset(storage)).toBe(0);
  });
});
