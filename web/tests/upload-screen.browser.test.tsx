import { page } from "@vitest/browser/context";
import { describe, expect, it, vi } from "vitest";
import { render } from "vitest-browser-react";
import { ApiError } from "../src/api/client";
import type { ApiClient } from "../src/api/client";
import { UploadScreen } from "../src/features/upload/UploadScreen";
import type { Me } from "../src/api/types";

const ME: Me = {
  email: "ivan@example.com",
  plan: "free",
  operations_used: 1,
  operations_limit: 3,
};

/** Клиент, у которого экран трогает только загрузку. */
function fakeClient(
  uploadTrack: ApiClient["uploadTrack"],
): ApiClient {
  return { uploadTrack } as unknown as ApiClient;
}

function wavFile(name = "song.wav") {
  return new File([new Uint8Array([1, 2, 3])], name, { type: "audio/wav" });
}

describe("UploadScreen", () => {
  it("отдаёт идентификаторы наверх после удачной загрузки", async () => {
    const onUploaded = vi.fn();
    const client = fakeClient(
      vi.fn(async () => ({ trackId: "t1", jobId: "j1" })),
    );

    render(<UploadScreen client={client} me={ME} onUploaded={onUploaded} onLogout={vi.fn()} onShowPricing={vi.fn()} />);
    const input = await fileInput();
    await userSelects(input, wavFile());

    await vi.waitFor(() =>
      expect(onUploaded).toHaveBeenCalledWith({ trackId: "t1", jobId: "j1" }),
    );
  });

  it("переводит код ошибки в человеческую фразу", async () => {
    const client = fakeClient(
      vi.fn(async () => {
        throw new ApiError("unsupported_format", 400);
      }),
    );

    render(<UploadScreen client={client} me={ME} onUploaded={vi.fn()} onLogout={vi.fn()} onShowPricing={vi.fn()} />);
    const input = await fileInput();
    await userSelects(input, wavFile("song.m4a"));

    await expect
      .element(page.getByRole("alert"))
      .toHaveTextContent("Формат не поддерживается");
  });

  it("незнакомый код не оставляет человека без объяснения", async () => {
    const client = fakeClient(
      vi.fn(async () => {
        throw new ApiError("teapot", 418);
      }),
    );

    render(<UploadScreen client={client} me={ME} onUploaded={vi.fn()} onLogout={vi.fn()} onShowPricing={vi.fn()} />);
    const input = await fileInput();
    await userSelects(input, wavFile());

    await expect
      .element(page.getByRole("alert"))
      .toHaveTextContent("Не удалось загрузить файл");
  });

  it("исчерпанный лимит объясняется словами, а не кодом", async () => {
    const client = fakeClient(
      vi.fn(async () => {
        throw new ApiError("quota_exceeded", 429);
      }),
    );

    render(
      <UploadScreen
        client={client}
        me={{ ...ME, operations_used: 3 }}
        onUploaded={vi.fn()}
        onLogout={vi.fn()} onShowPricing={vi.fn()}
      />,
    );
    const input = await fileInput();
    await userSelects(input, wavFile());

    await expect
      .element(page.getByRole("alert"))
      .toHaveTextContent("три трека в месяц");
  });

  it("показывает остаток и адрес", async () => {
    render(
      <UploadScreen
        client={fakeClient(vi.fn())}
        me={ME}
        onUploaded={vi.fn()}
        onLogout={vi.fn()} onShowPricing={vi.fn()}
      />,
    );

    await expect.element(page.getByText(/осталось 2 из 3/)).toBeVisible();
    await expect.element(page.getByText(/ivan@example.com/)).toBeVisible();
  });

  it("после сбоя тот же файл можно выбрать снова", async () => {
    // Совет «попробуйте ещё раз» невыполним, если поле хранит прежнее
    // значение: повторный выбор того же файла не даёт события `change`.
    const uploadTrack = vi.fn(async () => {
      throw new ApiError("upload_failed", 500);
    });
    render(
      <UploadScreen
        client={fakeClient(uploadTrack)}
        me={ME}
        onUploaded={vi.fn()}
        onLogout={vi.fn()} onShowPricing={vi.fn()}
      />,
    );
    const input = await fileInput();

    await userSelects(input, wavFile());
    await expect.element(page.getByRole("alert")).toBeVisible();
    expect(input.value).toBe("");

    await userSelects(input, wavFile());

    await vi.waitFor(() => expect(uploadTrack).toHaveBeenCalledTimes(2));
  });
});

/**
 * Ждёт поле выбора файла.
 *
 * React 19 рисует не синхронно: сразу после `render` в документе ещё пусто,
 * и `querySelector` возвращает null.
 */
async function fileInput(): Promise<HTMLInputElement> {
  return await vi.waitFor(() => {
    const element = document.querySelector('input[type="file"]');
    if (!element) throw new Error("поле выбора файла ещё не отрисовано");
    return element as HTMLInputElement;
  });
}

/** Кладёт файл в поле и сообщает об этом React так же, как браузер. */
async function userSelects(input: HTMLInputElement, file: File) {
  const transfer = new DataTransfer();
  transfer.items.add(file);
  input.files = transfer.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
}
