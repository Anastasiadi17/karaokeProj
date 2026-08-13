import { useEffect } from "react";
import type { ApiClient } from "../../api/client";
import { useJobPolling } from "./useJobPolling";

const STAGE_LABELS: Record<string, string> = {
  loading: "Загружаю модель",
  separating: "Отделяю вокал",
  writing: "Сохраняю дорожки",
};

export function ProcessingScreen({
  client,
  jobId,
  onReady,
}: {
  client: ApiClient;
  jobId: string;
  onReady: () => void;
}) {
  const { state, error } = useJobPolling(client, jobId);

  useEffect(() => {
    if (state?.status === "done") onReady();
  }, [state?.status, onReady]);

  if (error) return <p role="alert">Связь с сервером потеряна: {error}</p>;
  if (state?.status === "failed") {
    return <p role="alert">Обработка не удалась: {state.error}</p>;
  }

  const label = state?.stage ? STAGE_LABELS[state.stage] : "В очереди";

  return (
    <section>
      <h1>Готовлю минусовку</h1>
      <p>{label}</p>
      <progress value={state?.progress ?? 0} max={1} />
    </section>
  );
}
