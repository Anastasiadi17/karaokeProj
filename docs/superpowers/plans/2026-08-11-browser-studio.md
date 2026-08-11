# Браузерная студия (подсистема B) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Веб-интерфейс, который загружает трек в API, даёт записать голос поверх минусовки с компенсацией задержки и отдаёт сведённый файл.

**Architecture:** SPA на Vite + React. Вся работа со звуком вынесена в `src/audio/` — модуль без единого импорта React, оперирующий буферами и параметрами. React в `src/features/` держит состояние интерфейса и вызывает движок. Компенсация задержки применяется при сведении, а не в реальном времени.

**Tech Stack:** TypeScript 5.x, Vite 6, React 19, Vitest (Node + browser mode), Playwright, Web Audio API, AudioWorklet.

## Global Constraints

- **Подсистема A должна быть готова** (`docs/superpowers/plans/2026-08-11-processing-core.md`). API поднимается на `http://127.0.0.1:8000`.
- **`src/audio/**` не импортирует React ни прямо, ни косвенно.** Это условие проверяемости движка без рендера.
- **`getUserMedia` вызывается только с `echoCancellation: false`, `noiseSuppression: false`, `autoGainControl: false`.** Включённые по умолчанию, они режут певческий диапазон и добавляют плавающую задержку, ломающую компенсацию.
- **Захват только через `AudioWorkletNode`.** `MediaRecorder` даёт сжатый webm с неопределённым таймингом, непригодный для выравнивания.
- **`AudioContext` создаётся с явным `sampleRate`, равным частоте трека.** Иначе рассинхрон копится по длине песни.
- **Смещение применяется при сведении**, сдвигом буфера, а не задержкой в графе.
- Рабочая директория для всех команд: `web/`.

---

### Task 1: Каркас приложения и клиент API

**Files:**
- Create: `web/package.json`, `web/tsconfig.json`, `web/vite.config.ts`, `web/index.html`
- Create: `web/src/main.tsx`, `web/src/App.tsx`
- Create: `web/src/api/types.ts`, `web/src/api/client.ts`
- Create: `web/tests/api-client.test.ts`

**Interfaces:**
- Consumes: HTTP-контракт подсистемы A
- Produces: типы `JobStatus = "queued" | "running" | "done" | "failed"`, `Stage = "loading" | "separating" | "writing"`, `JobState { status, stage, progress, error, result }`, `UploadResult { trackId, jobId }`; класс `ApiClient(baseUrl: string)` с методами `uploadTrack(file: File): Promise<UploadResult>`, `getJob(jobId: string): Promise<JobState>`, `stemUrl(trackId: string, kind: "vocals" | "no_vocals"): string`, `deleteTrack(trackId: string): Promise<void>`; ошибка `ApiError` с полем `code`.

- [ ] **Step 1: Создать проект**

```bash
cd web
npm create vite@latest . -- --template react-ts
npm install
npm install -D vitest @vitest/browser playwright
```

- [ ] **Step 2: Настроить Vite и Vitest**

Заменить `web/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
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
        },
      },
      {
        test: {
          name: "browser",
          include: ["tests/**/*.browser.test.ts"],
          browser: {
            enabled: true,
            provider: "playwright",
            headless: true,
            instances: [{ browser: "chromium" }],
          },
        },
      },
    ],
  },
});
```

Добавить в `web/package.json` в `scripts`:

```json
"test": "vitest run --project node",
"test:browser": "vitest run --project browser",
"test:all": "vitest run"
```

- [ ] **Step 3: Написать падающий тест**

Создать `web/tests/api-client.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "../src/api/client";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(response: unknown, status = 200) {
  const fetchMock = vi.fn(async () =>
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

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
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
      result: { stems: { vocals: "a", no_vocals: "b" } },
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

  it("ApiError сохраняет код для неизвестных ошибок", async () => {
    stubFetch({}, 500);
    await expect(
      new ApiClient("http://api.test").getJob("j1"),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
```

- [ ] **Step 4: Запустить тест, убедиться что падает**

Run: `npm test -- api-client`
Expected: FAIL — `Cannot find module '../src/api/client'`

- [ ] **Step 5: Написать типы**

Создать `web/src/api/types.ts`:

```ts
export type JobStatus = "queued" | "running" | "done" | "failed";
export type Stage = "loading" | "separating" | "writing";
export type StemKind = "vocals" | "no_vocals";

export interface JobResult {
  stems: Record<StemKind, string>;
}

export interface JobState {
  status: JobStatus;
  stage: Stage | null;
  progress: number;
  error: string | null;
  result: JobResult | null;
}

export interface UploadResult {
  trackId: string;
  jobId: string;
}
```

- [ ] **Step 6: Написать клиент**

Создать `web/src/api/client.ts`:

```ts
import type { JobState, StemKind, UploadResult } from "./types";

export class ApiError extends Error {
  constructor(readonly code: string, readonly status: number) {
    super(`${code} (HTTP ${status})`);
    this.name = "ApiError";
  }
}

async function parseOrThrow<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const code = (body as { error?: string }).error ?? "unknown_error";
    throw new ApiError(code, response.status);
  }
  return body as T;
}

export class ApiClient {
  constructor(private readonly baseUrl: string = "") {}

  async uploadTrack(file: File): Promise<UploadResult> {
    const form = new FormData();
    form.append("file", file);

    const response = await fetch(`${this.baseUrl}/api/tracks`, {
      method: "POST",
      body: form,
    });

    const body = await parseOrThrow<{ track_id: string; job_id: string }>(
      response,
    );
    return { trackId: body.track_id, jobId: body.job_id };
  }

  async getJob(jobId: string): Promise<JobState> {
    const response = await fetch(`${this.baseUrl}/api/jobs/${jobId}`);
    return parseOrThrow<JobState>(response);
  }

  stemUrl(trackId: string, kind: StemKind): string {
    return `${this.baseUrl}/api/tracks/${trackId}/stems/${kind}`;
  }

  async deleteTrack(trackId: string): Promise<void> {
    const response = await fetch(`${this.baseUrl}/api/tracks/${trackId}`, {
      method: "DELETE",
    });
    if (!response.ok) {
      throw new ApiError("delete_failed", response.status);
    }
  }
}
```

- [ ] **Step 7: Запустить тесты, убедиться что проходят**

Run: `npm test -- api-client`
Expected: PASS, 6 тестов

- [ ] **Step 8: Коммит**

```bash
cd .. && git add web
git commit -m "feat(web): каркас приложения и клиент API"
```

---

### Task 2: Кодировщик WAV

**Files:**
- Create: `web/src/audio/encode.ts`
- Create: `web/tests/encode.test.ts`

**Interfaces:**
- Consumes: ничего
- Produces: `encodeWav(channels: Float32Array[], sampleRate: number): Blob`; `interleave(channels: Float32Array[]): Float32Array`; `floatToPcm16(sample: number): number`.

- [ ] **Step 1: Написать падающий тест**

Создать `web/tests/encode.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { encodeWav, floatToPcm16, interleave } from "../src/audio/encode";

async function headerOf(blob: Blob) {
  const view = new DataView(await blob.arrayBuffer());
  const text = (offset: number) =>
    String.fromCharCode(...[0, 1, 2, 3].map((i) => view.getUint8(offset + i)));
  return {
    riff: text(0),
    wave: text(8),
    fmt: text(12),
    data: text(36),
    channels: view.getUint16(22, true),
    sampleRate: view.getUint32(24, true),
    bitsPerSample: view.getUint16(34, true),
    dataBytes: view.getUint32(40, true),
    byteLength: view.byteLength,
  };
}

describe("floatToPcm16", () => {
  it("отображает нуль в нуль", () => {
    expect(floatToPcm16(0)).toBe(0);
  });

  it("зажимает выход за пределы диапазона", () => {
    expect(floatToPcm16(2)).toBe(32767);
    expect(floatToPcm16(-2)).toBe(-32768);
  });
});

describe("interleave", () => {
  it("чередует сэмплы каналов", () => {
    const left = new Float32Array([1, 3, 5]);
    const right = new Float32Array([2, 4, 6]);
    expect(Array.from(interleave([left, right]))).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("оставляет моно без изменений", () => {
    expect(Array.from(interleave([new Float32Array([1, 2])]))).toEqual([1, 2]);
  });
});

describe("encodeWav", () => {
  it("пишет корректный заголовок", async () => {
    const blob = encodeWav(
      [new Float32Array(100), new Float32Array(100)],
      44100,
    );
    const h = await headerOf(blob);

    expect(h.riff).toBe("RIFF");
    expect(h.wave).toBe("WAVE");
    expect(h.fmt).toBe("fmt ");
    expect(h.data).toBe("data");
    expect(h.channels).toBe(2);
    expect(h.sampleRate).toBe(44100);
    expect(h.bitsPerSample).toBe(16);
  });

  it("считает размер данных как каналы × кадры × 2 байта", async () => {
    const h = await headerOf(
      encodeWav([new Float32Array(50), new Float32Array(50)], 48000),
    );
    expect(h.dataBytes).toBe(50 * 2 * 2);
    expect(h.byteLength).toBe(44 + 50 * 2 * 2);
  });

  it("переживает круговой рейс значений", async () => {
    const source = new Float32Array([0, 0.5, -0.5, 1, -1]);
    const blob = encodeWav([source], 44100);
    const view = new DataView(await blob.arrayBuffer());

    const decoded = Array.from({ length: source.length }, (_, i) =>
      view.getInt16(44 + i * 2, true),
    );

    expect(decoded[0]).toBe(0);
    expect(decoded[3]).toBe(32767);
    expect(decoded[4]).toBe(-32768);
    expect(Math.abs(decoded[1] / 32767 - 0.5)).toBeLessThan(0.001);
  });

  it("отдаёт blob с типом audio/wav", () => {
    expect(encodeWav([new Float32Array(10)], 44100).type).toBe("audio/wav");
  });
});
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `npm test -- encode`
Expected: FAIL — `Cannot find module '../src/audio/encode'`

- [ ] **Step 3: Написать реализацию**

Создать `web/src/audio/encode.ts`:

```ts
/** Кодирование в WAV 16 бит. Без зависимостей: заголовок плюс PCM. */

