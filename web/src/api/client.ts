import type { JobState, StemKind, UploadResult } from "./types";

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
