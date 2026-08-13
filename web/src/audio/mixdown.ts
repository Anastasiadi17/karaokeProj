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
  watermark: boolean;
}

export function bufferToChannels(buffer: AudioBuffer): Samples[] {
  return Array.from({ length: buffer.numberOfChannels }, (_, ch) =>
    Float32Array.from(buffer.getChannelData(ch)),
  );
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
  const shifted = toStereo(voice).map((channel) => {
    const aligned = shiftSamples(channel, offsetSamples);
    if (aligned.length === frames) return aligned;
    const fitted = new Float32Array(frames);
    fitted.set(aligned.subarray(0, Math.min(frames, aligned.length)));
    return fitted;
  });

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

  return ctx.startRendering();
}
