/**
 * Описатель микса: одни и те же узлы для файла и для живого прослушивания.
 *
 * Существует ради единственного правила, на котором держится студия: человек
 * должен слышать то, что скачает. Пока сборка была одна и офлайновая, правило
 * держалось само собой. Живой пульт — это второй способ звучать, а два способа
 * расходятся молча, и первым об этом узнаёт не разработчик, а тот, кто уже
 * скачал файл.
 *
 * Поэтому граф описан здесь ровно один раз и строится на любом
 * `BaseAudioContext`: экспорт строит его на `OfflineAudioContext` и рендерит,
 * прослушивание — на живом и держит ручки под ползунками.
 */

import { secToSamples, shiftSamples } from "./latency";
import { createReverb } from "./reverb";
import type { Samples } from "./samples";
import { generateWatermark } from "./watermark";

export interface MixParams {
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

export interface MixSources {
  music: AudioBuffer;
  voice: Samples[];
  /** Готовая подпевка. Считается снаружи: это самая тяжёлая арифметика. */
  harmony: Samples[] | null;
}

export interface MixGraph {
  /** Выход микса. Его подключает тот, кто строил: к динамикам или к рендеру. */
  output: GainNode;
  /** @param offsetSec с какого места дорожки играть. */
  start(when?: number, offsetSec?: number): void;
  stop(): void;
  /** Вызывается, когда микс доиграл сам. Ручная остановка его не зовёт. */
  onEnded(handler: () => void): void;
  setVoiceGain(value: number, at?: number): void;
  setMusicGain(value: number, at?: number): void;
  setHarmonyGain(value: number, at?: number): void;
  setReverbWet(value: number, at?: number): void;
}

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

/** Сдвигает дорожку на компенсацию задержки и подгоняет под длину микса. */
function aligned(
  ctx: BaseAudioContext,
  channels: Samples[],
  offsetSamples: number,
  frames: number,
): AudioBuffer {
  const prepared = toStereo(channels).map((channel) =>
    fitToFrames(shiftSamples(channel, offsetSamples), frames),
  );
  const buffer = ctx.createBuffer(2, frames, ctx.sampleRate);
  buffer.copyToChannel(prepared[0], 0);
  buffer.copyToChannel(prepared[1], 1);
  return buffer;
}

export function buildMixGraph(
  ctx: BaseAudioContext,
  sources: MixSources,
  params: MixParams,
): MixGraph {
  const frames = sources.music.length;
  const offsetSamples = secToSamples(params.offsetSec, ctx.sampleRate);

  const output = ctx.createGain();

  // --- музыка ---
  const musicGain = ctx.createGain();
  musicGain.gain.value = params.musicGain;
  const musicSource = ctx.createBufferSource();
  musicSource.buffer = sources.music;
  musicSource.connect(musicGain).connect(output);

  // --- голос со сдвигом ---
  const voiceSource = ctx.createBufferSource();
  voiceSource.buffer = aligned(ctx, sources.voice, offsetSamples, frames);

  const voiceGain = ctx.createGain();
  voiceGain.gain.value = params.voiceGain;
  voiceSource.connect(voiceGain);

  const dry = ctx.createGain();
  dry.gain.value = 1 - params.reverbWet;
  voiceGain.connect(dry).connect(output);

  // Ветка реверба строится всегда, даже на нуле: иначе ползунок «Реверб»
  // требовал бы пересборки графа, то есть разрыва звука. На нуле она даёт
  // ровный ноль и в файл ничего не приносит.
  const wet = ctx.createGain();
  wet.gain.value = params.reverbWet;
  voiceGain.connect(createReverb(ctx)).connect(wet).connect(output);

  // --- подпевка ---
  // Выводится из того же дубля, поэтому и сдвигается так же: иначе она
  // разъедется с голосом ровно на величину компенсации задержки.
  const harmonyGain = ctx.createGain();
  harmonyGain.gain.value = params.harmonyGain ?? 0;
  let harmonySource: AudioBufferSourceNode | null = null;
  if (sources.harmony) {
    harmonySource = ctx.createBufferSource();
    harmonySource.buffer = aligned(
      ctx, sources.harmony, offsetSamples, frames,
    );
    // Мимо реверба: подпевка и так размазана расхождением голосов, а второй
    // хвост превращает её в кашу.
    harmonySource.connect(harmonyGain).connect(output);
  }

  // --- водяной знак ---
  let markSource: AudioBufferSourceNode | null = null;
  if (params.watermark) {
    const mark = generateWatermark(ctx.sampleRate, frames);
    const markBuffer = ctx.createBuffer(2, frames, ctx.sampleRate);
    markBuffer.copyToChannel(mark, 0);
    markBuffer.copyToChannel(mark, 1);
    markSource = ctx.createBufferSource();
    markSource.buffer = markBuffer;
    markSource.connect(output);
  }

  const sourceNodes = [musicSource, voiceSource, harmonySource, markSource]
    .filter((node): node is AudioBufferSourceNode => node !== null);

  // Плавно, а не рывком: мгновенная смена громкости слышна щелчком, и на
  // ползунке, который тащат пальцем, это была бы очередь щелчков.
  const ramp = (param: AudioParam, value: number, at?: number) => {
    param.setTargetAtTime(value, at ?? ctx.currentTime, 0.01);
  };

  let started = false;
  let ended: (() => void) | null = null;

  // Конец микса — это конец минусовки: все буферы графа подогнаны под её
  // длину. Ручная остановка снимает обработчик до `stop()`, поэтому «доиграл»
  // и «остановили» не путаются между собой.
  musicSource.addEventListener("ended", () => ended?.());

  return {
    output,

    start(when = 0, offsetSec = 0) {
      if (started) return;
      started = true;
      for (const node of sourceNodes) node.start(when, offsetSec);
    },

    stop() {
      if (!started) return;
      started = false;
      ended = null;
      for (const node of sourceNodes) {
        try {
          node.stop();
        } catch {
          // Источник мог не успеть стартовать — тогда останавливать нечего.
        }
      }
    },

    onEnded(handler) {
      ended = handler;
    },

    setVoiceGain: (value, at) => ramp(voiceGain.gain, value, at),
    setMusicGain: (value, at) => ramp(musicGain.gain, value, at),
    setHarmonyGain: (value, at) => ramp(harmonyGain.gain, value, at),
    setReverbWet: (value, at) => {
      const clamped = Math.max(0, Math.min(1, value));
      ramp(wet.gain, clamped, at);
      ramp(dry.gain, 1 - clamped, at);
    },
  };
}
