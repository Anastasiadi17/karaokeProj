import { useEffect, useState } from "react";
import type { ApiClient } from "../../api/client";
import type { JobState } from "../../api/types";

const SETTLED = new Set(["done", "failed"]);

/** Опрашивает задачу до завершения. Вынесено из хука ради тестируемости. */
export async function pollUntilSettled(
  getJob: () => Promise<JobState>,
  intervalMs: number,
  onUpdate: (state: JobState) => void,
): Promise<JobState> {
  for (;;) {
    const state = await getJob();
    onUpdate(state);
    if (SETTLED.has(state.status)) return state;
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
