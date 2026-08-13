export type JobStatus = "queued" | "running" | "done" | "failed";
export type Stage = "loading" | "separating" | "writing";
export type StemKind = "vocals" | "no_vocals";

export interface JobResult {
  stems: Record<StemKind, string>;
  degraded: boolean;
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

export interface Me {
  email: string;
  plan: "free" | "pro";
  operations_used: number;
  operations_limit: number;
}