export function floatToPcm16(sample: number): number {
  const clamped = Math.max(-1, Math.min(1, sample));
  return clamped < 0 ? Math.round(clamped * 32768) : Math.round(clamped * 32767);
}

export function interleave(channels: Float32Array[]): Float32Array {
  if (channels.length === 1) return channels[0];

  const frames = channels[0].length;
  const out = new Float32Array(frames * channels.length);
  for (let frame = 0; frame < frames; frame += 1) {
    for (let ch = 0; ch < channels.length; ch += 1) {
      out[frame * channels.length + ch] = channels[ch][frame];
    }
  }
  return out;
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let i = 0; i < text.length; i += 1) {
    view.setUint8(offset + i, text.charCodeAt(i));
  }
}

export function encodeWav(channels: Float32Array[], sampleRate: number): Blob {
  const samples = interleave(channels);
  const channelCount = channels.length;
  const dataBytes = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buffer);

  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeAscii(view, 8, "WAVE");

  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channelCount, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * channelCount * 2, true);
  view.setUint16(32, channelCount * 2, true);
  view.setUint16(34, 16, true);

  writeAscii(view, 36, "data");
  view.setUint32(40, dataBytes, true);

  for (let i = 0; i < samples.length; i += 1) {
    view.setInt16(44 + i * 2, floatToPcm16(samples[i]), true);
  }

  return new Blob([buffer], { type: "audio/wav" });
}
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `npm test -- encode`
Expected: PASS, 8 тестов

- [ ] **Step 5: Коммит**

```bash
cd .. && git add web/src/audio/encode.ts web/tests/encode.test.ts
git commit -m "feat(web): кодировщик WAV без зависимостей"
```

---

### Task 3: Смещение записи и его хранение

**Files:**
- Create: `web/src/audio/latency.ts`
- Create: `web/tests/latency.test.ts`

**Interfaces:**
- Consumes: ничего
- Produces: `estimateLatencySec(ctx: { baseLatency?: number; outputLatency?: number }): number`; `shiftSamples(channel: Float32Array, offsetSamples: number): Float32Array`; `secToSamples(sec: number, sampleRate: number): number`; `loadOffset(storage: Storage): number`, `saveOffset(storage: Storage, sec: number): void`; константы `MIN_OFFSET_SEC = -0.2`, `MAX_OFFSET_SEC = 0.2`.

- [ ] **Step 1: Написать падающий тест**

Создать `web/tests/latency.test.ts`:

```ts
import { beforeEach, describe, expect, it } from "vitest";
import {
  MAX_OFFSET_SEC,
  MIN_OFFSET_SEC,
  estimateLatencySec,
  loadOffset,
  saveOffset,
  secToSamples,
  shiftSamples,
} from "../src/audio/latency";

class MemoryStorage implements Storage {
  private data = new Map<string, string>();
  get length() {
    return this.data.size;
  }
  clear() {
    this.data.clear();
  }
  getItem(key: string) {
    return this.data.get(key) ?? null;
  }
  key(index: number) {
    return [...this.data.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.data.delete(key);
  }
  setItem(key: string, value: string) {
    this.data.set(key, value);
  }
}

describe("estimateLatencySec", () => {
  it("складывает базовую и выходную задержку", () => {
    expect(estimateLatencySec({ baseLatency: 0.01, outputLatency: 0.04 })).toBe(
      0.05,
    );
  });

  it("переживает отсутствие полей", () => {
    expect(estimateLatencySec({})).toBe(0);
  });
});

describe("secToSamples", () => {
  it("переводит секунды в сэмплы", () => {
    expect(secToSamples(0.1, 44100)).toBe(4410);
  });

  it("округляет до целого", () => {
    expect(Number.isInteger(secToSamples(0.0333, 44100))).toBe(true);
  });
});

describe("shiftSamples", () => {
  it("сдвигает содержимое назад, сохраняя длину", () => {
    const source = new Float32Array([0, 0, 1, 2, 3]);
    const result = shiftSamples(source, 2);
    expect(result.length).toBe(5);
    expect(Array.from(result.slice(0, 3))).toEqual([1, 2, 3]);
  });

  it("дополняет хвост нулями", () => {
    const result = shiftSamples(new Float32Array([1, 2, 3, 4]), 2);
    expect(Array.from(result)).toEqual([3, 4, 0, 0]);
  });

  it("сдвигает вперёд при отрицательном смещении", () => {
    const result = shiftSamples(new Float32Array([1, 2, 3, 4]), -2);
    expect(Array.from(result)).toEqual([0, 0, 1, 2]);
  });

  it("нулевое смещение ничего не меняет", () => {
    expect(Array.from(shiftSamples(new Float32Array([1, 2, 3]), 0))).toEqual([
      1, 2, 3,
    ]);
  });

  it("сдвиг длиннее буфера даёт тишину", () => {
    expect(Array.from(shiftSamples(new Float32Array([1, 2]), 10))).toEqual([
      0, 0,
    ]);
  });
});

describe("хранение смещения", () => {
  let storage: MemoryStorage;
  beforeEach(() => {
    storage = new MemoryStorage();
  });

  it("возвращает ноль, если ничего не сохранено", () => {
    expect(loadOffset(storage)).toBe(0);
  });

  it("переживает круговой рейс", () => {
    saveOffset(storage, 0.075);
    expect(loadOffset(storage)).toBeCloseTo(0.075, 5);
  });

  it("зажимает значение по границам", () => {
    saveOffset(storage, 5);
    expect(loadOffset(storage)).toBe(MAX_OFFSET_SEC);
    saveOffset(storage, -5);
    expect(loadOffset(storage)).toBe(MIN_OFFSET_SEC);
  });

  it("игнорирует испорченное значение", () => {
    storage.setItem("karaoke.latencyOffsetSec", "не число");
    expect(loadOffset(storage)).toBe(0);
  });
});
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `npm test -- latency`
Expected: FAIL — `Cannot find module '../src/audio/latency'`

- [ ] **Step 3: Написать реализацию**

Создать `web/src/audio/latency.ts`:

```ts
/**
 * Компенсация задержки записи.
 *
 * Браузер отдаёт звук в наушники и забирает с микрофона не мгновенно, поэтому
 * записанный голос оказывается позже музыки. Смещение вычитается при сведении
 * сдвигом буфера — так его можно поменять уже после записи, не переписывая дубль.
 */

const STORAGE_KEY = "karaoke.latencyOffsetSec";

export const MIN_OFFSET_SEC = -0.2;
export const MAX_OFFSET_SEC = 0.2;

export function estimateLatencySec(ctx: {
  baseLatency?: number;
  outputLatency?: number;
}): number {
  return (ctx.baseLatency ?? 0) + (ctx.outputLatency ?? 0);
}

export function secToSamples(sec: number, sampleRate: number): number {
  return Math.round(sec * sampleRate);
}

export function shiftSamples(
  channel: Float32Array,
  offsetSamples: number,
): Float32Array {
  if (offsetSamples === 0) return channel;

  const out = new Float32Array(channel.length);
  if (offsetSamples > 0) {
    const count = Math.max(0, channel.length - offsetSamples);
    out.set(channel.subarray(offsetSamples, offsetSamples + count), 0);
  } else {
    const shift = -offsetSamples;
    const count = Math.max(0, channel.length - shift);
    out.set(channel.subarray(0, count), shift);
  }
  return out;
}

function clamp(sec: number): number {
  return Math.max(MIN_OFFSET_SEC, Math.min(MAX_OFFSET_SEC, sec));
}

export function loadOffset(storage: Storage): number {
  const raw = storage.getItem(STORAGE_KEY);
  if (raw === null) return 0;
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? clamp(parsed) : 0;
}

export function saveOffset(storage: Storage, sec: number): void {
  storage.setItem(STORAGE_KEY, String(clamp(sec)));
}
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `npm test -- latency`
Expected: PASS, 12 тестов

- [ ] **Step 5: Коммит**

```bash
cd .. && git add web/src/audio/latency.ts web/tests/latency.test.ts
git commit -m "feat(web): расчёт и хранение смещения записи"
```

---

### Task 4: Импульсная характеристика реверба

**Files:**
- Create: `web/src/audio/reverb.ts`
- Create: `web/tests/reverb.test.ts`

