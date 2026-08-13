import { createReverb } from "./reverb";

/**
 * Прослушивание собственного голоса при записи.
 *
 * Выключен по умолчанию: круг через Web Audio добавляет 20–40 мс при
 * слышимом пороге около 15–20, и часть людей это раздражает сильнее, чем
 * пение всухую. Решение оставлено пользователю.
 */
export class Monitor {
  private readonly ctx: AudioContext;
  private readonly input: GainNode;
  private readonly dry: GainNode;
  private readonly wet: GainNode;
  private readonly output: GainNode;
  private attached: AudioNode | null = null;
  private on = false;

  constructor(ctx: AudioContext) {
    this.ctx = ctx;
    this.input = ctx.createGain();
    this.dry = ctx.createGain();
    this.wet = ctx.createGain();
    this.output = ctx.createGain();

    this.dry.gain.value = 0.7;
    this.wet.gain.value = 0.3;
    this.output.gain.value = 0;

    this.input.connect(this.dry).connect(this.output);
    this.input
      .connect(createReverb(ctx))
      .connect(this.wet)
      .connect(this.output);
    this.output.connect(ctx.destination);
  }

  /**
   * Состояние держится отдельным полем, а не читается из `output.gain.value`:
   * включение идёт плавным нарастанием, и сразу после вызова параметр ещё
   * стоит на нуле. Переключатель в интерфейсе не должен полсекунды врать.
   */
  get enabled(): boolean {
    return this.on;
  }

  attach(source: AudioNode): void {
    this.detach();
    source.connect(this.input);
    this.attached = source;
  }

  detach(): void {
    if (this.attached) {
      this.attached.disconnect(this.input);
      this.attached = null;
    }
  }

  setEnabled(on: boolean): void {
    this.on = on;
    this.output.gain.setTargetAtTime(on ? 1 : 0, this.ctx.currentTime, 0.01);
  }

  setWet(value: number): void {
    const wet = Math.max(0, Math.min(1, value));
    this.wet.gain.value = wet;
    this.dry.gain.value = 1 - wet;
  }
}
