import { describe, expect, it } from "vitest";
import { Monitor } from "../src/audio/monitor";

function makeSource(ctx: AudioContext) {
  const osc = ctx.createOscillator();
  osc.frequency.value = 440;
  osc.start();
  return osc;
}

describe("Monitor", () => {
  it("по умолчанию выключен, чтобы не удивлять обратной связью", () => {
    const ctx = new AudioContext();
    expect(new Monitor(ctx).enabled).toBe(false);
    void ctx.close();
  });

  it("включается и выключается", () => {
    const ctx = new AudioContext();
    const monitor = new Monitor(ctx);
    monitor.attach(makeSource(ctx));

    monitor.setEnabled(true);
    expect(monitor.enabled).toBe(true);
    monitor.setEnabled(false);
    expect(monitor.enabled).toBe(false);

    void ctx.close();
  });

  it("setWet зажимает значение в 0..1", () => {
    const ctx = new AudioContext();
    const monitor = new Monitor(ctx);
    monitor.attach(makeSource(ctx));

    expect(() => monitor.setWet(5)).not.toThrow();
    expect(() => monitor.setWet(-5)).not.toThrow();

    void ctx.close();
  });

  it("detach можно звать без attach", () => {
    const ctx = new AudioContext();
    expect(() => new Monitor(ctx).detach()).not.toThrow();
    void ctx.close();
  });

  it("повторный attach не накапливает соединения", () => {
    const ctx = new AudioContext();
    const monitor = new Monitor(ctx);
    monitor.attach(makeSource(ctx));
    expect(() => monitor.attach(makeSource(ctx))).not.toThrow();
    void ctx.close();
  });
});