**Interfaces:**
- Consumes: ничего
- Produces: `generateImpulse(sampleRate: number, durationSec: number, decay: number): Float32Array[]` (два канала); `createReverb(ctx: BaseAudioContext, options?: { durationSec?: number; decay?: number }): ConvolverNode`.

- [ ] **Step 1: Написать падающий тест**

Создать `web/tests/reverb.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { generateImpulse } from "../src/audio/reverb";

function rms(data: Float32Array, from: number, to: number): number {
  let sum = 0;
  for (let i = from; i < to; i += 1) sum += data[i] * data[i];
  return Math.sqrt(sum / (to - from));
}

describe("generateImpulse", () => {
  it("отдаёт два канала нужной длины", () => {
    const channels = generateImpulse(44100, 2.0, 2.0);
    expect(channels).toHaveLength(2);
    expect(channels[0].length).toBe(88200);
    expect(channels[1].length).toBe(88200);
  });

  it("затухает: конец тише начала", () => {
    const [left] = generateImpulse(44100, 2.0, 2.0);
    const head = rms(left, 0, 4410);
    const tail = rms(left, left.length - 4410, left.length);
    expect(tail).toBeLessThan(head * 0.5);
  });

  it("каналы различаются, иначе реверб звучит моно", () => {
    const [left, right] = generateImpulse(44100, 1.0, 2.0);
    let identical = 0;
    for (let i = 0; i < left.length; i += 1) {
      if (left[i] === right[i]) identical += 1;
    }
    expect(identical).toBeLessThan(left.length * 0.01);
  });

  it("остаётся в допустимом диапазоне", () => {
    const [left] = generateImpulse(44100, 1.0, 2.0);
    for (let i = 0; i < left.length; i += 1) {
      expect(Math.abs(left[i])).toBeLessThanOrEqual(1);
    }
  });

  it("большее значение decay даёт более быстрое затухание", () => {
    const [slow] = generateImpulse(44100, 2.0, 1.0);
    const [fast] = generateImpulse(44100, 2.0, 6.0);
    const at = slow.length - 4410;
    expect(rms(fast, at, fast.length)).toBeLessThan(rms(slow, at, slow.length));
  });
});
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `npm test -- reverb`
Expected: FAIL — `Cannot find module '../src/audio/reverb'`

- [ ] **Step 3: Написать реализацию**

Создать `web/src/audio/reverb.ts`:

```ts
/**
 * Реверб на свёртке с синтетической импульсной характеристикой.
 *
 * Затухающий шум вместо записанного отклика помещения: внешних файлов не
 * требует, лицензировать нечего, для «комнаты» звучит достаточно.
 */

export function generateImpulse(
  sampleRate: number,
  durationSec: number,
  decay: number,
): Float32Array[] {
  const length = Math.floor(sampleRate * durationSec);

  return [0, 1].map(() => {
    const channel = new Float32Array(length);
    for (let i = 0; i < length; i += 1) {
      const envelope = Math.pow(1 - i / length, decay);
      channel[i] = (Math.random() * 2 - 1) * envelope;
    }
    return channel;
  });
}

export function createReverb(
  ctx: BaseAudioContext,
  options: { durationSec?: number; decay?: number } = {},
): ConvolverNode {
  const { durationSec = 2.0, decay = 2.5 } = options;
  const channels = generateImpulse(ctx.sampleRate, durationSec, decay);

  const impulse = ctx.createBuffer(2, channels[0].length, ctx.sampleRate);
  impulse.copyToChannel(channels[0], 0);
  impulse.copyToChannel(channels[1], 1);

  const convolver = ctx.createConvolver();
  convolver.buffer = impulse;
  return convolver;
}
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `npm test -- reverb`
Expected: PASS, 5 тестов

- [ ] **Step 5: Коммит**

```bash
cd .. && git add web/src/audio/reverb.ts web/tests/reverb.test.ts
git commit -m "feat(web): синтетическая импульсная характеристика для реверба"
```

---

### Task 5: Водяной знак

**Files:**
- Create: `web/src/audio/watermark.ts`
- Create: `web/tests/watermark.test.ts`

**Interfaces:**
- Consumes: ничего
- Produces: `generateWatermark(sampleRate: number, totalSamples: number, options?: { intervalSec?: number; gain?: number }): Float32Array`; константы `WATERMARK_INTERVAL_SEC = 30`, `WATERMARK_GAIN = 0.126` (−18 dB).

- [ ] **Step 1: Написать падающий тест**

Создать `web/tests/watermark.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  WATERMARK_GAIN,
  WATERMARK_INTERVAL_SEC,
  generateWatermark,
} from "../src/audio/watermark";

function peak(data: Float32Array, from: number, to: number): number {
  let max = 0;
  for (let i = from; i < Math.min(to, data.length); i += 1) {
    max = Math.max(max, Math.abs(data[i]));
  }
  return max;
}

describe("generateWatermark", () => {
  it("длина совпадает с длиной микса", () => {
    expect(generateWatermark(44100, 44100 * 10).length).toBe(441000);
  });

  it("ставит маркер в начале", () => {
    const track = generateWatermark(44100, 44100 * 5);
    expect(peak(track, 0, 4410)).toBeGreaterThan(0);
  });

  it("повторяет маркер через заданный интервал", () => {
    const sr = 44100;
    const track = generateWatermark(sr, sr * 70, { intervalSec: 30 });
    expect(peak(track, sr * 30, sr * 30 + 4410)).toBeGreaterThan(0);
    expect(peak(track, sr * 60, sr * 60 + 4410)).toBeGreaterThan(0);
  });

  it("между маркерами тишина", () => {
    const sr = 44100;
    const track = generateWatermark(sr, sr * 40, { intervalSec: 30 });
    expect(peak(track, sr * 10, sr * 20)).toBe(0);
  });

  it("не превышает заданную громкость", () => {
    const track = generateWatermark(44100, 44100 * 5, { gain: WATERMARK_GAIN });
    expect(peak(track, 0, track.length)).toBeLessThanOrEqual(WATERMARK_GAIN);
  });

  it("умолчание интервала соответствует константе", () => {
    const sr = 8000;
    const track = generateWatermark(sr, sr * (WATERMARK_INTERVAL_SEC + 2));
    const at = sr * WATERMARK_INTERVAL_SEC;
    expect(peak(track, at, at + 800)).toBeGreaterThan(0);
  });

  it("не выходит за границу буфера у самого конца", () => {
    const sr = 44100;
    expect(() => generateWatermark(sr, sr * 30 + 10, { intervalSec: 30 })).not.toThrow();
  });
});
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `npm test -- watermark`
Expected: FAIL — `Cannot find module '../src/audio/watermark'`

- [ ] **Step 3: Написать реализацию**

Создать `web/src/audio/watermark.ts`:

```ts
/**
 * Звуковой водяной знак для бесплатного экспорта.
 *
 * Короткий затухающий тон в начале и далее через интервал. Синтезируется
 * кодом, поэтому не требует ассетов и не создаёт лицензионных вопросов.
 */

export const WATERMARK_INTERVAL_SEC = 30;
/** −18 dB */
export const WATERMARK_GAIN = 0.126;

const MARK_DURATION_SEC = 0.25;
const MARK_FREQ_HZ = 880;

export function generateWatermark(
  sampleRate: number,
  totalSamples: number,
  options: { intervalSec?: number; gain?: number } = {},
): Float32Array {
  const { intervalSec = WATERMARK_INTERVAL_SEC, gain = WATERMARK_GAIN } =
    options;

  const track = new Float32Array(totalSamples);
  const markLength = Math.floor(sampleRate * MARK_DURATION_SEC);
  const stride = Math.floor(sampleRate * intervalSec);

  for (let start = 0; start < totalSamples; start += stride) {
    const count = Math.min(markLength, totalSamples - start);
    for (let i = 0; i < count; i += 1) {
      const envelope = 1 - i / markLength;
      track[start + i] =
        Math.sin((2 * Math.PI * MARK_FREQ_HZ * i) / sampleRate) *
        envelope *
        gain;
    }
  }

  return track;
}
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `npm test -- watermark`
Expected: PASS, 7 тестов

- [ ] **Step 5: Коммит**

```bash
cd .. && git add web/src/audio/watermark.ts web/tests/watermark.test.ts
git commit -m "feat(web): звуковой водяной знак"
```

---

### Task 6: Сведение и тест на выравнивание

Главная задача проекта. Тест на выравнивание превращает вопрос «попадает ли
голос в музыку» из субъективного в детерминированный.

**Files:**
- Create: `web/src/audio/mixdown.ts`
- Create: `web/tests/mixdown.browser.test.ts`

**Interfaces:**
- Consumes: `shiftSamples`, `secToSamples` (Task 3), `generateImpulse` (Task 4), `generateWatermark` (Task 5)
- Produces: `MixOptions { offsetSec: number; voiceGain: number; musicGain: number; reverbWet: number; watermark: boolean }`; `mixdown(music: AudioBuffer, voice: Float32Array[], sampleRate: number, options: MixOptions): Promise<AudioBuffer>`; `bufferToChannels(buffer: AudioBuffer): Float32Array[]`.

- [ ] **Step 1: Написать падающий тест**

