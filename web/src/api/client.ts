import type { JobState, Me, StemKind, UploadResult } from "./types";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, status: number) {
    super(`${code} (HTTP ${status})`);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
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
  private readonly baseUrl: string;

  constructor(baseUrl: string = "") {
    this.baseUrl = baseUrl;
  }

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

  /** Текущий пользователь или `null`, если сессии нет. */
  async me(): Promise<Me | null> {
    const response = await fetch(`${this.baseUrl}/api/me`);
    if (response.status === 401) return null;
    return parseOrThrow<Me>(response);
  }

  /** Просит письмо со ссылкой входа. Ответ одинаков для любого адреса. */
  async requestLogin(email: string): Promise<void> {
    const response = await fetch(`${this.baseUrl}/api/auth/request`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new ApiError(
        (body as { error?: string }).error ?? "unknown_error",
        response.status,
      );
    }
  }

  async logout(): Promise<void> {
    await fetch(`${this.baseUrl}/api/auth/logout`, { method: "POST" });
  }

  stemUrl(trackId: string, kind: StemKind): string {
    return `${this.baseUrl}/api/tracks/${trackId}/stems/${kind}`;
  }

  /**
   * Дорожка байтами. Ответ проверяется до раскодирования: сервер на пропавшую
   * дорожку отдаёт JSON с ошибкой, и без проверки этот JSON уехал бы в
   * `decodeAudioData`, а человек увидел бы «Unable to decode audio data»
   * вместо «трек удалён, загрузите заново».
   */
  async fetchStem(trackId: string, kind: StemKind): Promise<ArrayBuffer> {
    const response = await fetch(this.stemUrl(trackId, kind));
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const code = (body as { error?: string }).error ?? "stem_unavailable";
      throw new ApiError(code, response.status);
    }
    return response.arrayBuffer();
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
