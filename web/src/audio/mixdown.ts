import { secToSamples, shiftSamples } from "./latency";
import { createReverb } from "./reverb";
import type { Samples } from "./samples";
import { generateWatermark } from "./watermark";

export interface MixOptions {
  /** Задержка записи в секундах. Положительная — голос запоздал. */
  offsetSec: number;
  voiceGain: number;
  musicGain: number;
  /** Доля обработанного сигнала, 0..1. */
  reverbWet: number;
  /** Громкость подпевки, 0 — её нет вовсе. */
  harmonyGain?: number;
  watermark: boolean;
}

export function bufferToChannels(buffer: AudioBuffer): Samples[] {
  return Array.from({ length: buffer.numberOfChannels }, (_, ch) =>
    Float32Array.from(buffer.getChannelData(ch)),
  );
}

/** Подгоняет буфер под длину микса: лишнее режет, недостающее добивает тишиной. */
function fitToFrames(channel: Samples, frames: number): Samples {
  if (channel.length === frames) return channel;
  const fitted = new Float32Array(frames);
  fitted.set(channel.subarray(0, Math.min(frames, channel.length)));
  return fitted;
}

function toStereo(channels: Samples[]): Samples[] {
  if (channels.length >= 2) return channels.slice(0, 2);
  return [channels[0], channels[0]];
}

export async function mixdown(
  music: AudioBuffer,
  voice: Samples[],
  sampleRate: number,
  options: MixOptions,
  harmony: Samples[] | null = null,
): Promise<AudioBuffer> {
  const frames = music.length;
  const ctx = new OfflineAudioContext(2, frames, sampleRate);

  const master = ctx.createGain();
  master.connect(ctx.destination);

  // --- музыка ---
  const musicGain = ctx.createGain();
  musicGain.gain.value = options.musicGain;
  const musicSource = ctx.createBufferSource();
  musicSource.buffer = music;
  musicSource.connect(musicGain).connect(master);
  musicSource.start();

  // --- голос со сдвигом ---
  const offsetSamples = secToSamples(options.offsetSec, sampleRate);
  const shifted = toStereo(voice).map((channel) =>
    fitToFrames(shiftSamples(channel, offsetSamples), frames),
  );

  const voiceBuffer = ctx.createBuffer(2, frames, sampleRate);
  voiceBuffer.copyToChannel(shifted[0], 0);
  voiceBuffer.copyToChannel(shifted[1], 1);

  const voiceSource = ctx.createBufferSource();
  voiceSource.buffer = voiceBuffer;

  const voiceGain = ctx.createGain();
  voiceGain.gain.value = options.voiceGain;
  voiceSource.connect(voiceGain);

  const dry = ctx.createGain();
  dry.gain.value = 1 - options.reverbWet;
  voiceGain.connect(dry).connect(master);

  if (options.reverbWet > 0) {
    const wet = ctx.createGain();
    wet.gain.value = options.reverbWet;
    voiceGain.connect(createReverb(ctx)).connect(wet).connect(master);
  }

  voiceSource.start();

  // Подпевка выводится из того же дубля, поэтому и сдвигается так же: иначе
  // она разъедется с голосом ровно на величину компенсации задержки.
  const harmonyGain = options.harmonyGain ?? 0;
  if (harmony && harmonyGain > 0) {
    const alignedHarmony = toStereo(harmony).map((channel) =>
      fitToFrames(shiftSamples(channel, offsetSamples), frames),
    );
    const harmonyBuffer = ctx.createBuffer(2, frames, sampleRate);
    harmonyBuffer.copyToChannel(alignedHarmony[0], 0);
    harmonyBuffer.copyToChannel(alignedHarmony[1], 1);

    const harmonySource = ctx.createBufferSource();
    harmonySource.buffer = harmonyBuffer;
    const gain = ctx.createGain();
    gain.gain.value = harmonyGain;
    // Мимо реверба: подпевка и так размазана расхождением голосов, а
    // второй хвост превращает её в кашу.
    harmonySource.connect(gain).connect(master);
    harmonySource.start();
  }

  // --- водяной знак ---
  if (options.watermark) {
    const mark = generateWatermark(sampleRate, frames);
    const markBuffer = ctx.createBuffer(2, frames, sampleRate);
    markBuffer.copyToChannel(mark, 0);
    markBuffer.copyToChannel(mark, 1);

    const markSource = ctx.createBufferSource();
    markSource.buffer = markBuffer;
    markSource.connect(master);
    markSource.start();
  }

  const rendered = await ctx.startRendering();
  return limitToFullScale(rendered);
}

/**
 * Убирает перегрузку сведения, если она случилась.
 *
 * Голос и минусовка складываются с усилением до 2,0 каждый, сверху уходит
 * реверб и водяной знак — сумма легко выходит за единицу. Дальше её всё равно
 * зажмёт кодировщик, но зажмёт грязно: щелчками, ровно теми, о которых
 * приложение честно предупреждает на входе. Поэтому весь микс тише ровно
 * настолько, насколько вылез пик, и не тише: громкость — дело ползунков.
 */
function limitToFullScale(buffer: AudioBuffer): AudioBuffer {
  let peak = 0;
  for (let ch = 0; ch < buffer.numberOfChannels; ch += 1) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i += 1) {
      const value = Math.abs(data[i]);
      if (value > peak) peak = value;
    }
  }

  if (peak <= 1) return buffer;

  const gain = 1 / peak;
  for (let ch = 0; ch < buffer.numberOfChannels; ch += 1) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i += 1) data[i] *= gain;
  }
  return buffer;
}