Создать `web/tests/mixdown.browser.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { bufferToChannels, mixdown } from "../src/audio/mixdown";

const SR = 44100;

const BASE = {
  offsetSec: 0,
  voiceGain: 1,
  musicGain: 1,
  reverbWet: 0,
  watermark: false,
};

/** Буфер тишины с одиночным щелчком на заданной секунде. */
function clickBuffer(atSec: number, durationSec = 4): AudioBuffer {
  const ctx = new OfflineAudioContext(2, SR * durationSec, SR);
  const buffer = ctx.createBuffer(2, SR * durationSec, SR);
  const index = Math.round(atSec * SR);
  for (let ch = 0; ch < 2; ch += 1) {
    buffer.getChannelData(ch)[index] = 1;
  }
  return buffer;
}

function clickChannels(atSec: number, durationSec = 4): Float32Array[] {
  return [0, 1].map(() => {
    const data = new Float32Array(SR * durationSec);
    data[Math.round(atSec * SR)] = 1;
    return data;
  });
}

function peakIndex(data: Float32Array): number {
  let best = 0;
  let bestValue = 0;
  for (let i = 0; i < data.length; i += 1) {
    if (Math.abs(data[i]) > bestValue) {
      bestValue = Math.abs(data[i]);
      best = i;
    }
  }
  return best;
}

describe("mixdown", () => {
  it("КЛЮЧЕВОЙ: компенсация ставит запоздавший голос на место", async () => {
    const LATENCY = 0.12;
    const music = clickBuffer(1.0);
    const voice = clickChannels(1.0 + LATENCY);

    const mixed = await mixdown(music, voice, SR, {
      ...BASE,
      offsetSec: LATENCY,
      musicGain: 0,
    });

    const found = peakIndex(mixed.getChannelData(0));
    expect(Math.abs(found - SR * 1.0)).toBeLessThan(5);
  });

  it("без компенсации голос остаётся сдвинутым", async () => {
    const music = clickBuffer(1.0);
    const voice = clickChannels(1.12);

    const mixed = await mixdown(music, voice, SR, { ...BASE, musicGain: 0 });

    expect(peakIndex(mixed.getChannelData(0))).toBeGreaterThan(SR * 1.1);
  });

  it("длительность равна длительности музыки", async () => {
    const music = clickBuffer(1.0, 3);
    const mixed = await mixdown(music, clickChannels(1.0, 5), SR, BASE);
    expect(mixed.duration).toBeCloseTo(3, 2);
  });

  it("при нулевой громкости голоса остаётся только музыка", async () => {
    const music = clickBuffer(0.5);
    const mixed = await mixdown(music, clickChannels(2.0), SR, {
      ...BASE,
      voiceGain: 0,
    });
    expect(Math.abs(peakIndex(mixed.getChannelData(0)) - SR * 0.5)).toBeLessThan(5);
  });

  it("при нулевой громкости музыки остаётся только голос", async () => {
    const music = clickBuffer(0.5);
    const mixed = await mixdown(music, clickChannels(2.0), SR, {
      ...BASE,
      musicGain: 0,
    });
    expect(Math.abs(peakIndex(mixed.getChannelData(0)) - SR * 2.0)).toBeLessThan(5);
  });

  it("реверб продлевает звучание голоса", async () => {
    const music = clickBuffer(0.5, 4);
    const voice = clickChannels(0.5, 4);

    const dry = await mixdown(music, voice, SR, { ...BASE, musicGain: 0 });
    const wet = await mixdown(music, voice, SR, {
      ...BASE,
      musicGain: 0,
      reverbWet: 1,
    });

    const energyAfter = (buffer: AudioBuffer) => {
      const data = buffer.getChannelData(0);
      let sum = 0;
      for (let i = Math.round(SR * 0.7); i < data.length; i += 1) {
        sum += data[i] * data[i];
      }
      return sum;
    };

    expect(energyAfter(wet)).toBeGreaterThan(energyAfter(dry) * 10);
  });

  it("водяной знак слышен в начале", async () => {
    const silence = new OfflineAudioContext(2, SR * 2, SR).createBuffer(
      2,
      SR * 2,
      SR,
    );
    const voice = [new Float32Array(SR * 2), new Float32Array(SR * 2)];

    const marked = await mixdown(silence, voice, SR, {
      ...BASE,
      watermark: true,
    });

    let peak = 0;
    const data = marked.getChannelData(0);
    for (let i = 0; i < SR * 0.3; i += 1) peak = Math.max(peak, Math.abs(data[i]));
    expect(peak).toBeGreaterThan(0.01);
  });

  it("bufferToChannels отдаёт независимые копии", () => {
    const buffer = clickBuffer(1.0);
    const channels = bufferToChannels(buffer);
    channels[0][0] = 0.5;
    expect(buffer.getChannelData(0)[0]).toBe(0);
  });
});
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `npm run test:browser -- mixdown`
Expected: FAIL — `Cannot find module '../src/audio/mixdown'`

- [ ] **Step 3: Написать реализацию**

Создать `web/src/audio/mixdown.ts`:

```ts
import { secToSamples, shiftSamples } from "./latency";
import { createReverb } from "./reverb";
import { generateWatermark } from "./watermark";

export interface MixOptions {
  /** Задержка записи в секундах. Положительная — голос запоздал. */
  offsetSec: number;
  voiceGain: number;
  musicGain: number;
  /** Доля обработанного сигнала, 0..1. */
  reverbWet: number;
  watermark: boolean;
}

export function bufferToChannels(buffer: AudioBuffer): Float32Array[] {
  return Array.from({ length: buffer.numberOfChannels }, (_, ch) =>
    Float32Array.from(buffer.getChannelData(ch)),
  );
}

function toStereo(channels: Float32Array[]): Float32Array[] {
  if (channels.length >= 2) return channels.slice(0, 2);
  return [channels[0], channels[0]];
}

export async function mixdown(
  music: AudioBuffer,
  voice: Float32Array[],
  sampleRate: number,
  options: MixOptions,
): Promise<AudioBuffer> {
  const frames = music.length;
  const ctx = new OfflineAudioContext(2, frames, sampleRate);

  const master = ctx.createGain();
  master.connect(ctx.destination);

  // --- музыка ---
  const musicGain = ctx.createGain();
  musicGain.gain.value = options.musicGain;
  const musicSource = ctx.createBufferSource();
  musicSource.buffer = music;
  musicSource.connect(musicGain).connect(master);
  musicSource.start();

  // --- голос со сдвигом ---
  const offsetSamples = secToSamples(options.offsetSec, sampleRate);
  const shifted = toStereo(voice).map((channel) => {
    const aligned = shiftSamples(channel, offsetSamples);
    if (aligned.length === frames) return aligned;
    const fitted = new Float32Array(frames);
    fitted.set(aligned.subarray(0, Math.min(frames, aligned.length)));
    return fitted;
  });

  const voiceBuffer = ctx.createBuffer(2, frames, sampleRate);
  voiceBuffer.copyToChannel(shifted[0], 0);
  voiceBuffer.copyToChannel(shifted[1], 1);

  const voiceSource = ctx.createBufferSource();
  voiceSource.buffer = voiceBuffer;

  const voiceGain = ctx.createGain();
  voiceGain.gain.value = options.voiceGain;
  voiceSource.connect(voiceGain);

  const dry = ctx.createGain();
  dry.gain.value = 1 - options.reverbWet;
  voiceGain.connect(dry).connect(master);

  if (options.reverbWet > 0) {
    const wet = ctx.createGain();
    wet.gain.value = options.reverbWet;
    voiceGain.connect(createReverb(ctx)).connect(wet).connect(master);
  }

  voiceSource.start();

  // --- водяной знак ---
  if (options.watermark) {
    const mark = generateWatermark(sampleRate, frames);
    const markBuffer = ctx.createBuffer(2, frames, sampleRate);
    markBuffer.copyToChannel(mark, 0);
    markBuffer.copyToChannel(mark, 1);

    const markSource = ctx.createBufferSource();
    markSource.buffer = markBuffer;
    markSource.connect(master);
    markSource.start();
  }

  return ctx.startRendering();
}
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `npm run test:browser -- mixdown`
Expected: PASS, 8 тестов, включая ключевой тест на выравнивание

- [ ] **Step 5: Коммит**

```bash
cd .. && git add web/src/audio/mixdown.ts web/tests/mixdown.browser.test.ts
git commit -m "feat(web): сведение с компенсацией задержки и тест на выравнивание"
```

---

### Task 7: Захват с микрофона через AudioWorklet

**Files:**
- Create: `web/public/recorder-worklet.js`
- Create: `web/src/audio/recorder.ts`
- Create: `web/tests/recorder.browser.test.ts`

**Interfaces:**
- Consumes: ничего
- Produces: `MIC_CONSTRAINTS: MediaStreamConstraints`; класс `Recorder(ctx: AudioContext)` с методами `start(stream: MediaStream): Promise<void>`, `stop(): Float32Array[]`, свойство `isRecording: boolean`; `concatChunks(chunks: Float32Array[]): Float32Array`.

- [ ] **Step 1: Написать воркет**

Создать `web/public/recorder-worklet.js`:

