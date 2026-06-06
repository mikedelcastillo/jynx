"use client";

import React, { useEffect, useMemo } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Handle,
  Position,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type {
  SourceState,
  ChunkState,
  PipelineState,
} from "@/lib/types";

interface PipelineGraphProps {
  sources: SourceState[]; // fetch-phase source roster, in arrival order
  chunks: Record<number, ChunkState>; // keyed by chunk id
  pipeline: PipelineState | null; // null before fetch starts
}

// ---------- layout ----------
const Rs = 150; // inner ring radius (sources)
const Rc = 280; // outer ring radius (chunks)
const CORE_POS = { x: 0, y: 0 };
const FINAL_POS = { x: 0, y: 110 };

interface Positions {
  sources: Record<string, { x: number; y: number }>;
  chunks: Record<number, { x: number; y: number }>;
}

// Pure helper: compute stable node positions from the source/chunk roster.
function computeLayout(
  sourceLabels: string[],
  chunkPairs: { id: number; source: string }[]
): Positions {
  const sources: Positions["sources"] = {};
  const chunks: Positions["chunks"] = {};

  const n = sourceLabels.length;
  const sourceAngle: Record<string, number> = {};

  sourceLabels.forEach((label, i) => {
    const angle = (2 * Math.PI * i) / Math.max(1, n) - Math.PI / 2;
    sourceAngle[label] = angle;
    sources[label] = { x: Rs * Math.cos(angle), y: Rs * Math.sin(angle) };
  });

  // Group chunks by source string (even if the source isn't in the roster yet).
  const bySource: Record<string, number[]> = {};
  for (const { id, source } of chunkPairs) {
    (bySource[source] ??= []).push(id);
  }

  // Assign a fallback angle to sources that have chunks but aren't in the roster,
  // distributing them after the known sources so they don't overlap.
  const extraSources = Object.keys(bySource).filter(
    (s) => !(s in sourceAngle)
  );
  extraSources.forEach((label, i) => {
    const idx = n + i;
    const total = n + extraSources.length;
    const angle = (2 * Math.PI * idx) / Math.max(1, total) - Math.PI / 2;
    sourceAngle[label] = angle;
  });

  const totalSrc = Math.max(1, Object.keys(bySource).length);
  const sector = (2 * Math.PI) / totalSrc;
  for (const source of Object.keys(bySource)) {
    const ids = bySource[source].slice().sort((a, b) => a - b);
    const m = ids.length;
    const theta = sourceAngle[source] ?? -Math.PI / 2;
    // Spread chunks wide enough to avoid overlap at the tighter ring, but not
    // past ~80% of this source's angular sector so neighbours don't collide.
    const spread = Math.min(0.34 * Math.max(0, m - 1), sector * 0.8);
    ids.forEach((id, i) => {
      // Spread chunks across [theta - spread/2, theta + spread/2].
      const offset = m === 1 ? 0 : (i / (m - 1) - 0.5) * spread;
      const angle = theta + offset;
      chunks[id] = { x: Rc * Math.cos(angle), y: Rc * Math.sin(angle) };
    });
  }

  return { sources, chunks };
}

// ---------- custom node components ----------
interface CoreData {
  pipeline: PipelineState | null;
}

const PHASE_LABELS: Record<string, string> = {
  fetch: "Fetching",
  chunk: "Chunking",
  map: "Generating",
  reduce: "Reducing",
  done: "Done",
};

