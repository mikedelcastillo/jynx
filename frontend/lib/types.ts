export interface Option {
  label: string;
  value: string;
}

export interface Question {
  question: string;
  options: Option[];
  answer: string;
}

export interface QuizData {
  questions: Question[];
}

export interface QuizResult {
  status: "ok" | "fail";
  message: string;
  data: QuizData;
}

export type LogLevel = "info" | "warn" | "error" | "success";

export interface LogEvent {
  type: "log";
  level: LogLevel;
  message: string;
  data?: Record<string, unknown>;
}

export interface FinalEvent {
  type: "final";
  data: QuizResult;
}

// ---------- live pipeline events ----------
// One map task's lifecycle (a single per-chunk LLM call).
export type ChunkLifecycle =
  | "queued"
  | "running"
  | "retrying"
  | "done"
  | "failed"
  | "skipped";

export interface ChunkEvent {
  type: "chunk";
  id: number;
  source: string;
  state: ChunkLifecycle;
  chars?: number; // running: cumulative streamed chars
  count?: number; // done: questions produced
  attempt?: number; // retrying: attempt number
  error?: string; // failed
}

export type PipelinePhase =
  | "fetch"
  | "chunk"
  | "map"
  | "reduce"
  | "crawl"
  | "done";

export interface ProgressEvent {
  type: "progress";
  phase: PipelinePhase;
  // fetch
  source?: string;
  state?: string; // "fetching" | "ready" | "blocked"
  // map (aggregate snapshot)
  total?: number;
  done?: number;
  running?: number;
  failed?: number;
  questions?: number;
  // reduce
  step?: string; // "dedupe" | "selection" | "fallback"
  removed?: number;
  remaining?: number;
  kept?: number;
  // crawl
  depth?: number;
  max_depth?: number;
  fetched?: number;
  cap?: number;
}

export type StreamEvent = LogEvent | FinalEvent | ChunkEvent | ProgressEvent;

// ---------- view models (built from the event stream in page.tsx) ----------
export type SourceFetchState = "fetching" | "ready" | "blocked";

export interface SourceState {
  source: string;
  state: SourceFetchState;
}

export interface ChunkState {
  id: number;
  source: string;
  state: ChunkLifecycle;
  chars: number;
  count: number;
  attempt: number;
  error?: string;
}

export interface PipelineState {
  phase: PipelinePhase;
  total: number;
  done: number;
  running: number;
  failed: number;
  questions: number;
  reduceStep?: string;
}