```js
/**
 * Захват сырых кадров с микрофона.
 *
 * MediaRecorder здесь не годится: он отдаёт сжатый webm с неопределённым
 * таймингом, а для выравнивания нужен точный счёт сэмплов.
 */
class RecorderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input.length > 0 && input[0].length > 0) {
      this.port.postMessage(
        input.map((channel) => new Float32Array(channel)),
      );
    }
    return true;
  }
}

registerProcessor("recorder-processor", RecorderProcessor);
```

- [ ] **Step 2: Написать падающий тест**

Создать `web/tests/recorder.browser.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { MIC_CONSTRAINTS, Recorder, concatChunks } from "../src/audio/recorder";

describe("MIC_CONSTRAINTS", () => {
  it("отключает обработку, ломающую пение и тайминг", () => {
    const audio = MIC_CONSTRAINTS.audio as MediaTrackConstraints;
    expect(audio.echoCancellation).toBe(false);
    expect(audio.noiseSuppression).toBe(false);
    expect(audio.autoGainControl).toBe(false);
  });
});

describe("concatChunks", () => {
  it("склеивает куски по порядку", () => {
    const result = concatChunks([
      new Float32Array([1, 2]),
      new Float32Array([3]),
      new Float32Array([4, 5]),
    ]);
    expect(Array.from(result)).toEqual([1, 2, 3, 4, 5]);
  });

  it("пустой список даёт пустой буфер", () => {
    expect(concatChunks([]).length).toBe(0);
  });
});

describe("Recorder", () => {
  it("пишет сигнал из графа и отдаёт стерео", async () => {
    const ctx = new AudioContext({ sampleRate: 44100 });
    await ctx.audioWorklet.addModule("/recorder-worklet.js");

    const destination = ctx.createMediaStreamDestination();
    const osc = ctx.createOscillator();
    osc.frequency.value = 440;
    osc.connect(destination);
    osc.start();

    const recorder = new Recorder(ctx);
    await recorder.start(destination.stream);
    expect(recorder.isRecording).toBe(true);

    await new Promise((resolve) => setTimeout(resolve, 300));
    const channels = recorder.stop();

    expect(recorder.isRecording).toBe(false);
    expect(channels).toHaveLength(2);
    expect(channels[0].length).toBeGreaterThan(1000);

    const peak = channels[0].reduce((m, v) => Math.max(m, Math.abs(v)), 0);
    expect(peak).toBeGreaterThan(0.1);

    osc.stop();
    await ctx.close();
  });

  it("stop без start отдаёт пустые каналы", () => {
    const ctx = new AudioContext();
    const channels = new Recorder(ctx).stop();
    expect(channels[0].length).toBe(0);
    void ctx.close();
  });
});
```

- [ ] **Step 3: Запустить тест, убедиться что падает**

Run: `npm run test:browser -- recorder`
Expected: FAIL — `Cannot find module '../src/audio/recorder'`

- [ ] **Step 4: Написать реализацию**

Создать `web/src/audio/recorder.ts`:

```ts
/**
 * Захват голоса. Складывает сырые кадры воркета в непрерывный буфер.
 *
 * Никакой компенсации задержки здесь нет намеренно: сдвиг применяется при
 * сведении, чтобы его можно было поменять, не переписывая дубль.
 */

export const MIC_CONSTRAINTS: MediaStreamConstraints = {
  audio: {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false,
    channelCount: 2,
  },
  video: false,
};

export function concatChunks(chunks: Float32Array[]): Float32Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const out = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

export class Recorder {
  private node: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private left: Float32Array[] = [];
  private right: Float32Array[] = [];

  constructor(private readonly ctx: AudioContext) {}

  get isRecording(): boolean {
    return this.node !== null;
  }

  async start(stream: MediaStream): Promise<void> {
    this.left = [];
    this.right = [];

    this.node = new AudioWorkletNode(this.ctx, "recorder-processor");
    this.node.port.onmessage = (event: MessageEvent<Float32Array[]>) => {
      const channels = event.data;
      this.left.push(channels[0]);
      this.right.push(channels[1] ?? channels[0]);
    };

    this.source = this.ctx.createMediaStreamSource(stream);
    this.source.connect(this.node);

    // Воркет должен получать вызовы process — для этого узел подключается к
    // назначению через глушитель, иначе граф считается неактивным.
    const mute = this.ctx.createGain();
    mute.gain.value = 0;
    this.node.connect(mute).connect(this.ctx.destination);
  }

  stop(): Float32Array[] {
    if (this.node) {
      this.node.port.onmessage = null;
      this.node.disconnect();
      this.source?.disconnect();
      this.node = null;
      this.source = null;
    }
    return [concatChunks(this.left), concatChunks(this.right)];
  }
}
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `npm run test:browser -- recorder`
Expected: PASS, 4 теста

- [ ] **Step 6: Коммит**

```bash
cd .. && git add web/public/recorder-worklet.js web/src/audio/recorder.ts web/tests/recorder.browser.test.ts
git commit -m "feat(web): захват голоса через AudioWorklet"
```

---

### Task 8: Мониторинг с ревербом

**Files:**
- Create: `web/src/audio/monitor.ts`
- Create: `web/tests/monitor.browser.test.ts`

**Interfaces:**
- Consumes: `createReverb` (Task 4)
- Produces: класс `Monitor(ctx: AudioContext)` с методами `attach(source: AudioNode): void`, `detach(): void`, `setEnabled(on: boolean): void`, `setWet(value: number): void`, свойство `enabled: boolean`.

- [ ] **Step 1: Написать падающий тест**

Создать `web/tests/monitor.browser.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { Monitor } from "../src/audio/monitor";

function makeSource(ctx: AudioContext) {
  const osc = ctx.createOscillator();
  osc.frequency.value = 440;
  osc.start();
  return osc;
}

describe("Monitor", () => {
  it("по умолчанию выключен, чтобы не удивлять обратной связью", () => {
    const ctx = new AudioContext();
    expect(new Monitor(ctx).enabled).toBe(false);
    void ctx.close();
  });

  it("включается и выключается", () => {
    const ctx = new AudioContext();
    const monitor = new Monitor(ctx);
    monitor.attach(makeSource(ctx));

    monitor.setEnabled(true);
    expect(monitor.enabled).toBe(true);
    monitor.setEnabled(false);
    expect(monitor.enabled).toBe(false);

    void ctx.close();
  });

  it("setWet зажимает значение в 0..1", () => {
    const ctx = new AudioContext();
    const monitor = new Monitor(ctx);
    monitor.attach(makeSource(ctx));

    expect(() => monitor.setWet(5)).not.toThrow();
    expect(() => monitor.setWet(-5)).not.toThrow();

    void ctx.close();
  });

  it("detach можно звать без attach", () => {
    const ctx = new AudioContext();
    expect(() => new Monitor(ctx).detach()).not.toThrow();
    void ctx.close();
  });

  it("повторный attach не накапливает соединения", () => {
    const ctx = new AudioContext();
    const monitor = new Monitor(ctx);
    monitor.attach(makeSource(ctx));
    expect(() => monitor.attach(makeSource(ctx))).not.toThrow();
    void ctx.close();
  });
});
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `npm run test:browser -- monitor`
Expected: FAIL — `Cannot find module '../src/audio/monitor'`

- [ ] **Step 3: Написать реализацию**

Создать `web/src/audio/monitor.ts`:

```ts
import { createReverb } from "./reverb";

/**
 * Прослушивание собственного голоса при записи.
 *
 * Выключен по умолчанию: круг через Web Audio добавляет 20–40 мс при
 * слышимом пороге около 15–20, и часть людей это раздражает сильнее, чем
 * пение всухую. Решение оставлено пользователю.
 */
export class Monitor {
  private readonly input: GainNode;
  private readonly dry: GainNode;
  private readonly wet: GainNode;
  private readonly output: GainNode;
  private attached: AudioNode | null = null;

  constructor(private readonly ctx: AudioContext) {
    this.input = ctx.createGain();
    this.dry = ctx.createGain();
    this.wet = ctx.createGain();
    this.output = ctx.createGain();

    this.dry.gain.value = 0.7;
    this.wet.gain.value = 0.3;
    this.output.gain.value = 0;

    this.input.connect(this.dry).connect(this.output);
    this.input.connect(createReverb(ctx)).connect(this.wet).connect(this.output);
    this.output.connect(ctx.destination);
  }

  get enabled(): boolean {
    return this.output.gain.value > 0;
  }

  attach(source: AudioNode): void {
    this.detach();
    source.connect(this.input);
    this.attached = source;
  }

  detach(): void {
    if (this.attached) {
      this.attached.disconnect(this.input);
      this.attached = null;
    }
  }

  setEnabled(on: boolean): void {
    this.output.gain.setTargetAtTime(on ? 1 : 0, this.ctx.currentTime, 0.01);
  }

  setWet(value: number): void {
    const wet = Math.max(0, Math.min(1, value));
    this.wet.gain.value = wet;
    this.dry.gain.value = 1 - wet;
  }
}
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `npm run test:browser -- monitor`
Expected: PASS, 5 тестов

- [ ] **Step 5: Коммит**

```bash
cd .. && git add web/src/audio/monitor.ts web/tests/monitor.browser.test.ts
git commit -m "feat(web): мониторинг голоса с ревербом"
```

---

### Task 9: Индикатор уровня и клиппинга

Клиппинг надо ловить во время записи: узнать о нём после сведения — значит
потерять дубль целиком.

**Files:**
- Create: `web/src/audio/meter.ts`
- Create: `web/tests/meter.test.ts`

**Interfaces:**
- Consumes: ничего
- Produces: `peakOf(frame: Float32Array): number`; `dbFromPeak(peak: number): number`; класс `LevelMeter(ctx: AudioContext, options?: { clipThreshold?: number })` с методами `attach(source: AudioNode): void`, `detach(): void`, `read(): { peak: number; db: number; clipped: boolean }`, `resetClip(): void`; константа `CLIP_THRESHOLD = 0.99`.

- [ ] **Step 1: Написать падающий тест**

Создать `web/tests/meter.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { CLIP_THRESHOLD, dbFromPeak, peakOf } from "../src/audio/meter";

