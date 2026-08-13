import { page } from "@vitest/browser/context";
import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import type { ApiClient } from "../src/api/client";
import type { JobState } from "../src/api/types";
import { ProcessingScreen } from "../src/features/processing/ProcessingScreen";

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

/** Клиент, отвечающий заданным сценарием; последний ответ повторяется. */
function scriptedClient(script: (JobState | Error)[]): ApiClient {
  let index = 0;
  return {
    getJob: vi.fn(async () => {
      const next = script[Math.min(index, script.length - 1)];
      index += 1;
      if (next instanceof Error) throw next;
      return next;
    }),
  } as unknown as ApiClient;
}

describe("ProcessingScreen", () => {
  it("называет стадию человеческими словами", async () => {
    const client = scriptedClient([
      state({ status: "running", stage: "separating", progress: 0.5 }),
    ]);

    render(
      <ProcessingScreen client={client} jobId="j1" onReady={vi.fn()} />,
    );

    await expect
      .element(page.getByText("Отделяю вокал"))
      .toBeVisible();
  });

  it("до первого ответа говорит «В очереди», а не пустоту", async () => {
    const client = scriptedClient([state({ status: "queued" })]);

    render(
      <ProcessingScreen client={client} jobId="j1" onReady={vi.fn()} />,
    );

    await expect.element(page.getByText("В очереди")).toBeVisible();
  });

  it("зовёт onReady, когда задача сделана", async () => {
    const onReady = vi.fn();
    const client = scriptedClient([
      state({ status: "running", stage: "separating", progress: 0.5 }),
      state({
        status: "done",
        progress: 1,
        result: {
          stems: { vocals: "v", no_vocals: "n" },
          degraded: false,
        },
      }),
    ]);

    render(
      <ProcessingScreen client={client} jobId="j1" onReady={onReady} />,
    );

    await vi.waitFor(() => expect(onReady).toHaveBeenCalled(), {
      timeout: 5000,
    });
  });

  it("показывает причину, когда обработка не удалась", async () => {
    const client = scriptedClient([
      state({ status: "failed", error: "CUDA out of memory" }),
    ]);

    render(
      <ProcessingScreen client={client} jobId="j1" onReady={vi.fn()} />,
    );

    await expect
      .element(page.getByRole("alert"))
      .toHaveTextContent("CUDA out of memory");
  });

  it("одиночный сбой сети не выводит экран из работы", async () => {
    // Разделение идёт десятки секунд; один не дошедший ответ не повод
    // объявлять связь потерянной, пока задача считается дальше.
    const client = scriptedClient([
      new Error("сеть моргнула"),
      state({ status: "running", stage: "writing", progress: 0.9 }),
    ]);

    render(
      <ProcessingScreen client={client} jobId="j1" onReady={vi.fn()} />,
    );

    await expect
      .element(page.getByText("Сохраняю дорожки"))
      .toBeVisible({ timeout: 5000 });
  });
});
