import { describe, expect, it } from "vitest";
import { encodeWav, floatToPcm16, interleave } from "../src/audio/encode";

async function headerOf(blob: Blob) {
  const view = new DataView(await blob.arrayBuffer());
  const text = (offset: number) =>
    String.fromCharCode(...[0, 1, 2, 3].map((i) => view.getUint8(offset + i)));
  return {
    riff: text(0),
    wave: text(8),
    fmt: text(12),
    data: text(36),
    channels: view.getUint16(22, true),
    sampleRate: view.getUint32(24, true),
    bitsPerSample: view.getUint16(34, true),
    dataBytes: view.getUint32(40, true),
    byteLength: view.byteLength,
  };
}

describe("floatToPcm16", () => {
  it("отображает нуль в нуль", () => {
    expect(floatToPcm16(0)).toBe(0);
  });

  it("зажимает выход за пределы диапазона", () => {
    expect(floatToPcm16(2)).toBe(32767);
    expect(floatToPcm16(-2)).toBe(-32768);
  });
});

describe("interleave", () => {
  it("чередует сэмплы каналов", () => {
    const left = new Float32Array([1, 3, 5]);
    const right = new Float32Array([2, 4, 6]);
    expect(Array.from(interleave([left, right]))).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("оставляет моно без изменений", () => {
    expect(Array.from(interleave([new Float32Array([1, 2])]))).toEqual([1, 2]);
  });
});

describe("encodeWav", () => {
  it("пишет корректный заголовок", async () => {
    const blob = encodeWav(
      [new Float32Array(100), new Float32Array(100)],
      44100,
    );
    const h = await headerOf(blob);

    expect(h.riff).toBe("RIFF");
    expect(h.wave).toBe("WAVE");
    expect(h.fmt).toBe("fmt ");
    expect(h.data).toBe("data");
    expect(h.channels).toBe(2);
    expect(h.sampleRate).toBe(44100);
    expect(h.bitsPerSample).toBe(16);
  });

  it("считает размер данных как каналы × кадры × 2 байта", async () => {
    const h = await headerOf(
      encodeWav([new Float32Array(50), new Float32Array(50)], 48000),
    );
    expect(h.dataBytes).toBe(50 * 2 * 2);
    expect(h.byteLength).toBe(44 + 50 * 2 * 2);
  });

  it("переживает круговой рейс значений", async () => {
    const source = new Float32Array([0, 0.5, -0.5, 1, -1]);
    const blob = encodeWav([source], 44100);
    const view = new DataView(await blob.arrayBuffer());

    const decoded = Array.from({ length: source.length }, (_, i) =>
      view.getInt16(44 + i * 2, true),
    );

    expect(decoded[0]).toBe(0);
    expect(decoded[3]).toBe(32767);
    expect(decoded[4]).toBe(-32768);
    expect(Math.abs(decoded[1] / 32767 - 0.5)).toBeLessThan(0.001);
  });

  it("отдаёт blob с типом audio/wav", () => {
    expect(encodeWav([new Float32Array(10)], 44100).type).toBe("audio/wav");
  });
});