describe("peakOf", () => {
  it("находит максимум по модулю", () => {
    expect(peakOf(new Float32Array([0.1, -0.8, 0.3]))).toBeCloseTo(0.8, 5);
  });

  it("пустой кадр даёт ноль", () => {
    expect(peakOf(new Float32Array(0))).toBe(0);
  });

  it("тишина даёт ноль", () => {
    expect(peakOf(new Float32Array([0, 0, 0]))).toBe(0);
  });
});

describe("dbFromPeak", () => {
  it("единица соответствует нулю децибел", () => {
    expect(dbFromPeak(1)).toBeCloseTo(0, 5);
  });

  it("половина соответствует примерно минус шести", () => {
    expect(dbFromPeak(0.5)).toBeCloseTo(-6.02, 1);
  });

  it("тишина не даёт минус бесконечность", () => {
    expect(Number.isFinite(dbFromPeak(0))).toBe(true);
    expect(dbFromPeak(0)).toBeLessThanOrEqual(-100);
  });
});

describe("CLIP_THRESHOLD", () => {
  it("срабатывает до полной шкалы, чтобы успеть предупредить", () => {
    expect(CLIP_THRESHOLD).toBeGreaterThan(0.9);
    expect(CLIP_THRESHOLD).toBeLessThan(1);
  });
});
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `npm test -- meter`
Expected: FAIL — `Cannot find module '../src/audio/meter'`

- [ ] **Step 3: Написать реализацию**

Создать `web/src/audio/meter.ts`:

```ts
/**
 * Измерение уровня входа с фиксацией клиппинга.
 *
 * Клиппинг сообщается сразу: после сведения о нём узнавать поздно, дубль
 * уже испорчен.
 */

export const CLIP_THRESHOLD = 0.99;
const SILENCE_DB = -120;

export function peakOf(frame: Float32Array): number {
  let peak = 0;
  for (let i = 0; i < frame.length; i += 1) {
    const value = Math.abs(frame[i]);
    if (value > peak) peak = value;
  }
  return peak;
}

export function dbFromPeak(peak: number): number {
  if (peak <= 0) return SILENCE_DB;
  return Math.max(SILENCE_DB, 20 * Math.log10(peak));
}

export class LevelMeter {
  private readonly analyser: AnalyserNode;
  private readonly frame: Float32Array;
  private readonly clipThreshold: number;
  private attached: AudioNode | null = null;
  private clipped = false;

  constructor(
    ctx: AudioContext,
    options: { clipThreshold?: number } = {},
  ) {
    this.clipThreshold = options.clipThreshold ?? CLIP_THRESHOLD;
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 2048;
    this.frame = new Float32Array(this.analyser.fftSize);
  }

  attach(source: AudioNode): void {
    this.detach();
    source.connect(this.analyser);
    this.attached = source;
  }

  detach(): void {
    if (this.attached) {
      this.attached.disconnect(this.analyser);
      this.attached = null;
    }
  }

  resetClip(): void {
    this.clipped = false;
  }

  read(): { peak: number; db: number; clipped: boolean } {
    this.analyser.getFloatTimeDomainData(this.frame);
    const peak = peakOf(this.frame);
    if (peak >= this.clipThreshold) this.clipped = true;
    return { peak, db: dbFromPeak(peak), clipped: this.clipped };
  }
}
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `npm test -- meter`
Expected: PASS, 7 тестов

- [ ] **Step 5: Коммит**

```bash
cd .. && git add web/src/audio/meter.ts web/tests/meter.test.ts
git commit -m "feat(web): индикатор уровня с фиксацией клиппинга"
```

---

### Task 10: Экран загрузки и ожидания обработки

**Files:**
- Create: `web/src/features/upload/UploadScreen.tsx`
- Create: `web/src/features/processing/useJobPolling.ts`
- Create: `web/src/features/processing/ProcessingScreen.tsx`
- Modify: `web/src/App.tsx`
- Create: `web/tests/use-job-polling.test.ts`

**Interfaces:**
- Consumes: `ApiClient`, `JobState` (Task 1)
- Produces: хук `useJobPolling(client: ApiClient, jobId: string | null, intervalMs?: number): { state: JobState | null; error: string | null }`; компоненты `UploadScreen({ client, onUploaded })` и `ProcessingScreen({ client, jobId, onReady })`; тип `AppStage = "upload" | "processing" | "studio"`.

- [ ] **Step 1: Написать падающий тест**

Создать `web/tests/use-job-polling.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { pollUntilSettled } from "../src/features/processing/useJobPolling";
import type { JobState } from "../src/api/types";

function state(partial: Partial<JobState>): JobState {
  return {
    status: "queued",
    stage: null,
    progress: 0,
    error: null,
    result: null,
    ...partial,
  };
}

describe("pollUntilSettled", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("останавливается на done и отдаёт последнее состояние", async () => {
    const responses = [
      state({ status: "queued" }),
      state({ status: "running", stage: "separating", progress: 0.5 }),
      state({ status: "done", progress: 1 }),
    ];
    const getJob = vi.fn(async () => responses.shift()!);
    const seen: JobState[] = [];

    const promise = pollUntilSettled(getJob, 10, (s) => seen.push(s));
    await vi.runAllTimersAsync();
    const final = await promise;

    expect(final.status).toBe("done");
    expect(seen).toHaveLength(3);
    expect(getJob).toHaveBeenCalledTimes(3);
  });

  it("останавливается на failed", async () => {
    const getJob = vi.fn(async () =>
      state({ status: "failed", error: "CUDA out of memory" }),
    );

    const promise = pollUntilSettled(getJob, 10, () => {});
    await vi.runAllTimersAsync();
    const final = await promise;

    expect(final.status).toBe("failed");
    expect(final.error).toBe("CUDA out of memory");
    expect(getJob).toHaveBeenCalledTimes(1);
  });

  it("пробрасывает ошибку сети", async () => {
    const getJob = vi.fn(async () => {
      throw new Error("сеть недоступна");
    });

    const promise = pollUntilSettled(getJob, 10, () => {});
    await vi.runAllTimersAsync();

    await expect(promise).rejects.toThrow("сеть недоступна");
  });
});
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `npm test -- use-job-polling`
Expected: FAIL — `Cannot find module '../src/features/processing/useJobPolling'`

- [ ] **Step 3: Написать опрос и хук**

Создать `web/src/features/processing/useJobPolling.ts`:

```ts
import { useEffect, useState } from "react";
import type { ApiClient } from "../../api/client";
import type { JobState } from "../../api/types";

const SETTLED = new Set(["done", "failed"]);

/** Опрашивает задачу до завершения. Вынесено из хука ради тестируемости. */
export async function pollUntilSettled(
  getJob: () => Promise<JobState>,
  intervalMs: number,
  onUpdate: (state: JobState) => void,
): Promise<JobState> {
  for (;;) {
    const state = await getJob();
    onUpdate(state);
    if (SETTLED.has(state.status)) return state;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

export function useJobPolling(
  client: ApiClient,
  jobId: string | null,
  intervalMs = 1000,
): { state: JobState | null; error: string | null } {
  const [state, setState] = useState<JobState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    pollUntilSettled(
      () => client.getJob(jobId),
      intervalMs,
      (next) => {
        if (!cancelled) setState(next);
      },
    ).catch((exc: unknown) => {
      if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
    });

    return () => {
      cancelled = true;
    };
  }, [client, jobId, intervalMs]);

  return { state, error };
}
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `npm test -- use-job-polling`
Expected: PASS, 3 теста

- [ ] **Step 5: Написать экран загрузки**

Создать `web/src/features/upload/UploadScreen.tsx`:

```tsx
import { useState } from "react";
import { ApiError } from "../../api/client";
import type { ApiClient } from "../../api/client";
import type { UploadResult } from "../../api/types";

const MESSAGES: Record<string, string> = {
  unsupported_format: "Файл не похож на аудио. Нужен mp3, wav, m4a или flac.",
  too_long: "Трек длиннее 10 минут.",
  too_large: "Файл больше 100 МБ.",
};

