import { expect, test } from "@playwright/test";

test("путь от загрузки до скачанного микса", async ({ page }) => {
  await page.goto("/");

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
  await page.goto("/");

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
  await page.goto("/");

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

test("понятная ошибка на неподходящем файле", async ({ page }) => {
  await page.goto("/");

  await page.setInputFiles('input[type="file"]', {
    name: "junk.mp3",
    mimeType: "audio/mpeg",
    buffer: Buffer.from("это точно не аудио"),
  });

  await expect(page.getByRole("alert")).toContainText(
    "Формат не поддерживается",
  );
});
