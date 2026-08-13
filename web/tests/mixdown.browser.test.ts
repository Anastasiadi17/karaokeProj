import { describe, expect, it } from "vitest";
import { bufferToChannels, mixdown } from "../src/audio/mixdown";

const SR = 44100;

const BASE = {
  offsetSec: 0,
  voiceGain: 1,
  musicGain: 1,
  reverbWet: 0,
  watermark: false,
};

/** Буфер тишины с одиночным щелчком на заданной секунде. */
function clickBuffer(atSec: number, durationSec = 4): AudioBuffer {
  const ctx = new OfflineAudioContext(2, SR * durationSec, SR);
  const buffer = ctx.createBuffer(2, SR * durationSec, SR);
  const index = Math.round(atSec * SR);
  for (let ch = 0; ch < 2; ch += 1) {
    buffer.getChannelData(ch)[index] = 1;
  }
  return buffer;
}

function clickChannels(atSec: number, durationSec = 4): Float32Array[] {
  return [0, 1].map(() => {
    const data = new Float32Array(SR * durationSec);
    data[Math.round(atSec * SR)] = 1;
    return data;
  });
}

function peakIndex(data: Float32Array): number {
  let best = 0;
  let bestValue = 0;
  for (let i = 0; i < data.length; i += 1) {
    if (Math.abs(data[i]) > bestValue) {
      bestValue = Math.abs(data[i]);
      best = i;
    }
  }
  return best;
}

describe("mixdown", () => {
  it("КЛЮЧЕВОЙ: компенсация ставит запоздавший голос на место", async () => {
    const LATENCY = 0.12;
    const music = clickBuffer(1.0);
    const voice = clickChannels(1.0 + LATENCY);

    const mixed = await mixdown(music, voice, SR, {
      ...BASE,
      offsetSec: LATENCY,
      musicGain: 0,
    });

    const found = peakIndex(mixed.getChannelData(0));
    expect(Math.abs(found - SR * 1.0)).toBeLessThan(5);
  });

  it("без компенсации голос остаётся сдвинутым", async () => {
    const music = clickBuffer(1.0);
    const voice = clickChannels(1.12);

    const mixed = await mixdown(music, voice, SR, { ...BASE, musicGain: 0 });

    expect(peakIndex(mixed.getChannelData(0))).toBeGreaterThan(SR * 1.1);
  });

  it("длительность равна длительности музыки", async () => {
    const music = clickBuffer(1.0, 3);
    const mixed = await mixdown(music, clickChannels(1.0, 5), SR, BASE);
    expect(mixed.duration).toBeCloseTo(3, 2);
  });

  it("при нулевой громкости голоса остаётся только музыка", async () => {
    const music = clickBuffer(0.5);
    const mixed = await mixdown(music, clickChannels(2.0), SR, {
      ...BASE,
      voiceGain: 0,
    });
    expect(Math.abs(peakIndex(mixed.getChannelData(0)) - SR * 0.5)).toBeLessThan(
      5,
    );
  });

  it("при нулевой громкости музыки остаётся только голос", async () => {
    const music = clickBuffer(0.5);
    const mixed = await mixdown(music, clickChannels(2.0), SR, {
      ...BASE,
      musicGain: 0,
    });
    expect(Math.abs(peakIndex(mixed.getChannelData(0)) - SR * 2.0)).toBeLessThan(
      5,
    );
  });

  it("реверб продлевает звучание голоса", async () => {
    const music = clickBuffer(0.5, 4);
    const voice = clickChannels(0.5, 4);

    const dry = await mixdown(music, voice, SR, { ...BASE, musicGain: 0 });
    const wet = await mixdown(music, voice, SR, {
      ...BASE,
      musicGain: 0,
      reverbWet: 1,
    });

    const energyAfter = (buffer: AudioBuffer) => {
      const data = buffer.getChannelData(0);
      let sum = 0;
      for (let i = Math.round(SR * 0.7); i < data.length; i += 1) {
        sum += data[i] * data[i];
      }
      return sum;
    };

    expect(energyAfter(wet)).toBeGreaterThan(energyAfter(dry) * 10);
  });

  it("водяной знак слышен в начале", async () => {
    const silence = new OfflineAudioContext(2, SR * 2, SR).createBuffer(
      2,
      SR * 2,
      SR,
    );
    const voice = [new Float32Array(SR * 2), new Float32Array(SR * 2)];

    const marked = await mixdown(silence, voice, SR, {
      ...BASE,
      watermark: true,
    });

    let peak = 0;
    const data = marked.getChannelData(0);
    for (let i = 0; i < SR * 0.3; i += 1)
      peak = Math.max(peak, Math.abs(data[i]));
    expect(peak).toBeGreaterThan(0.01);
  });

  it("перегруженное сведение не выходит за полную шкалу", async () => {
    // Щелчок в музыке и щелчок в голосе в одной точке, оба усиления на
    // максимуме ползунка: сумма заведомо больше единицы.
    const music = clickBuffer(1.0, 2);
    const voice = clickChannels(1.0, 2);

    const mixed = await mixdown(music, voice, SR, {
      ...BASE,
      voiceGain: 2,
      musicGain: 2,
    });

    const data = mixed.getChannelData(0);
    let peak = 0;
    for (let i = 0; i < data.length; i += 1) {
      peak = Math.max(peak, Math.abs(data[i]));
    }

    expect(peak).toBeLessThanOrEqual(1);
    expect(peak).toBeGreaterThan(0.9);
  });

  it("не трогает сведение, которое и так в шкале", async () => {
    const music = clickBuffer(1.0, 2);
    const voice = clickChannels(1.5, 2);

    const mixed = await mixdown(music, voice, SR, {
      ...BASE,
      voiceGain: 0.5,
      musicGain: 0.5,
    });

    const data = mixed.getChannelData(0);
    expect(data[Math.round(SR * 1.5)]).toBeCloseTo(0.5, 3);
  });

  it("bufferToChannels отдаёт независимые копии", () => {
    const buffer = clickBuffer(1.0);
    const channels = bufferToChannels(buffer);
    channels[0][0] = 0.5;
    expect(buffer.getChannelData(0)[0]).toBe(0);
  });
});
