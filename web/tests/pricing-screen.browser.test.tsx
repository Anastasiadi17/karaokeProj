import { page } from "@vitest/browser/context";
import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { ApiError } from "../src/api/client";
import type { ApiClient } from "../src/api/client";
import type { Me } from "../src/api/types";
import { PricingScreen } from "../src/features/pricing/PricingScreen";

const FREE: Me = {
  email: "ivan@example.com",
  plan: "free",
  operations_used: 1,
  operations_limit: 3,
  credits: 0,
};

const PRO: Me = { ...FREE, plan: "pro" };

function client(overrides: Partial<ApiClient>): ApiClient {
  return overrides as unknown as ApiClient;
}

describe("PricingScreen", () => {
  it("бесплатному предлагает Pro", async () => {
    render(
      <PricingScreen client={client({})} me={FREE} onBack={vi.fn()} />,
    );

    await expect
      .element(page.getByRole("button", { name: "Перейти на Pro" }))
      .toBeVisible();
  });

  it("подписчику предлагает управление, а не покупку", async () => {
    render(<PricingScreen client={client({})} me={PRO} onBack={vi.fn()} />);

    await expect
      .element(page.getByRole("button", { name: "Управлять подпиской" }))
      .toBeVisible();
    expect(
      document.body.textContent?.includes("Перейти на Pro"),
    ).toBe(false);
  });

  it("недоступная оплата объясняется, а не молчит", async () => {
    // Без ключей Stripe эндпоинт отвечает 503 — человек не должен видеть
    // застывшую кнопку и гадать, нажалась она или нет.
    const startCheckout = vi.fn(async () => {
      throw new ApiError("billing_not_configured", 503);
    });

    render(
      <PricingScreen
        client={client({ startCheckout })}
        me={FREE}
        onBack={vi.fn()}
      />,
    );
    await page.getByRole("button", { name: "Перейти на Pro" }).click();

    await expect
      .element(page.getByRole("alert"))
      .toHaveTextContent("Оплата сейчас недоступна");
  });

  it("недоступная покупка кредитов тоже объясняется", async () => {
    const buyCredits = vi.fn(async () => {
      throw new ApiError("credits_not_configured", 503);
    });

    render(
      <PricingScreen
        client={client({ buyCredits })}
        me={FREE}
        onBack={vi.fn()}
      />,
    );
    await page.getByRole("button", { name: "Купить пакет" }).click();

    await expect
      .element(page.getByRole("alert"))
      .toHaveTextContent("Покупка кредитов сейчас недоступна");
  });

  it("цена и условия названы прямо", async () => {
    render(<PricingScreen client={client({})} me={FREE} onBack={vi.fn()} />);

    await expect.element(page.getByText(/\$9,99/)).toBeVisible();
    await expect.element(page.getByText(/3 трека в месяц/)).toBeVisible();
  });
});
