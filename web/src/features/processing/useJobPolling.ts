import { useEffect, useState } from "react";
import type { ApiClient } from "../../api/client";
import type { JobState } from "../../api/types";

const SETTLED = new Set(["done", "failed"]);

/**
 * Сколько подряд неудачных опросов терпеть, прежде чем сдаться.
 *
 * Разделение идёт десятки секунд, а то и минуты. За это время один ответ
 * может не дойти — перезапуск uvicorn, моргнувший Wi-Fi. Сдаваться с первого
 * раза нельзя: задача на сервере при этом считается дальше и досчитается, а
 * человек уже увидел «связь потеряна» и остался без кнопки повтора.
 */
const MAX_CONSECUTIVE_FAILURES = 5;

/** Опрашивает задачу до завершения. Вынесено из хука ради тестируемости. */
export async function pollUntilSettled(
  getJob: () => Promise<JobState>,
  intervalMs: number,
  onUpdate: (state: JobState) => void,
  maxFailures = MAX_CONSECUTIVE_FAILURES,
): Promise<JobState> {
  let failures = 0;

  for (;;) {
    try {
      const state = await getJob();
      failures = 0;
      onUpdate(state);
      if (SETTLED.has(state.status)) return state;
    } catch (exc) {
      failures += 1;
      if (failures >= maxFailures) throw exc;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

export function useJobPolling(
  client: ApiClient,
  jobId: string | null,
  intervalMs = 1000,
): { state: JobState | null; error: string | null } {
  const [state, setState] = useState<JobState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    pollUntilSettled(
      () => client.getJob(jobId),
      intervalMs,
      (next) => {
        if (!cancelled) setState(next);
      },
    ).catch((exc: unknown) => {
      if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
    });

    return () => {
      cancelled = true;
    };
  }, [client, jobId, intervalMs]);

  return { state, error };
}
