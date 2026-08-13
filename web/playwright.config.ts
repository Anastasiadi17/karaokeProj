import { defineConfig } from "@playwright/test";

/**
 * По умолчанию сценарии идут против dev-сервера Vite, который поднимается
 * здесь же. Если задан `KARAOKE_E2E_BASE_URL`, сценарии идут против уже
 * поднятого адреса и своего сервера не запускают — так проверяется собранный
 * фронт, который раздаёт сам API (`KARAOKE_WEB_DIST`).
 */
const externalBaseUrl = process.env.KARAOKE_E2E_BASE_URL;
const baseURL = externalBaseUrl ?? "http://127.0.0.1:5173";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  use: {
    baseURL,
    launchOptions: {
      args: [
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        "--use-file-for-fake-audio-capture=e2e/fixtures/sample.wav",
        "--autoplay-policy=no-user-gesture-required",
      ],
    },
  },
  ...(externalBaseUrl
    ? {}
    : {
        webServer: {
          command: "npm run dev",
          url: baseURL,
          reuseExistingServer: true,
        },
      }),
});