const CoreNode = React.memo(function CoreNode({ data }: { data: CoreData }) {
  const p = data.pipeline;
  const phase = p?.phase ?? "fetch";
  const label = PHASE_LABELS[phase] ?? "Fetching";
  return (
    <div className="pf-node pf-core">
      <Handle type="target" position={Position.Top} />
      <div className="pf-core-label">{label}</div>
      {p && phase === "map" && (
        <div className="pf-core-stats">
          <span>
            {p.done}/{p.total}
          </span>
          <span>{p.running} running</span>
          <span>{p.questions} questions</span>
        </div>
      )}
      {p && phase === "reduce" && p.reduceStep && (
        <div className="pf-core-stats">
          <span>{p.reduceStep}</span>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
});

function shortenSource(source: string): string {
  if (!source) return "source";
  if (source === "pasted text" || source.toLowerCase() === "text") {
    return "pasted text";
  }
  try {
    const url = new URL(source);
    const segments = url.pathname.split("/").filter(Boolean);
    if (segments.length > 0) return segments[segments.length - 1];
    return url.hostname;
  } catch {
    const parts = source.split("/").filter(Boolean);
    return parts.length > 0 ? parts[parts.length - 1] : source;
  }
}

const SourceNode = React.memo(function SourceNode({
  data,
}: {
  data: { source: SourceState };
}) {
  const { source } = data;
  return (
    <div className={`pf-node pf-source is-${source.state}`}>
      <Handle type="target" position={Position.Top} />
      <span className="pf-source-label">{shortenSource(source.source)}</span>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
});

function formatChars(n: number): string {
  return n.toLocaleString("en-US");
}

const ChunkNode = React.memo(function ChunkNode({
  data,
}: {
  data: { chunk: ChunkState };
}) {
  const { chunk } = data;
  let content: React.ReactNode = null;
  switch (chunk.state) {
    case "running":
      // Reasoning models stream no content while "thinking", so chars sits at
      // 0 for a while — show "working…" instead of a frozen-looking 0.
      content =
        chunk.chars > 0 ? (
          formatChars(chunk.chars)
        ) : (
          <span className="pf-chunk-working">working…</span>
        );
      break;
    case "retrying":
      content = `⟳${chunk.attempt}`;
      break;
    case "done":
      content = (
        <>
          <span className="pf-chunk-check">✓</span>
          <span className="pf-chunk-count">+{chunk.count}</span>
        </>
      );
      break;
    case "failed":
      content = "✗";
      break;
    case "skipped":
      content = "—";
      break;
    default:
      content = "";
  }
  return (
    <div className={`pf-node pf-chunk is-${chunk.state}`}>
      <Handle type="target" position={Position.Top} />
      <div className="pf-chunk-body">{content}</div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
});

const FinalNode = React.memo(function FinalNode({
  data,
}: {
  data: { questions?: number };
}) {
  return (
    <div className="pf-node pf-final">
      <Handle type="target" position={Position.Top} />
      <span className="pf-final-star">★</span>
      <span className="pf-final-label">Quiz ready</span>
      {typeof data.questions === "number" && (
        <span className="pf-final-count">{data.questions}</span>
      )}
    </div>
  );
});

// Registered ONCE at module scope so it isn't re-created each render.
const nodeTypes = {
  core: CoreNode,
  source: SourceNode,
  chunk: ChunkNode,
  final: FinalNode,
};

export default function PipelineGraph({
  sources,
  chunks,
  pipeline,
}: PipelineGraphProps) {
  const chunkList = Object.values(chunks);

  // Memoize the layout on roster identity (sorted source labels + sorted chunk
  // ids) so positions stay stable across data-only updates.
  const sourceLabels = sources.map((s) => s.source);
  const sortedSourceKey = sourceLabels.slice().sort().join("|");
  const sortedChunkKey = chunkList
    .map((c) => c.id)
    .slice()
    .sort((a, b) => a - b)
    .join("|");

  const positions = useMemo(
    () =>
      computeLayout(
        sourceLabels,
        chunkList.map((c) => ({ id: c.id, source: c.source }))
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sortedSourceKey, sortedChunkKey]
  );

  const phase = pipeline?.phase;

  const nodes = useMemo(() => {
    const result: {
      id: string;
      type: string;
      position: { x: number; y: number };
      data: Record<string, unknown>;
    }[] = [];

    result.push({
      id: "core",
      type: "core",
      position: CORE_POS,
      data: { pipeline },
    });

    for (const s of sources) {
      const pos = positions.sources[s.source] ?? CORE_POS;
      result.push({
        id: `src:${s.source}`,
        type: "source",
        position: pos,
        data: { source: s },
      });
    }

    for (const c of chunkList) {
      const pos = positions.chunks[c.id] ?? CORE_POS;
      result.push({
        id: `chunk:${c.id}`,
        type: "chunk",
        position: pos,
        data: { chunk: c },
      });
    }

    if (phase === "done") {
      result.push({
        id: "final",
        type: "final",
        position: FINAL_POS,
        data: { questions: pipeline?.questions },
      });
    }

    return result;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources, chunks, pipeline, positions]);

  const edges = useMemo(() => {
    const result: {
      id: string;
      source: string;
      target: string;
      animated?: boolean;
    }[] = [];

    // core -> each source
    for (const s of sources) {
      result.push({
        id: `e:core:src:${s.source}`,
        source: "core",
        target: `src:${s.source}`,
        animated: s.state === "fetching",
      });
    }

    // source -> each of its chunks (branching), and chunk -> core when done.
    const reducing = phase === "reduce";
    for (const c of chunkList) {
      // Only draw the source->chunk edge if the source node exists; otherwise
      // the edge would dangle. Fall back to core->chunk when source missing.
      const srcId = `src:${c.source}`;
      const hasSource = sources.some((s) => s.source === c.source);
      result.push({
        id: `e:src:chunk:${c.id}`,
        source: hasSource ? srcId : "core",
        target: `chunk:${c.id}`,
        animated: c.state === "running" || c.state === "retrying",
      });

      if (c.state === "done") {
        result.push({
          id: `e:chunk:core:done:${c.id}`,
          source: `chunk:${c.id}`,
          target: "core",
          animated: reducing,
        });
      }
    }

    if (phase === "done") {
      result.push({
        id: "e:core:final",
        source: "core",
        target: "final",
        animated: true,
      });
    }

    return result;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources, chunks, pipeline]);

  return (
    <ReactFlowProvider>
      <div className="pf-graph">
        <FlowCanvas nodes={nodes} edges={edges} />
      </div>
    </ReactFlowProvider>
  );
}

// Inner canvas: re-fits the viewport whenever the graph grows (nodes stream in
// over time, so a one-shot `fitView` would leave new nodes off-screen).
function FlowCanvas({
  nodes,
  edges,
}: {
  nodes: { id: string; type: string; position: { x: number; y: number }; data: Record<string, unknown> }[];
  edges: { id: string; source: string; target: string; animated?: boolean }[];
}) {
  const { fitView } = useReactFlow();
  const nodeCount = nodes.length;

  useEffect(() => {
    // Defer to the next frame so React Flow has measured the new nodes.
    const t = setTimeout(() => {
      fitView({ padding: 0.2, duration: 300 });
    }, 50);
    return () => clearTimeout(t);
  }, [nodeCount, fitView]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      fitViewOptions={{ padding: 0.2 }}
      minZoom={0.1}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      proOptions={{ hideAttribution: true }}
    >
      <Background />
    </ReactFlow>
  );
}
