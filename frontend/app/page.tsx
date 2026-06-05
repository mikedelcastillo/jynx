"use client";

import { useRef, useState } from "react";
import UrlInput from "@/components/UrlInput";
import LogConsole from "@/components/LogConsole";
import Quiz from "@/components/Quiz";
import RawJsonPanel from "@/components/RawJsonPanel";
import { streamQuiz } from "@/lib/stream";
import type { LogEvent, QuizResult } from "@/lib/types";

type View = "input" | "loading" | "result";

export default function Page() {
  const [view, setView] = useState<View>("input");
  const [urls, setUrls] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [result, setResult] = useState<QuizResult | null>(null);
  const [showRaw, setShowRaw] = useState(false);

  const abortRef = useRef<AbortController | null>(null);

  const canSubmit = urls.length > 0 || text.trim().length > 0;

  async function run(runUrls: string[], runText: string) {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLogs([]);
    setResult(null);
    setShowRaw(false);
    setView("loading");

    try {
      for await (const event of streamQuiz(
        { urls: runUrls, text: runText },
        controller.signal
      )) {
        if (event.type === "log") {
          setLogs((prev) => [...prev, event]);
        } else if (event.type === "final") {
          setResult(event.data);
          setView("result");
        }
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      setLogs((prev) => [
        ...prev,
        {
          type: "log",
          level: "error",
          message: `Unexpected error: ${
            err instanceof Error ? err.message : String(err)
          }`,
        },
      ]);
    }

    // If the stream ended without a final event, surface that.
    if (!controller.signal.aborted) {
      setView((v) => {
        if (v === "loading") {
          setLogs((prev) => [
            ...prev,
            {
              type: "log",
              level: "error",
              message: "Stream ended without a final result.",
            },
          ]);
        }
        return v;
      });
    }
  }

  function handleSubmit() {
    if (!canSubmit) return;
    run(urls, text);
  }

  function handleRetry() {
    run(urls, text);
  }

  function handleClose() {
    abortRef.current?.abort();
    setView("input");
  }

  if (view === "input") {
    return (
      <div className="view-input">
        <h1 className="title">Jynx</h1>
        <p className="subtitle">
          Turn a webpage or pasted text into a quiz.
        </p>

        <label className="field-label">URLs</label>
        <UrlInput urls={urls} onChange={setUrls} />

        <label className="field-label">Text</label>
        <textarea
          className="text-area"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste extra text, context, or instructions..."
          rows={6}
        />

        <button
          type="button"
          className="primary-btn"
          onClick={handleSubmit}
          disabled={!canSubmit}
        >
          Submit
        </button>
      </div>
    );
  }

  if (view === "loading") {
    return (
      <div className="view-loading">
        <h2 className="title">Quizzifying...</h2>
        <LogConsole logs={logs} />
        <div className="button-row">
          <button type="button" onClick={handleClose}>
            Close
          </button>
        </div>
      </div>
    );
  }

  // result view
  const status = result?.status;
  return (
    <div className="view-result">
      <div className="button-row">
        <button type="button" onClick={handleRetry}>
          Retry
        </button>
        <button type="button" onClick={handleClose}>
          Close
        </button>
        <button type="button" onClick={() => setShowRaw((s) => !s)}>
          View sample results
        </button>
      </div>

      {status === "fail" && (
        <div className="fail-banner">
          <strong>Generation failed.</strong>
          <div>{result?.message}</div>
        </div>
      )}

      {result && <Quiz questions={result.data.questions} />}

      {result && (
        <RawJsonPanel
          result={result}
          open={showRaw}
          onClose={() => setShowRaw(false)}
        />
      )}
    </div>
  );
}
