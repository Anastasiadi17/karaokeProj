import react from "@vitejs/plugin-react";
import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    // Явный IPv4: на Windows «localhost» разрешается сначала в ::1, и
    // ожидание сервера по адресу 127.0.0.1 не дожидается ничего.
    host: "127.0.0.1",
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    projects: [
      {
        test: {
          name: "node",
          environment: "node",
          include: ["tests/**/*.test.ts"],
          exclude: ["tests/**/*.browser.test.ts", "tests/**/*.browser.test.tsx"],
        },
      },
      {
        test: {
          name: "browser",
          include: ["tests/**/*.browser.test.ts", "tests/**/*.browser.test.tsx"],
          browser: {
            enabled: true,
            provider: playwright({
              launchOptions: {
                args: [
                  // Без этого AudioContext в headless остаётся suspended:
                  // жеста пользователя в тестах нет, process() не вызывается.
                  "--autoplay-policy=no-user-gesture-required",
                  // Микрофон в тестах — синтетический, без запроса разрешения.
                  "--use-fake-device-for-media-stream",
                  "--use-fake-ui-for-media-stream",
                ],
              },
            }),
            headless: true,
            instances: [{ browser: "chromium" }],
            // Windows: «localhost» разрешается сначала в ::1, а сервер слушает
            // IPv4 — без явного хоста браузер стучится не туда.
            api: { host: "127.0.0.1" },
          },
        },
      },
    ],
  },
});
