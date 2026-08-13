/**
 * Звуковой водяной знак для бесплатного экспорта.
 *
 * Короткий затухающий тон в начале и далее через интервал. Синтезируется
 * кодом, поэтому не требует ассетов и не создаёт лицензионных вопросов.
 */

import type { Samples } from "./samples";

export const WATERMARK_INTERVAL_SEC = 30;
/** −18 dB */
export const WATERMARK_GAIN = 0.126;

const MARK_DURATION_SEC = 0.25;
const MARK_FREQ_HZ = 880;

export function generateWatermark(
  sampleRate: number,
  totalSamples: number,
  options: { intervalSec?: number; gain?: number } = {},
): Samples {
  const { intervalSec = WATERMARK_INTERVAL_SEC, gain = WATERMARK_GAIN } =
    options;

  const track = new Float32Array(totalSamples);
  const markLength = Math.floor(sampleRate * MARK_DURATION_SEC);
  const stride = Math.floor(sampleRate * intervalSec);

  for (let start = 0; start < totalSamples; start += stride) {
    const count = Math.min(markLength, totalSamples - start);
    for (let i = 0; i < count; i += 1) {
      const envelope = 1 - i / markLength;
      track[start + i] =
        Math.sin((2 * Math.PI * MARK_FREQ_HZ * i) / sampleRate) *
        envelope *
        gain;
    }
  }

  return track;
}
