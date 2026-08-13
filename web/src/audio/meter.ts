/**
 * Измерение уровня входа с фиксацией клиппинга.
 *
 * Клиппинг сообщается сразу: после сведения о нём узнавать поздно, дубль
 * уже испорчен.
 */

import type { Samples } from "./samples";

export const CLIP_THRESHOLD = 0.99;
const SILENCE_DB = -120;

export function peakOf(frame: Float32Array): number {
  let peak = 0;
  for (let i = 0; i < frame.length; i += 1) {
    const value = Math.abs(frame[i]);
    if (value > peak) peak = value;
  }
  return peak;
}

export function dbFromPeak(peak: number): number {
  if (peak <= 0) return SILENCE_DB;
  return Math.max(SILENCE_DB, 20 * Math.log10(peak));
}

export class LevelMeter {
  private readonly analyser: AnalyserNode;
  private readonly frame: Samples;
  private readonly clipThreshold: number;
  private attached: AudioNode | null = null;
  private clipped = false;

  constructor(ctx: AudioContext, options: { clipThreshold?: number } = {}) {
    this.clipThreshold = options.clipThreshold ?? CLIP_THRESHOLD;
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 2048;
    this.frame = new Float32Array(this.analyser.fftSize);
  }

  attach(source: AudioNode): void {
    this.detach();
    source.connect(this.analyser);
    this.attached = source;
  }

  detach(): void {
    if (this.attached) {
      this.attached.disconnect(this.analyser);
      this.attached = null;
    }
  }

  resetClip(): void {
    this.clipped = false;
  }

  read(): { peak: number; db: number; clipped: boolean } {
    this.analyser.getFloatTimeDomainData(this.frame);
    const peak = peakOf(this.frame);
    if (peak >= this.clipThreshold) this.clipped = true;
    return { peak, db: dbFromPeak(peak), clipped: this.clipped };
  }
}
