"use client";

import { useRef, useState } from "react";
import UrlInput from "@/components/UrlInput";
import PipelineGraph from "@/components/PipelineGraph";
import LayeredGraph from "@/components/LayeredGraph";
import TreeGraph from "@/components/TreeGraph";
import LogDrawer from "@/components/LogDrawer";
import Quiz from "@/components/Quiz";
import RawJsonPanel from "@/components/RawJsonPanel";
import { streamQuiz } from "@/lib/stream";
import type {
  LogEvent,
  QuizResult,
  SourceState,
  SourceFetchState,
  ChunkState,
  PipelineState,
} from "@/lib/types";

type View = "input" | "loading" | "result";
type VizTab = "graph" | "layered" | "tree";

export default function Page() {
  const [view, setView] = useState<View>("input");
  const [urls, setUrls] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [count, setCount] = useState(10);
  const [depth, setDepth] = useState(1);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [result, setResult] = useState<QuizResult | null>(null);
  const [showRaw, setShowRaw] = useState(false);
  const [sources, setSources] = useState<SourceState[]>([]);
  const [chunks, setChunks] = useState<Record<number, ChunkState>>({});
  const [pipeline, setPipeline] = useState<PipelineState | null>(null);
  const [vizTab, setVizTab] = useState<VizTab>("graph");

  const abortRef = useRef<AbortController | null>(null);

  const canSubmit = urls.length > 0 || text.trim().length > 0;

  async function run(runUrls: string[], runText: string) {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLogs([]);
    setResult(null);
    setShowRaw(false);
    setSources([]);
    setChunks({});
    setPipeline(null);
    setView("loading");

    try {
      for await (const event of streamQuiz(
        { urls: runUrls, text: runText, num_questions: count, crawl_depth: depth },
        controller.signal
      )) {
        if (event.type === "log") {
          setLogs((prev) => [...prev, event]);
        } else if (event.type === "final") {
          setResult(event.data);
          setView("result");
        } else if (event.type === "progress") {
          if (event.phase === "fetch" && event.source) {
            setSources((prev) => {
              const next = prev.filter((s) => s.source !== event.source);
              return [
                ...next,
                {
                  source: event.source!,
                  state: (event.state as SourceFetchState) ?? "fetching",
                },
              ];
            });
          } else {
            setPipeline((prev) => ({
              phase: event.phase,
              total: event.total ?? prev?.total ?? 0,
              done: event.done ?? prev?.done ?? 0,
              running: event.running ?? prev?.running ?? 0,
              failed: event.failed ?? prev?.failed ?? 0,
              questions: event.questions ?? prev?.questions ?? 0,
              reduceStep:
                event.phase === "reduce" ? event.step : prev?.reduceStep,
            }));
          }
        } else if (event.type === "chunk") {
          setChunks((prev) => ({
            ...prev,
            [event.id]: {
              id: event.id,
              source: event.source,
              state: event.state,
              chars: event.chars ?? prev[event.id]?.chars ?? 0,
              count: event.count ?? prev[event.id]?.count ?? 0,
              attempt: event.attempt ?? prev[event.id]?.attempt ?? 0,
              error: event.error ?? prev[event.id]?.error,
            },
          }));
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

        <label className="field-label">Number of questions</label>
        <input
          type="number"
          className="text-area"
          value={count}
          min={1}
          max={50}
          onChange={(e) => {
            const n = parseInt(e.target.value, 10);
            setCount(Number.isNaN(n) ? 10 : Math.max(1, Math.min(50, n)));
          }}
        />

        <label className="field-label">Link crawl depth</label>
        <select
          className="select"
          value={depth}
          onChange={(e) => setDepth(parseInt(e.target.value, 10))}
        >
          {[0, 1, 2, 3, 4, 5].map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <p className="field-hint">
          Follow relevant links found on the page, this many levels deep. 0 =
          don&apos;t follow links.
        </p>

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
    const tabs: { id: VizTab; label: string }[] = [
      { id: "graph", label: "Radial" },
      { id: "layered", label: "Layered" },
      { id: "tree", label: "Tree" },
    ];
    return (
      <div className="view-loading">
        <div className="viz-tabs" role="tablist">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={vizTab === t.id}
              className={`viz-tab${vizTab === t.id ? " is-active" : ""}`}
              onClick={() => setVizTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="loading-graph">
          {vizTab === "graph" && (
            <PipelineGraph
              sources={sources}
              chunks={chunks}
              pipeline={pipeline}
            />
          )}
          {vizTab === "layered" && (
            <LayeredGraph sources={sources} chunks={chunks} pipeline={pipeline} />
          )}
          {vizTab === "tree" && (
            <TreeGraph sources={sources} chunks={chunks} pipeline={pipeline} />
          )}
        </div>
        <div className="loading-toolbar">
          <button type="button" onClick={handleClose}>
            Close
          </button>
        </div>
        <LogDrawer logs={logs} />
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
