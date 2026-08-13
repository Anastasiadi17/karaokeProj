/**
 * Реверб на свёртке с синтетической импульсной характеристикой.
 *
 * Затухающий шум вместо записанного отклика помещения: внешних файлов не
 * требует, лицензировать нечего, для «комнаты» звучит достаточно.
 */

import type { Samples } from "./samples";

export function generateImpulse(
  sampleRate: number,
  durationSec: number,
  decay: number,
): Samples[] {
  const length = Math.floor(sampleRate * durationSec);

  return [0, 1].map(() => {
    const channel = new Float32Array(length);
    for (let i = 0; i < length; i += 1) {
      const envelope = Math.pow(1 - i / length, decay);
      channel[i] = (Math.random() * 2 - 1) * envelope;
    }
    return channel;
  });
}

export function createReverb(
  ctx: BaseAudioContext,
  options: { durationSec?: number; decay?: number } = {},
): ConvolverNode {
  const { durationSec = 2.0, decay = 2.5 } = options;
  const channels = generateImpulse(ctx.sampleRate, durationSec, decay);

  const impulse = ctx.createBuffer(2, channels[0].length, ctx.sampleRate);
  impulse.copyToChannel(channels[0], 0);
  impulse.copyToChannel(channels[1], 1);

  const convolver = ctx.createConvolver();
  convolver.buffer = impulse;
  return convolver;
}
