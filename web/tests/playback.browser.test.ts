import { describe, expect, it, vi } from "vitest";
import { playBuffer } from "../src/audio/playback";

function shortBuffer(ctx: AudioContext, seconds: number): AudioBuffer {
  const buffer = ctx.createBuffer(1, Math.round(ctx.sampleRate * seconds), ctx.sampleRate);
  buffer.getChannelData(0).fill(0.1);
  return buffer;
}

describe("playBuffer", () => {
  it("зовёт обработчик конца, когда буфер доиграл", async () => {
    const ctx = new AudioContext();
    const onEnded = vi.fn();

    playBuffer(ctx, shortBuffer(ctx, 0.1), onEnded);

    await vi.waitFor(() => expect(onEnded).toHaveBeenCalledTimes(1), {
      timeout: 3000,
    });
    await ctx.close();
  });

  it("остановка руками тоже считается концом, и ровно одним", async () => {
    // `source.stop()` сам вызывает `onended`, поэтому наивная реализация
    // сообщает о конце дважды — и интерфейс успевает моргнуть кнопкой.
    const ctx = new AudioContext();
    const onEnded = vi.fn();

    const playback = playBuffer(ctx, shortBuffer(ctx, 5), onEnded);
    playback.stop();

    expect(onEnded).toHaveBeenCalledTimes(1);

    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(onEnded).toHaveBeenCalledTimes(1);
    await ctx.close();
  });

  it("повторная остановка ничего не делает", async () => {
    const ctx = new AudioContext();
    const onEnded = vi.fn();

    const playback = playBuffer(ctx, shortBuffer(ctx, 5), onEnded);
    playback.stop();
    playback.stop();

    expect(onEnded).toHaveBeenCalledTimes(1);
    await ctx.close();
  });

  it("остановка после конца не зовёт обработчик второй раз", async () => {
    const ctx = new AudioContext();
    const onEnded = vi.fn();

    const playback = playBuffer(ctx, shortBuffer(ctx, 0.05), onEnded);
    await vi.waitFor(() => expect(onEnded).toHaveBeenCalledTimes(1), {
      timeout: 3000,
    });

    playback.stop();

    expect(onEnded).toHaveBeenCalledTimes(1);
    await ctx.close();
  });
});
