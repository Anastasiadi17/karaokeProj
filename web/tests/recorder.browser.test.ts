import { describe, expect, it } from "vitest";
import { MIC_CONSTRAINTS, Recorder, concatChunks } from "../src/audio/recorder";

describe("MIC_CONSTRAINTS", () => {
  it("отключает обработку, ломающую пение и тайминг", () => {
    const audio = MIC_CONSTRAINTS.audio as MediaTrackConstraints;
    expect(audio.echoCancellation).toBe(false);
    expect(audio.noiseSuppression).toBe(false);
    expect(audio.autoGainControl).toBe(false);
  });
});

describe("concatChunks", () => {
  it("склеивает куски по порядку", () => {
    const result = concatChunks([
      new Float32Array([1, 2]),
      new Float32Array([3]),
      new Float32Array([4, 5]),
    ]);
    expect(Array.from(result)).toEqual([1, 2, 3, 4, 5]);
  });

  it("пустой список даёт пустой буфер", () => {
    expect(concatChunks([]).length).toBe(0);
  });
});

describe("Recorder", () => {
  it("пишет сигнал из графа и отдаёт стерео", async () => {
    const ctx = new AudioContext({ sampleRate: 44100 });
    await ctx.audioWorklet.addModule("/recorder-worklet.js");

    const destination = ctx.createMediaStreamDestination();
    const osc = ctx.createOscillator();
    osc.frequency.value = 440;
    osc.connect(destination);
    osc.start();

    const recorder = new Recorder(ctx);
    await recorder.start(destination.stream);
    expect(recorder.isRecording).toBe(true);

    await new Promise((resolve) => setTimeout(resolve, 300));
    const channels = recorder.stop();

    expect(recorder.isRecording).toBe(false);
    expect(channels).toHaveLength(2);
    expect(channels[0].length).toBeGreaterThan(1000);

    const peak = channels[0].reduce((m, v) => Math.max(m, Math.abs(v)), 0);
    expect(peak).toBeGreaterThan(0.1);

    osc.stop();
    await ctx.close();
  });

  it("stop без start отдаёт пустые каналы", () => {
    const ctx = new AudioContext();
    const channels = new Recorder(ctx).stop();
    expect(channels[0].length).toBe(0);
    void ctx.close();
  });
});
