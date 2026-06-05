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

export type StreamEvent = LogEvent | FinalEvent;
