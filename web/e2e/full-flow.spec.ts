import { createHmac } from "node:crypto";
import { expect, test } from "@playwright/test";

/**
 * Вход обязателен, поэтому каждый сценарий начинается с него.
 *
 * Письмо в сквозном тесте не прочитать, поэтому API поднимается с
 * `KARAOKE_EXPOSE_LOGIN_LINK=1` и отдаёт ссылку прямо в ответе. Флаг
 * существует только для разработки и при старте пишет об этом в лог.
 */
async function signIn(
  page: import("@playwright/test").Page,
): Promise<string> {
  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2)}@example.com`;
  const response = await page.request.post("/api/auth/request", {
    data: { email },
  });
  const { link } = (await response.json()) as { link: string };
  const url = new URL(link);
  await page.goto(`${url.pathname}${url.search}`);
  await expect(page.getByRole("heading", { name: "Загрузите трек" })).toBeVisible();
  return email;
}

/**
 * Начисляет кредиты так же, как это делает Stripe: событием с настоящей
 * подписью. Купить пакет в тесте нельзя — ключей нет, — а проверять списание
 * на подложенном в базу балансе значило бы проверять не тот путь.
 */
async function grantCredits(
  page: import("@playwright/test").Page,
  email: string,
  amount: number,
) {
  const secret = process.env.KARAOKE_STRIPE_WEBHOOK_SECRET;
  test.skip(!secret, "нужен KARAOKE_STRIPE_WEBHOOK_SECRET у поднятого API");

  const event = {
    id: `evt_e2e_${Date.now()}_${Math.random().toString(36).slice(2)}`,
    type: "checkout.session.completed",
    data: {
      object: {
        mode: "payment",
        customer_details: { email },
        metadata: { credits: String(amount) },
      },
    },
  };
  const payload = JSON.stringify(event);
  const timestamp = Math.floor(Date.now() / 1000);
  const signature = createHmac("sha256", secret!)
    .update(`${timestamp}.${payload}`)
    .digest("hex");

  const response = await page.request.post("/api/billing/webhook", {
    data: payload,
    headers: {
      "content-type": "application/json",
      "stripe-signature": `t=${timestamp},v1=${signature}`,
    },
  });
  expect(response.status()).toBe(200);
}

test("путь от загрузки до скачанного микса", async ({ page }) => {
  await signIn(page);

  await page.setInputFiles('input[type="file"]', "e2e/fixtures/sample.wav");

  await expect(page.getByRole("heading", { name: "Студия" })).toBeVisible({
    timeout: 90_000,
  });

  await page.getByRole("button", { name: "Записать" }).click();
  await page.waitForTimeout(2000);
  await page.getByRole("button", { name: "Остановить" }).click();

  const exportButton = page.getByRole("button", { name: "Скачать микс" });
  await expect(exportButton).toBeEnabled();

  const downloadPromise = page.waitForEvent("download");
  await exportButton.click();
  const download = await downloadPromise;

  expect(download.suggestedFilename()).toBe("karaoke-mix.wav");
});

test("дубль не теряется, когда минусовка доиграла сама", async ({ page }) => {
  await signIn(page);

  await page.setInputFiles('input[type="file"]', "e2e/fixtures/sample.wav");
  await expect(page.getByRole("heading", { name: "Студия" })).toBeVisible({
    timeout: 90_000,
  });

  await page.getByRole("button", { name: "Записать" }).click();

  // Минусовка длится три секунды. Ждём, пока она кончится сама, и НЕ жмём
  // «Остановить»: так поёт человек, спевший песню до конца. Сначала надо
  // дождаться самой кнопки «Остановить» — иначе первая же проверка увидит
  // экран до перерисовки и пройдёт, ничего не подождав.
  const stopButton = page.getByRole("button", { name: "Остановить" });
  await expect(stopButton).toBeVisible();
  await expect(stopButton).toBeHidden({ timeout: 15_000 });

  await expect(page.getByRole("button", { name: "Скачать микс" })).toBeEnabled();
});

test("микс можно послушать до скачивания", async ({ page }) => {
  await signIn(page);

  await page.setInputFiles('input[type="file"]', "e2e/fixtures/sample.wav");
  await expect(page.getByRole("heading", { name: "Студия" })).toBeVisible({
    timeout: 90_000,
  });

  await page.getByRole("button", { name: "Записать" }).click();
  const stopButton = page.getByRole("button", { name: "Остановить", exact: true });
  await expect(stopButton).toBeVisible();
  await stopButton.click();

  await page.getByRole("button", { name: "Прослушать" }).click();

  // Кнопка переключилась — значит сведение собралось и играет.
  const stopPreview = page.getByRole("button", {
    name: "Остановить прослушивание",
  });
  await expect(stopPreview).toBeVisible({ timeout: 15_000 });

  await stopPreview.click();
  await expect(page.getByRole("button", { name: "Прослушать" })).toBeVisible();

  // Прослушивание не должно мешать экспорту: дубль остаётся на месте.
  await expect(page.getByRole("button", { name: "Скачать микс" })).toBeEnabled();
});

test("трек можно убрать с сервера, не теряя студию", async ({ page }) => {
  await signIn(page);

  await page.setInputFiles('input[type="file"]', "e2e/fixtures/sample.wav");
  await expect(page.getByRole("heading", { name: "Студия" })).toBeVisible({
    timeout: 90_000,
  });

  await page.getByRole("button", { name: "Удалить трек с сервера" }).click();

  await expect(page.getByText("Трек удалён с сервера")).toBeVisible();
  // Студия на месте: минусовка уже в памяти вкладки.
  await expect(page.getByRole("button", { name: "Записать" })).toBeEnabled();
});

test("понятная ошибка на неподходящем файле", async ({ page }) => {
  await signIn(page);

  await page.setInputFiles('input[type="file"]', {
    name: "junk.mp3",
    mimeType: "audio/mpeg",
    buffer: Buffer.from("это точно не аудио"),
  });

  await expect(page.getByRole("alert")).toContainText(
    "Формат не поддерживается",
  );
});

test("кредит списывается за улучшение звучания", async ({ page }) => {
  const email = await signIn(page);
  await grantCredits(page, email, 2);

  const before = await (await page.request.get("/api/me")).json();
  expect(before.credits).toBe(2);

  await page.setInputFiles('input[type="file"]', "e2e/fixtures/sample.wav");
  await expect(page.getByRole("heading", { name: "Студия" })).toBeVisible({
    timeout: 90_000,
  });

  await page.getByRole("button", { name: "Улучшить звучание (1 кредит)" }).click();

  await expect(page.getByText("Улучшение звучания включено")).toBeVisible();

  const after = await (await page.request.get("/api/me")).json();
  expect(after.credits).toBe(1);
});

test("без кредитов улучшение объясняет отказ", async ({ page }) => {
  await signIn(page);

  await page.setInputFiles('input[type="file"]', "e2e/fixtures/sample.wav");
  await expect(page.getByRole("heading", { name: "Студия" })).toBeVisible({
    timeout: 90_000,
  });

  await page.getByRole("button", { name: "Улучшить звучание (1 кредит)" }).click();

  await expect(page.getByRole("alert")).toContainText("Не хватает кредитов");
  // Кнопка остаётся на месте: отказ не должен выглядеть как поломка.
  await expect(
    page.getByRole("button", { name: "Улучшить звучание (1 кредит)" }),
  ).toBeVisible();
});
