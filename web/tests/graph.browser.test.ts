import { describe, expect, it } from "vitest";
import { buildMixGraph } from "../src/audio/graph";
import { bufferToChannels, mixdown } from "../src/audio/mixdown";

const SR = 44100;

const BASE = {
  offsetSec: 0,
  voiceGain: 1,
  musicGain: 1,
  reverbWet: 0,
  watermark: false,
};

function toneBuffer(freq: number, durationSec: number): AudioBuffer {
  const ctx = new OfflineAudioContext(2, SR * durationSec, SR);
  const buffer = ctx.createBuffer(2, SR * durationSec, SR);
  for (let ch = 0; ch < 2; ch += 1) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i += 1) {
      data[i] = 0.25 * Math.sin((2 * Math.PI * freq * i) / SR);
    }
  }
  return buffer;
}

function toneChannels(freq: number, durationSec: number): Float32Array[] {
  return [0, 1].map(() => {
    const data = new Float32Array(SR * durationSec);
    for (let i = 0; i < data.length; i += 1) {
      data[i] = 0.25 * Math.sin((2 * Math.PI * freq * i) / SR);
    }
    return data;
  });
}

function rms(data: Float32Array, from: number, to: number): number {
  let sum = 0;
  for (let i = from; i < to; i += 1) sum += data[i] * data[i];
  return Math.sqrt(sum / (to - from));
}

describe("описатель графа", () => {
  it("КЛЮЧЕВОЙ: собирает тот же микс, что и экспорт", async () => {
    // Реверб здесь выключен намеренно: его импульс — затухающий шум от
    // Math.random, то есть два реверба не совпадают между собой ни при каких
    // условиях. Сравнивать имеет смысл всё остальное.
    const music = toneBuffer(220, 2);
    const voice = toneChannels(440, 2);
    const params = { ...BASE, voiceGain: 0.7, musicGain: 0.5, offsetSec: 0.03 };

    const expected = await mixdown(music, voice, SR, params);

    const ctx = new OfflineAudioContext(2, music.length, SR);
    const graph = buildMixGraph(ctx, { music, voice, harmony: null }, params);
    graph.output.connect(ctx.destination);
    graph.start();
    const actual = await ctx.startRendering();

    const [left] = bufferToChannels(actual);
    const [expectedLeft] = bufferToChannels(expected);
    for (let i = 0; i < left.length; i += 641) {
      expect(left[i]).toBeCloseTo(expectedLeft[i], 6);
    }
  });

  it("КЛЮЧЕВОЙ: громкость, поменянная на ходу, слышна сразу", async () => {
    const music = toneBuffer(220, 2);
    const voice = toneChannels(440, 2);

    const ctx = new OfflineAudioContext(2, music.length, SR);
    const graph = buildMixGraph(ctx, { music, voice, harmony: null }, BASE);
    graph.output.connect(ctx.destination);
    graph.start();
    // Через секунду минусовку убирают в ноль. Источник при этом не трогается:
    // если бы ползунок требовал пересборки, звук бы прервался.
    graph.setMusicGain(0, 1.0);
    const rendered = await ctx.startRendering();

    const [left] = bufferToChannels(rendered);
    const before = rms(left, 0, SR - 1000);
    const after = rms(left, SR * 1.5, SR * 2);

    expect(after).toBeLessThan(before * 0.75);
  });

  it("играет с середины, когда просят с середины", async () => {
    const music = toneBuffer(220, 2);
    const voice = toneChannels(440, 2);

    const ctx = new OfflineAudioContext(2, SR, SR);
    const graph = buildMixGraph(ctx, { music, voice, harmony: null }, BASE);
    graph.output.connect(ctx.destination);
    // Секунда с середины двухсекундной дорожки: звук есть и он не тишина.
    graph.start(0, 1.0);
    const rendered = await ctx.startRendering();

    const [left] = bufferToChannels(rendered);
    expect(rms(left, 0, SR)).toBeGreaterThan(0.05);
  });
});
