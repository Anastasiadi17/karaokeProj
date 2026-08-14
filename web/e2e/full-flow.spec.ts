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

/**
 * Помечает все источники звука живого контекста: играет ли, остановлен ли.
 *
 * Иначе сквозной сценарий глух: кнопки переключаются правильно, а звук
 * продолжает идти, и тест этого не видит. Ставится до первого перехода.
 */
async function watchSources(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    const log: { started: boolean; stopped: boolean; ended: boolean }[] = [];
    (window as unknown as { __sources: typeof log }).__sources = log;

    const create = AudioContext.prototype.createBufferSource;
    AudioContext.prototype.createBufferSource = function patched(
      this: AudioContext,
    ) {
      const node = create.call(this);
      const entry = { started: false, stopped: false, ended: false };
      log.push(entry);
      const start = node.start.bind(node);
      const stop = node.stop.bind(node);
      node.start = (...args: Parameters<AudioBufferSourceNode["start"]>) => {
        entry.started = true;
        return start(...args);
      };
      node.stop = (...args: Parameters<AudioBufferSourceNode["stop"]>) => {
        entry.stopped = true;
        return stop(...args);
      };
      node.addEventListener("ended", () => {
        entry.ended = true;
      });
      return node;
    };

    // Значения громкостей живого контекста: по ним видно, доехал ли ползунок
    // до звука. Без этого «ничего не пересобралось» одинаково верно и для
    // работающего пульта, и для ползунка, не подключённого никуда.
    const gains: GainNode[] = [];
    const createGain = AudioContext.prototype.createGain;
    AudioContext.prototype.createGain = function patchedGain(
      this: AudioContext,
    ) {
      const node = createGain.call(this);
      gains.push(node);
      return node;
    };
    (window as unknown as { __gainValues: () => number[] }).__gainValues = () =>
      gains.map((g) => g.gain.value);
  });
}

async function gainValues(page: import("@playwright/test").Page) {
  return page.evaluate(() =>
    (window as unknown as { __gainValues: () => number[] }).__gainValues(),
  );
}

/**
 * Синус на заданную длину. Общая фикстура длится три секунды, а сценарию про
 * оставшийся звук нужна дорожка длиннее, чем всё ожидание в нём: на короткой
 * любой забытый источник успевает доиграть сам, и проверка проходит впустую.
 */
function makeWav(seconds: number): Buffer {
  const sampleRate = 44100;
  const channels = 2;
  const frames = sampleRate * seconds;
  const dataBytes = frames * channels * 2;
  const buffer = Buffer.alloc(44 + dataBytes);
  buffer.write("RIFF", 0, "ascii");
  buffer.writeUInt32LE(36 + dataBytes, 4);
  buffer.write("WAVE", 8, "ascii");
  buffer.write("fmt ", 12, "ascii");
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(channels, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * channels * 2, 28);
  buffer.writeUInt16LE(channels * 2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36, "ascii");
  buffer.writeUInt32LE(dataBytes, 40);
  for (let i = 0; i < frames; i += 1) {
    const value = Math.round(
      12000 * Math.sin((2 * Math.PI * 440 * i) / sampleRate),
    );
    for (let ch = 0; ch < channels; ch += 1) {
      buffer.writeInt16LE(value, 44 + (i * channels + ch) * 2);
    }
  }
  return buffer;
}

async function playingSources(page: import("@playwright/test").Page) {
  return page.evaluate(() =>
    (
      window as unknown as {
        __sources: { started: boolean; stopped: boolean; ended: boolean }[];
      }
    ).__sources.filter((s) => s.started && !s.stopped && !s.ended),
  );
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

test("микс, заказанный перед записью, не остаётся играть после неё", async ({
  page,
}) => {
  await watchSources(page);
  await signIn(page);

  await page.setInputFiles('input[type="file"]', {
    name: "long.wav",
    mimeType: "audio/wav",
    buffer: makeWav(20),
  });
  await expect(page.getByRole("heading", { name: "Студия" })).toBeVisible({
    timeout: 90_000,
  });

  // Дубль, чтобы было что слушать.
  await page.getByRole("button", { name: "Записать" }).click();
  const stopButton = page.getByRole("button", { name: "Остановить", exact: true });
  await expect(stopButton).toBeVisible();
  await page.waitForTimeout(800);
  await stopButton.click();
  await expect(page.getByRole("button", { name: "Скачать микс" })).toBeEnabled();

  // Оба нажатия в одном такте: так окно между заказом микса и его началом
  // не зависит от того, насколько быстра машина.
  await page.evaluate(() => {
    const byName = (name: string) =>
      [...document.querySelectorAll("button")].find(
        (b) => b.textContent?.trim() === name,
      );
    byName("Прослушать")?.click();
    byName("Записать")?.click();
  });

  await expect(stopButton).toBeVisible();
  await page.waitForTimeout(3000);
  await stopButton.click();
  await page.waitForTimeout(1000);

  // Минусовка длится двадцать секунд, то есть всё, что ещё звучит здесь,
  // звучит вопреки остановке, а не по инерции короткой фикстуры.
  expect(await playingSources(page)).toEqual([]);
});

test("ползунок правит идущий микс, а не пересобирает его", async ({ page }) => {
  await watchSources(page);
  await signIn(page);

  await page.setInputFiles('input[type="file"]', {
    name: "long.wav",
    mimeType: "audio/wav",
    buffer: makeWav(20),
  });
  await expect(page.getByRole("heading", { name: "Студия" })).toBeVisible({
    timeout: 90_000,
  });

  await page.getByRole("button", { name: "Записать" }).click();
  const stopButton = page.getByRole("button", { name: "Остановить", exact: true });
  await expect(stopButton).toBeVisible();
  await page.waitForTimeout(800);
  await stopButton.click();

  await page.getByRole("button", { name: "Прослушать" }).click();
  await expect(
    page.getByRole("button", { name: "Остановить прослушивание" }),
  ).toBeVisible({ timeout: 15_000 });

  const before = await playingSources(page);
  expect(before.length).toBeGreaterThan(0);

  // Точное совпадение: слово «голос» есть и в подписи к монитору.
  await page.getByLabel("Минусовка", { exact: true }).fill("0.2");
  await page.getByLabel("Голос", { exact: true }).fill("1.6");
  await page.waitForTimeout(500);

  // Тот же самый звук, что играл до движения ползунков: ни одного нового
  // источника, ни одного остановленного. Пересборка была бы слышна разрывом.
  expect(await playingSources(page)).toEqual(before);

  // И при этом ползунки доехали до узлов, а не остались на экране.
  const gains = await gainValues(page);
  expect(gains.some((value) => Math.abs(value - 0.2) < 0.01)).toBe(true);
  expect(gains.some((value) => Math.abs(value - 1.6) < 0.01)).toBe(true);
  await expect(
    page.getByRole("button", { name: "Остановить прослушивание" }),
  ).toBeVisible();
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
