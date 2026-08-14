/**
 * Автокалибровка смещения записи по щелчку.
 *
 * Идея простая: издать короткий щелчок, записать его микрофоном и посмотреть,
 * через сколько он вернулся. Это и есть круг «выход → воздух → вход», то самое
 * смещение, которое иначе подбирают ползунком.
 *
 * ГЛАВНОЕ ОГРАНИЧЕНИЕ, и оно не обходится: нужен звук через динамики. В
 * наушниках микрофон щелчка не слышит, и калибровка честно об этом говорит,
 * а не выдаёт случайное число. Поэтому она — помощник, а не замена ползунку.
 */

import type { Samples } from "./samples";

/** Ниже этой доли от пика всё считается фоном комнаты, а не щелчком. */
const ONSET_FRACTION = 0.35;
/** Тише этого пика в записи нет щелчка — есть тишина и шум. */
const MIN_PEAK = 0.02;

export interface CalibrationResult {
  offsetSec: number;
  /** Насколько щелчок громче фона. Меньше трёх — верить нечему. */
  confidence: number;
}

/**
 * Ищет начало щелчка: первый сэмпл, перешагнувший долю от пика.
 *
 * Именно начало, а не пик: у щелчка, прошедшего через комнату, пик может
 * прийтись на отражение, и тогда смещение окажется завышенным.
 */
export function findOnset(recorded: Samples): number {
  let peak = 0;
  for (let i = 0; i < recorded.length; i += 1) {
    const value = Math.abs(recorded[i]);
    if (value > peak) peak = value;
  }
  if (peak < MIN_PEAK) return -1;

  const threshold = peak * ONSET_FRACTION;
  for (let i = 0; i < recorded.length; i += 1) {
    if (Math.abs(recorded[i]) >= threshold) return i;
  }
  return -1;
}

/** Средняя громкость до щелчка — фон, с которым его сравнивают. */
function noiseBefore(recorded: Samples, onset: number): number {
  if (onset <= 0) return 0;
  let sum = 0;
  for (let i = 0; i < onset; i += 1) sum += Math.abs(recorded[i]);
  return sum / onset;
}

/**
 * Смещение по записи щелчка, изданного в момент `emittedAtSample`.
 *
 * `null` — щелчка не слышно: динамики выключены, играет в наушники, или
 * микрофон занят. Врать числом в таком случае хуже, чем признаться.
 */
export function measureOffset(
  recorded: Samples,
  emittedAtSample: number,
  sampleRate: number,
): CalibrationResult | null {
  const onset = findOnset(recorded);
  if (onset < 0 || onset <= emittedAtSample) return null;

  let peak = 0;
  for (let i = onset; i < recorded.length; i += 1) {
    peak = Math.max(peak, Math.abs(recorded[i]));
  }
  const noise = noiseBefore(recorded, onset);
  const confidence = noise > 0 ? peak / noise : Infinity;

  return {
    offsetSec: (onset - emittedAtSample) / sampleRate,
    confidence,
  };
}
