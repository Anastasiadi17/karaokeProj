/**
 * Компенсация задержки записи.
 *
 * Браузер отдаёт звук в наушники и забирает с микрофона не мгновенно, поэтому
 * записанный голос оказывается позже музыки. Смещение вычитается при сведении
 * сдвигом буфера — так его можно поменять уже после записи, не переписывая дубль.
 */

import type { Samples } from "./samples";

const STORAGE_KEY = "karaoke.latencyOffsetSec";

export const MIN_OFFSET_SEC = -0.2;
export const MAX_OFFSET_SEC = 0.2;

export function estimateLatencySec(ctx: {
  baseLatency?: number;
  outputLatency?: number;
}): number {
  return (ctx.baseLatency ?? 0) + (ctx.outputLatency ?? 0);
}

export function secToSamples(sec: number, sampleRate: number): number {
  return Math.round(sec * sampleRate);
}

export function shiftSamples(
  channel: Samples,
  offsetSamples: number,
): Samples {
  if (offsetSamples === 0) return channel;

  const out = new Float32Array(channel.length);
  if (offsetSamples > 0) {
    const count = Math.max(0, channel.length - offsetSamples);
    out.set(channel.subarray(offsetSamples, offsetSamples + count), 0);
  } else {
    const shift = -offsetSamples;
    const count = Math.max(0, channel.length - shift);
    out.set(channel.subarray(0, count), shift);
  }
  return out;
}

export function clampOffset(sec: number): number {
  return Math.max(MIN_OFFSET_SEC, Math.min(MAX_OFFSET_SEC, sec));
}

/**
 * Сохранённое смещение или `null`, если его не сохраняли.
 *
 * Отличать «не сохраняли» от сохранённого нуля обязательно: ноль — законная
 * настройка (звуковая карта без заметной задержки), и подменять его оценкой
 * значит молча ломать то, что человек выставил руками.
 */
export function loadOffset(storage: Storage): number | null {
  const raw = storage.getItem(STORAGE_KEY);
  if (raw === null) return null;
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? clampOffset(parsed) : null;
}

export function saveOffset(storage: Storage, sec: number): void {
  storage.setItem(STORAGE_KEY, String(clampOffset(sec)));
}
