import { useState } from "react";
import type { ApiClient } from "../../api/client";
import type { Me } from "../../api/types";

/**
 * Страница тарифа.
 *
 * Пишем только то, что уже работает: три трека в месяц против безлимита и
 * водяной знак против его отсутствия. AI-функций и кредитов здесь нет —
 * обещать в прайсе то, чего в продукте не существует, значит собирать деньги
 * за намерение.
 */
export function PricingScreen({
  client,
  me,
  onBack,
}: {
  client: ApiClient;
  me: Me;
  onBack: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go(action: "checkout" | "portal") {
    setBusy(true);
    setError(null);
    try {
      window.location.href =
        action === "checkout"
          ? await client.startCheckout()
          : await client.openPortal();
    } catch {
      setBusy(false);
      setError(
        action === "checkout"
          ? "Оплата сейчас недоступна. На бесплатном тарифе всё продолжает работать."
          : "Управление подпиской сейчас недоступно.",
      );
    }
  }

  return (
    <section>
      <h1>Тарифы</h1>

      <article>
        <h2>Бесплатно</h2>
        <ul>
          <li>3 трека в месяц</li>
          <li>Трек целиком, до 10 минут</li>
          <li>Короткий сигнал в экспорте каждые полминуты</li>
        </ul>
        {me.plan === "free" && <p>Ваш текущий тариф.</p>}
      </article>

      <article>
        <h2>Pro — $9,99 в месяц</h2>
        <ul>
          <li>Без ограничения на число треков</li>
          <li>Экспорт без сигнала</li>
          <li>Отмена в любой момент, доступ до конца оплаченного месяца</li>
        </ul>
        {me.plan === "pro" ? (
          <>
            <p>Ваш текущий тариф.</p>
            <button disabled={busy} onClick={() => void go("portal")}>
              Управлять подпиской
            </button>
          </>
        ) : (
          <button disabled={busy} onClick={() => void go("checkout")}>
            {busy ? "Открываю оплату…" : "Перейти на Pro"}
          </button>
        )}
      </article>

      <article>
        <h2>Кредиты</h2>
        <p>
          Сейчас на счету {me.credits}. Кредиты понадобятся для AI-функций —
          их пока нет, и купить пакет нельзя: продавать то, что не работает,
          мы не станем.
        </p>
      </article>

      {error && <p role="alert">{error}</p>}

      <p>
        Оплату принимает Stripe: карта вводится на его странице и до нас не
        доходит.
      </p>

      <button onClick={onBack}>Назад</button>
    </section>
  );
}