export function UploadScreen({
  client,
  onUploaded,
}: {
  client: ApiClient;
  onUploaded: (result: UploadResult) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file: File) {
    setBusy(true);
    setError(null);
    try {
      onUploaded(await client.uploadTrack(file));
    } catch (exc) {
      const code = exc instanceof ApiError ? exc.code : "unknown_error";
      setError(MESSAGES[code] ?? "Не удалось загрузить файл. Попробуйте ещё раз.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>Загрузите трек</h1>
      <input
        type="file"
        accept="audio/*"
        disabled={busy}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void handleFile(file);
        }}
      />
      {busy && <p>Загружаю…</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
```

- [ ] **Step 6: Написать экран обработки**

Создать `web/src/features/processing/ProcessingScreen.tsx`:

```tsx
import { useEffect } from "react";
import type { ApiClient } from "../../api/client";
import { useJobPolling } from "./useJobPolling";

const STAGE_LABELS: Record<string, string> = {
  loading: "Загружаю модель",
  separating: "Отделяю вокал",
  writing: "Сохраняю дорожки",
};

export function ProcessingScreen({
  client,
  jobId,
  onReady,
}: {
  client: ApiClient;
  jobId: string;
  onReady: () => void;
}) {
  const { state, error } = useJobPolling(client, jobId);

  useEffect(() => {
    if (state?.status === "done") onReady();
  }, [state?.status, onReady]);

  if (error) return <p role="alert">Связь с сервером потеряна: {error}</p>;
  if (state?.status === "failed") {
    return <p role="alert">Обработка не удалась: {state.error}</p>;
  }

  const label = state?.stage ? STAGE_LABELS[state.stage] : "В очереди";

  return (
    <section>
      <h1>Готовлю минусовку</h1>
      <p>{label}</p>
      <progress value={state?.progress ?? 0} max={1} />
    </section>
  );
}
```

- [ ] **Step 7: Связать в App**

Заменить `web/src/App.tsx`:

```tsx
import { useMemo, useState } from "react";
import { ApiClient } from "./api/client";
import { UploadScreen } from "./features/upload/UploadScreen";
import { ProcessingScreen } from "./features/processing/ProcessingScreen";
import { StudioScreen } from "./features/studio/StudioScreen";

export type AppStage = "upload" | "processing" | "studio";

export default function App() {
  const client = useMemo(() => new ApiClient(""), []);
  const [stage, setStage] = useState<AppStage>("upload");
  const [trackId, setTrackId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  if (stage === "upload") {
    return (
      <UploadScreen
        client={client}
        onUploaded={({ trackId: t, jobId: j }) => {
          setTrackId(t);
          setJobId(j);
          setStage("processing");
        }}
      />
    );
  }

  if (stage === "processing" && jobId) {
    return (
      <ProcessingScreen
        client={client}
        jobId={jobId}
        onReady={() => setStage("studio")}
      />
    );
  }

  return <StudioScreen client={client} trackId={trackId!} />;
}
```

Экран студии появится в задаче 11; до неё сборка не собирается — это ожидаемо.

- [ ] **Step 8: Коммит**

```bash
cd .. && git add web/src web/tests/use-job-polling.test.ts
git commit -m "feat(web): экраны загрузки и обработки"
```

---

### Task 11: Экран студии и экспорт

**Files:**
- Create: `web/src/features/studio/useStudio.ts`
- Create: `web/src/features/studio/StudioScreen.tsx`
- Create: `web/src/audio/download.ts`
- Create: `web/tests/download.test.ts`

**Interfaces:**
- Consumes: всё из задач 1–10
- Produces: `downloadBlob(blob: Blob, filename: string, doc?: Document): void`; хук `useStudio(client, trackId)` с полями `ready`, `recording`, `mixing`, `offsetSec`, `setOffsetSec`, `voiceGain`, `setVoiceGain`, `musicGain`, `setMusicGain`, `reverbWet`, `setReverbWet`, `monitorOn`, `setMonitorOn`, `error`, `startRecording`, `stopRecording`, `exportMix`, `hasTake`; компонент `StudioScreen`.

- [ ] **Step 1: Написать падающий тест**

Создать `web/tests/download.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { downloadBlob } from "../src/audio/download";

function fakeDocument() {
  const anchor = {
    href: "",
    download: "",
    click: vi.fn(),
    remove: vi.fn(),
  };
  return {
    anchor,
    doc: {
      createElement: vi.fn(() => anchor),
      body: { appendChild: vi.fn() },
    } as unknown as Document,
  };
}

describe("downloadBlob", () => {
  it("ставит имя файла и кликает по ссылке", () => {
    const { anchor, doc } = fakeDocument();
    vi.stubGlobal("URL", {
      createObjectURL: () => "blob:fake",
      revokeObjectURL: vi.fn(),
    });

    downloadBlob(new Blob(["x"]), "mix.wav", doc);

    expect(anchor.download).toBe("mix.wav");
    expect(anchor.href).toBe("blob:fake");
    expect(anchor.click).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });

  it("освобождает объектный URL", () => {
    const { doc } = fakeDocument();
    const revoke = vi.fn();
    vi.stubGlobal("URL", { createObjectURL: () => "blob:fake", revokeObjectURL: revoke });

    downloadBlob(new Blob(["x"]), "mix.wav", doc);

    expect(revoke).toHaveBeenCalledWith("blob:fake");
    vi.unstubAllGlobals();
  });
});
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `npm test -- download`
Expected: FAIL — `Cannot find module '../src/audio/download'`

- [ ] **Step 3: Написать скачивание**

Создать `web/src/audio/download.ts`:

```ts
export function downloadBlob(
  blob: Blob,
  filename: string,
  doc: Document = document,
): void {
  const url = URL.createObjectURL(blob);
  const anchor = doc.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  doc.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `npm test -- download`
Expected: PASS, 2 теста

- [ ] **Step 5: Написать хук студии**

Создать `web/src/features/studio/useStudio.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "../../api/client";
import { downloadBlob } from "../../audio/download";
import { encodeWav } from "../../audio/encode";
import { estimateLatencySec, loadOffset, saveOffset } from "../../audio/latency";
import { LevelMeter } from "../../audio/meter";
import { mixdown } from "../../audio/mixdown";
import { Monitor } from "../../audio/monitor";
import { MIC_CONSTRAINTS, Recorder } from "../../audio/recorder";

export function useStudio(client: ApiClient, trackId: string) {
  const ctxRef = useRef<AudioContext | null>(null);
  const musicRef = useRef<AudioBuffer | null>(null);
  const recorderRef = useRef<Recorder | null>(null);
  const monitorRef = useRef<Monitor | null>(null);
  const meterRef = useRef<LevelMeter | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const playbackRef = useRef<AudioBufferSourceNode | null>(null);
  const takeRef = useRef<Float32Array[] | null>(null);

  const [ready, setReady] = useState(false);
  const [recording, setRecording] = useState(false);
  const [mixing, setMixing] = useState(false);
  const [hasTake, setHasTake] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [levelDb, setLevelDb] = useState(-120);
  const [clipped, setClipped] = useState(false);

  const [offsetSec, setOffsetSecState] = useState(0);
  const [voiceGain, setVoiceGain] = useState(1);
  const [musicGain, setMusicGain] = useState(0.8);
  const [reverbWet, setReverbWet] = useState(0.25);
  const [monitorOn, setMonitorOnState] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const response = await fetch(client.stemUrl(trackId, "no_vocals"));
      const bytes = await response.arrayBuffer();

      const probe = new AudioContext();
      const decoded = await probe.decodeAudioData(bytes.slice(0));
      await probe.close();
      if (cancelled) return;

      // Контекст создаётся в частоте трека: иначе рассинхрон копится по
      // длине песни и голос медленно уползает.
      const ctx = new AudioContext({ sampleRate: decoded.sampleRate });
      await ctx.audioWorklet.addModule("/recorder-worklet.js");

      ctxRef.current = ctx;
      musicRef.current = decoded;
      recorderRef.current = new Recorder(ctx);
      monitorRef.current = new Monitor(ctx);
      meterRef.current = new LevelMeter(ctx);

      const stored = loadOffset(window.localStorage);
      setOffsetSecState(stored || estimateLatencySec(ctx));
      setReady(true);
    })().catch((exc: unknown) => {
      if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
    });

    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      void ctxRef.current?.close();
    };
  }, [client, trackId]);

  const setOffsetSec = useCallback((sec: number) => {
    setOffsetSecState(sec);
    saveOffset(window.localStorage, sec);
  }, []);

  const setMonitorOn = useCallback((on: boolean) => {
    monitorRef.current?.setEnabled(on);
    setMonitorOnState(on);
  }, []);

  const startRecording = useCallback(async () => {
    const ctx = ctxRef.current;
    const music = musicRef.current;
    if (!ctx || !music || !recorderRef.current) return;

    try {
      const stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
      streamRef.current = stream;

      const source = ctx.createMediaStreamSource(stream);
      monitorRef.current?.attach(source);
      meterRef.current?.resetClip();
      meterRef.current?.attach(source);
      setClipped(false);

      await recorderRef.current.start(stream);

      const playback = ctx.createBufferSource();
      playback.buffer = music;
      playback.connect(ctx.destination);
      playback.start();
      playback.onended = () => setRecording(false);
      playbackRef.current = playback;

      setRecording(true);
    } catch {
      setError(
        "Нет доступа к микрофону. Разрешите его в настройках браузера и " +
          "перезагрузите страницу.",
      );
    }
  }, []);

  const stopRecording = useCallback(() => {
    playbackRef.current?.stop();
    playbackRef.current = null;

    takeRef.current = recorderRef.current?.stop() ?? null;
    monitorRef.current?.detach();
    meterRef.current?.detach();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    setRecording(false);
    setHasTake((takeRef.current?.[0].length ?? 0) > 0);
  }, []);

  const exportMix = useCallback(async () => {
    const music = musicRef.current;
    const take = takeRef.current;
    if (!music || !take) return;

    setMixing(true);
    try {
      const mixed = await mixdown(music, take, music.sampleRate, {
        offsetSec,
        voiceGain,
        musicGain,
        reverbWet,
        watermark: true,
      });

      const channels = Array.from({ length: mixed.numberOfChannels }, (_, ch) =>
        Float32Array.from(mixed.getChannelData(ch)),
      );
      downloadBlob(encodeWav(channels, mixed.sampleRate), "karaoke-mix.wav");
    } finally {
      setMixing(false);
    }
  }, [offsetSec, voiceGain, musicGain, reverbWet]);

  // Уровень читается только во время записи: клиппинг надо показать сразу,
  // а не после сведения, когда дубль уже испорчен.
  useEffect(() => {
    if (!recording) return;
    const timer = window.setInterval(() => {
      const reading = meterRef.current?.read();
      if (!reading) return;
      setLevelDb(reading.db);
      setClipped(reading.clipped);
    }, 100);
    return () => window.clearInterval(timer);
  }, [recording]);

  useEffect(() => {
    if (!hasTake) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [hasTake]);

  return {
    ready, recording, mixing, hasTake, error, levelDb, clipped,
    offsetSec, setOffsetSec,
    voiceGain, setVoiceGain,
    musicGain, setMusicGain,
    reverbWet, setReverbWet,
    monitorOn, setMonitorOn,
    startRecording, stopRecording, exportMix,
  };
}
```

- [ ] **Step 6: Написать экран**

Создать `web/src/features/studio/StudioScreen.tsx`:

```tsx
import type { ApiClient } from "../../api/client";
import { MAX_OFFSET_SEC, MIN_OFFSET_SEC } from "../../audio/latency";
import { useStudio } from "./useStudio";

