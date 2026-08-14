import { buildMixGraph } from "./graph";
import type { MixParams, MixSources } from "./graph";
import { measureLoudness } from "./mastering";
import type { Samples } from "./samples";

/**
 * Сведение в файл: тот же граф, что играет вживую, но просчитанный целиком.
 *
 * Устройство микса живёт в `graph.ts` и здесь не повторяется — иначе файл и
 * прослушивание разъехались бы, как только кто-нибудь поправил бы одно из
 * двух описаний.
 */

export type MixOptions = MixParams;

export function bufferToChannels(buffer: AudioBuffer): Samples[] {
  return Array.from({ length: buffer.numberOfChannels }, (_, ch) =>
    Float32Array.from(buffer.getChannelData(ch)),
  );
}

/** Пик всего микса: число, которым живой граф уравнивается с файлом. */
export function peakOf(buffer: AudioBuffer): number {
  let peak = 0;
  for (let ch = 0; ch < buffer.numberOfChannels; ch += 1) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i += 1) {
      const value = Math.abs(data[i]);
      if (value > peak) peak = value;
    }
  }
  return peak;
}

/**
 * Меряет то, чего живой граф о себе не знает: пик и громкость всего микса.
 *
 * Оба числа существуют только у целого — просчитанного от начала до конца, —
 * а прослушивание играет с середины и вперёд не смотрит. Поэтому целое
 * считается один раз наперёд тем же графом, и живому пути отдаются готовые
 * множители. Ползунки после этого двигаются свободно, и чем дальше они уходят
 * от замеренного положения, тем старее числа: расхождение с файлом — доли
 * децибела, следующее «Прослушать» считает заново.
 */
export async function measureMix(
  sources: MixSources,
  params: MixParams,
): Promise<{ limit: number; loudnessDbfs: number }> {
  const { music } = sources;
  const ctx = new OfflineAudioContext(2, music.length, music.sampleRate);

  const graph = buildMixGraph(ctx, sources, params);
  graph.output.connect(ctx.destination);
  graph.start();

  const rendered = await ctx.startRendering();
  const peak = peakOf(rendered);
  const limit = peak > 1 ? 1 / peak : 1;

  // Тише ровно во столько же раз — значит и громкость ниже ровно на столько
  // же децибел. Второй проход ради этого не нужен.
  return {
    limit,
    loudnessDbfs: measureLoudness(rendered) + 20 * Math.log10(limit),
  };
}

export async function mixdown(
  music: AudioBuffer,
  voice: Samples[],
  sampleRate: number,
  options: MixOptions,
  harmony: Samples[] | null = null,
): Promise<AudioBuffer> {
  const ctx = new OfflineAudioContext(2, music.length, sampleRate);

  const graph = buildMixGraph(ctx, { music, voice, harmony }, options);
  graph.output.connect(ctx.destination);
  graph.start();

  return limitToFullScale(await ctx.startRendering());
}

/**
 * Убирает перегрузку сведения, если она случилась.
 *
 * Голос и минусовка складываются с усилением до 2,0 каждый, сверху уходит
 * реверб и водяной знак — сумма легко выходит за единицу. Дальше её всё равно
 * зажмёт кодировщик, но зажмёт грязно: щелчками, ровно теми, о которых
 * приложение честно предупреждает на входе. Поэтому весь микс тише ровно
 * настолько, насколько вылез пик, и не тише: громкость — дело ползунков.
 *
 * Это одно из двух мест, которые меряют микс целиком (второе — мастеринг), и
 * потому единственные, которых живой граф повторить не может: вживую «всего
 * микса» ещё не существует. Прослушивание берёт этот же множитель замером
 * наперёд — см. `useStudio`.
 */
function limitToFullScale(buffer: AudioBuffer): AudioBuffer {
  const peak = peakOf(buffer);
  if (peak <= 1) return buffer;

  const gain = 1 / peak;
  for (let ch = 0; ch < buffer.numberOfChannels; ch += 1) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i += 1) data[i] *= gain;
  }
  return buffer;
}
