import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { pollUntilSettled } from "../src/features/processing/useJobPolling";
import type { JobState } from "../src/api/types";

function state(partial: Partial<JobState>): JobState {
  return {
    status: "queued",
    stage: null,
    progress: 0,
    error: null,
    result: null,
    ...partial,
  };
}

describe("pollUntilSettled", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("останавливается на done и отдаёт последнее состояние", async () => {
    const responses = [
      state({ status: "queued" }),
      state({ status: "running", stage: "separating", progress: 0.5 }),
      state({ status: "done", progress: 1 }),
    ];
    const getJob = vi.fn(async () => responses.shift()!);
    const seen: JobState[] = [];

    const promise = pollUntilSettled(getJob, 10, (s) => seen.push(s));
    await vi.runAllTimersAsync();
    const final = await promise;

    expect(final.status).toBe("done");
    expect(seen).toHaveLength(3);
    expect(getJob).toHaveBeenCalledTimes(3);
  });

  it("останавливается на failed", async () => {
    const getJob = vi.fn(async () =>
      state({ status: "failed", error: "CUDA out of memory" }),
    );

    const promise = pollUntilSettled(getJob, 10, () => {});
    await vi.runAllTimersAsync();
    const final = await promise;

    expect(final.status).toBe("failed");
    expect(final.error).toBe("CUDA out of memory");
    expect(getJob).toHaveBeenCalledTimes(1);
  });

  it("сдаётся после нескольких подряд ошибок сети", async () => {
    const getJob = vi.fn(async () => {
      throw new Error("сеть недоступна");
    });

    const promise = pollUntilSettled(getJob, 10, () => {}, 3);
    await vi.runAllTimersAsync();

    await expect(promise).rejects.toThrow("сеть недоступна");
    expect(getJob).toHaveBeenCalledTimes(3);
  });

  it("переживает одиночный сбой посреди обработки", async () => {
    // Разделение идёт десятки секунд: один не дошедший ответ — не повод
    // объявлять человеку, что связь потеряна, когда задача считается дальше.
    const script: (JobState | Error)[] = [
      state({ status: "running", progress: 0.3 }),
      new Error("сеть моргнула"),
      state({ status: "done", progress: 1 }),
    ];
    const getJob = vi.fn(async () => {
      const next = script.shift()!;
      if (next instanceof Error) throw next;
      return next;
    });
    const seen: JobState[] = [];

    const promise = pollUntilSettled(getJob, 10, (s) => seen.push(s));
    await vi.runAllTimersAsync();
    const final = await promise;

    expect(final.status).toBe("done");
    expect(seen).toHaveLength(2);
  });
});