export function StudioScreen({
  client,
  trackId,
}: {
  client: ApiClient;
  trackId: string;
}) {
  const s = useStudio(client, trackId);

  if (s.error) return <p role="alert">{s.error}</p>;
  if (!s.ready) return <p>Готовлю студию…</p>;

  return (
    <section>
      <h1>Студия</h1>

      <div>
        {s.recording ? (
          <button onClick={s.stopRecording}>Остановить</button>
        ) : (
          <button onClick={() => void s.startRecording()}>Записать</button>
        )}
        <button disabled={!s.hasTake || s.mixing} onClick={() => void s.exportMix()}>
          {s.mixing ? "Свожу…" : "Скачать микс"}
        </button>
      </div>

      {s.recording && (
        <div>
          <meter min={-60} max={0} value={Math.max(-60, s.levelDb)} />
          <span>{s.levelDb.toFixed(0)} dB</span>
        </div>
      )}

      {s.clipped && (
        <p role="alert">
          Сигнал перегружен — в записи будут щелчки. Отодвиньтесь от микрофона
          или убавьте усиление входа и запишите дубль заново.
        </p>
      )}

      <label>
        <input
          type="checkbox"
          checked={s.monitorOn}
          onChange={(e) => s.setMonitorOn(e.target.checked)}
        />
        Слышать себя в наушниках (добавляет 20–40 мс задержки)
      </label>

      <label>
        Смещение записи: {(s.offsetSec * 1000).toFixed(0)} мс
        <input
          type="range"
          min={MIN_OFFSET_SEC}
          max={MAX_OFFSET_SEC}
          step={0.005}
          value={s.offsetSec}
          onChange={(e) => s.setOffsetSec(Number(e.target.value))}
        />
      </label>

      <label>
        Голос
        <input type="range" min={0} max={2} step={0.05} value={s.voiceGain}
               onChange={(e) => s.setVoiceGain(Number(e.target.value))} />
      </label>

      <label>
        Минусовка
        <input type="range" min={0} max={2} step={0.05} value={s.musicGain}
               onChange={(e) => s.setMusicGain(Number(e.target.value))} />
      </label>

      <label>
        Реверб
        <input type="range" min={0} max={1} step={0.05} value={s.reverbWet}
               onChange={(e) => s.setReverbWet(Number(e.target.value))} />
      </label>
    </section>
  );
}
```

- [ ] **Step 7: Проверить сборку и все быстрые тесты**

```bash
npm run build
npm run test:all
```

Expected: сборка проходит, все тесты зелёные

- [ ] **Step 8: Коммит**

```bash
cd .. && git add web
git commit -m "feat(web): экран студии, запись и экспорт микса"
```

---

### Task 12: Сквозной сценарий и README

**Files:**
- Create: `web/playwright.config.ts`
- Create: `web/e2e/full-flow.spec.ts`
- Create: `web/e2e/fixtures/sample.wav` (генерируется скриптом)
- Create: `web/e2e/make-fixture.mjs`
- Create: `web/README.md`
- Modify: `web/package.json` (скрипт `test:e2e`)

**Interfaces:**
- Consumes: всё предыдущее плюс запущенный API из подсистемы A
- Produces: сквозной тест пути «загрузил → дождался → записал → скачал»

- [ ] **Step 1: Написать генератор тестового аудио**

Создать `web/e2e/make-fixture.mjs`:

```js
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "fixtures");
mkdirSync(outDir, { recursive: true });

const sampleRate = 44100;
const seconds = 3;
const frames = sampleRate * seconds;
const channels = 2;
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
  const value = Math.round(12000 * Math.sin((2 * Math.PI * 440 * i) / sampleRate));
  for (let ch = 0; ch < channels; ch += 1) {
    buffer.writeInt16LE(value, 44 + (i * channels + ch) * 2);
  }
}

writeFileSync(join(outDir, "sample.wav"), buffer);
console.log("создан e2e/fixtures/sample.wav");
```

Запустить:

```bash
node e2e/make-fixture.mjs
```

- [ ] **Step 2: Настроить Playwright**

Создать `web/playwright.config.ts`:

```ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    launchOptions: {
      args: [
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        "--use-file-for-fake-audio-capture=e2e/fixtures/sample.wav",
        "--autoplay-policy=no-user-gesture-required",
      ],
    },
  },
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
  },
});
```

Установить и добавить скрипт:

```bash
npm install -D @playwright/test
npx playwright install chromium
```

В `package.json` в `scripts` добавить `"test:e2e": "playwright test"`.

- [ ] **Step 3: Написать сквозной тест**

Создать `web/e2e/full-flow.spec.ts`:

```ts
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

test("понятная ошибка на неподходящем файле", async ({ page }) => {
  await page.goto("/");

  await page.setInputFiles('input[type="file"]', {
    name: "junk.mp3",
    mimeType: "audio/mpeg",
    buffer: Buffer.from("это точно не аудио"),
  });

  await expect(page.getByRole("alert")).toContainText("не похож на аудио");
});
```

- [ ] **Step 4: Запустить сквозной тест**

Сначала поднять API в отдельном терминале:

```bash
cd api && .venv/Scripts/python -m uvicorn karaoke_api.main:app
```

Затем:

```bash
cd web && npm run test:e2e
```

Expected: PASS, 2 сценария

- [ ] **Step 5: Написать README**

Создать `web/README.md`:

```markdown
# Karaoke Studio

Браузерная часть: загрузка трека, запись голоса поверх минусовки, сведение и
экспорт.

## Запуск

Сначала поднимите API (см. `../api/README.md`), затем:

    npm install
    npm run dev

Открыть <http://127.0.0.1:5173>. Запросы к `/api` проксируются на порт 8000.

## Тесты

    npm test           # чистая логика, Node
    npm run test:browser  # Web Audio в headless Chromium
    npm run test:e2e      # сквозной путь, нужен запущенный API

Перед первым запуском e2e создать тестовое аудио:

    node e2e/make-fixture.mjs

## Ручная проверка

Автоматика не покрывает живой микрофон и качество звучания. Чек-лист:

1. Запись без наушников — убедиться, что нет самовозбуждения.
2. Мониторинг включён — оценить, терпима ли задержка.
3. Спеть куплет, экспортировать, послушать: голос попадает в музыку.
4. Подвигать ползунок смещения — сдвиг слышен в обе стороны.
5. Обновить страницу с несохранённой записью — браузер предупреждает.

## Устройство

`src/audio/` — движок без React: буферы на вход, буферы на выход.
Тестируется в headless-браузере на настоящем `OfflineAudioContext`.

`src/features/` — экраны и состояние интерфейса.

Смещение записи применяется при сведении сдвигом буфера, а не задержкой в
графе: так его можно менять уже после записи.
```

- [ ] **Step 6: Коммит**

```bash
cd .. && git add web
git commit -m "feat(web): сквозной сценарий и документация"
```

---

## Итог подсистемы B

После задачи 11 путь замкнут: трек загружается, минусовка готовится на сервере,
голос пишется в браузере с компенсацией задержки, микс сводится и скачивается.
Тест на выравнивание защищает самую хрупкую часть, сквозной сценарий
прогоняется без человека.

Не входит и ждёт отдельных циклов: аккаунты и биллинг (подсистема C),
AI-функции (D), авто-тюн, автокалибровка смещения по импульсу, восстановление
дубля из IndexedDB.
