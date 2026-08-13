import { useState } from "react";
import { ApiError } from "../../api/client";
import type { ApiClient } from "../../api/client";
import type { UploadResult } from "../../api/types";

const MESSAGES: Record<string, string> = {
  unsupported_format:
    "Формат не поддерживается. Нужен mp3, wav или flac — " +
    "m4a конвертируйте в один из них.",
  too_long: "Трек длиннее 10 минут.",
  too_large: "Файл больше 200 МБ.",
};

export function UploadScreen({
  client,
  onUploaded,
}: {
  client: ApiClient;
  onUploaded: (result: UploadResult) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file: File) {
    setBusy(true);
    setError(null);
    try {
      onUploaded(await client.uploadTrack(file));
    } catch (exc) {
      const code = exc instanceof ApiError ? exc.code : "unknown_error";
      setError(
        MESSAGES[code] ?? "Не удалось загрузить файл. Попробуйте ещё раз.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>Загрузите трек</h1>
      <input
        type="file"
        accept="audio/*"
        disabled={busy}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void handleFile(file);
        }}
      />
      {busy && <p>Загружаю…</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
