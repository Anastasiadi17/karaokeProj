import { useState } from "react";
import { ApiError } from "../../api/client";
import type { ApiClient } from "../../api/client";
import type { Me, UploadResult } from "../../api/types";

const MESSAGES: Record<string, string> = {
  unsupported_format:
    "Формат не поддерживается. Нужен mp3, wav или flac — " +
    "m4a конвертируйте в один из них.",
  too_long: "Трек длиннее 10 минут.",
  too_large: "Файл больше 200 МБ.",
  quota_exceeded:
    "На бесплатном тарифе три трека в месяц, и они закончились. " +
    "Счётчик обнулится первого числа.",
  unauthorized: "Сессия закончилась. Войдите ещё раз.",
};

export function UploadScreen({
  client,
  me,
  onUploaded,
  onLogout,
}: {
  client: ApiClient;
  me: Me;
  onUploaded: (result: UploadResult) => void;
  onLogout: () => void;
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
      <p>
        {me.email} · осталось {Math.max(0, me.operations_limit - me.operations_used)}{" "}
        из {me.operations_limit} треков в этом месяце{" "}
        <button onClick={onLogout}>Выйти</button>
      </p>
      {me.plan === "free" && (
        <p>
          <button
            onClick={async () => {
              try {
                // Уходим на страницу Stripe целиком: собирать форму оплаты
                // у себя значит попасть в периметр PCI без всякой нужды.
                window.location.href = await client.startCheckout();
              } catch {
                setError(
                  "Оплата сейчас недоступна. Попробуйте позже — на " +
                    "бесплатном тарифе всё продолжает работать.",
                );
              }
            }}
          >
            Перейти на Pro — без водяного знака
          </button>
        </p>
      )}
      <input
        type="file"
        accept="audio/*"
        disabled={busy}
        onChange={(event) => {
          const file = event.target.files?.[0];
          // Значение сбрасывается сразу: иначе повторный выбор того же файла
          // не даёт события, и совет «попробуйте ещё раз» после сорвавшейся
          // отправки невыполним — остаётся только перезагрузить страницу.
          event.target.value = "";
          if (file) void handleFile(file);
        }}
      />
      {busy && <p>Загружаю…</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
