import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "../src/api/client";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(response: unknown, status = 200) {
  const fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify(response), {
        status,
        headers: { "content-type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("ApiClient", () => {
  it("возвращает идентификаторы после загрузки", async () => {
    stubFetch({ track_id: "t1", job_id: "j1" }, 201);
    const client = new ApiClient("http://api.test");

    const result = await client.uploadTrack(
      new File([new Uint8Array([1, 2, 3])], "song.wav"),
    );

    expect(result).toEqual({ trackId: "t1", jobId: "j1" });
  });

  it("отправляет файл как multipart на /api/tracks", async () => {
    const fetchMock = stubFetch({ track_id: "t", job_id: "j" }, 201);
    await new ApiClient("http://api.test").uploadTrack(
      new File(["x"], "song.wav"),
    );

    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("http://api.test/api/tracks");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("превращает ошибку сервера в ApiError с кодом", async () => {
    stubFetch({ error: "too_long" }, 400);
    const client = new ApiClient("http://api.test");

    await expect(
      client.uploadTrack(new File(["x"], "big.wav")),
    ).rejects.toMatchObject({ code: "too_long" });
  });

  it("разбирает состояние задачи", async () => {
    stubFetch({
      status: "done",
      stage: null,
      progress: 1,
      error: null,
      result: {
        stems: { vocals: "a", no_vocals: "b" },
        degraded: false,
      },
    });

    const state = await new ApiClient("http://api.test").getJob("j1");

    expect(state.status).toBe("done");
    expect(state.progress).toBe(1);
    expect(state.result?.stems.no_vocals).toBe("b");
  });

  it("строит ссылку на дорожку", () => {
    const client = new ApiClient("http://api.test");
    expect(client.stemUrl("t1", "no_vocals")).toBe(
      "http://api.test/api/tracks/t1/stems/no_vocals",
    );
  });

  it("отдаёт байты дорожки", async () => {
    const bytes = new Uint8Array([1, 2, 3, 4]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(bytes, { status: 200 })),
    );

    const buffer = await new ApiClient("http://api.test").fetchStem(
      "t1",
      "no_vocals",
    );

    expect(new Uint8Array(buffer)).toEqual(bytes);
  });

  it("пропавшая дорожка даёт ApiError, а не мусор в декодер", async () => {
    stubFetch({ error: "not_found" }, 404);

    await expect(
      new ApiClient("http://api.test").fetchStem("t1", "no_vocals"),
    ).rejects.toMatchObject({ code: "not_found", status: 404 });
  });

  it("ApiError сохраняет код для неизвестных ошибок", async () => {
    stubFetch({}, 500);
    await expect(
      new ApiClient("http://api.test").getJob("j1"),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
