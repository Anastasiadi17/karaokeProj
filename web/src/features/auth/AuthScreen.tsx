import { useState } from "react";
import { ApiError } from "../../api/client";
import type { ApiClient } from "../../api/client";

const MESSAGES: Record<string, string> = {
  invalid_email: "Проверьте адрес: похоже, в нём опечатка.",
};

export function AuthScreen({ client }: { client: ApiClient }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      await client.requestLogin(email);
      setSent(true);
    } catch (exc) {
      const code = exc instanceof ApiError ? exc.code : "unknown_error";
      setError(MESSAGES[code] ?? "Не удалось отправить письмо. Попробуйте ещё раз.");
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <section>
        <h1>Проверьте почту</h1>
        {/* Про «если такой аккаунт есть» здесь не пишем: ответ сервера
            одинаков для любого адреса, и текст не должен намекать на
            обратное. */}
        <p>
          Отправили ссылку для входа на {email}. Она действует 15 минут и
          срабатывает один раз.
        </p>
        <button onClick={() => setSent(false)}>Другой адрес</button>
      </section>
    );
  }

  return (
    <section>
      <h1>Вход</h1>
      <p>Пароля нет: мы пришлём ссылку на почту.</p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <label>
          Почта
          <input
            type="email"
            required
            value={email}
            disabled={busy}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <button type="submit" disabled={busy || email.length === 0}>
          {busy ? "Отправляю…" : "Прислать ссылку"}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
