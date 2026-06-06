"use client";

import React, { useEffect } from "react";
import {
  ReactFlow,
  Background,
  Handle,
  Position,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { SourceState, ChunkState, PipelineState } from "@/lib/types";

// ---------- shared geometry types ----------
export interface XY {
  x: number;
  y: number;
}

export interface RFNode {
  id: string;
  type: string;
  position: XY;
  data: Record<string, unknown>;
}

export interface RFEdge {
  id: string;
  source: string;
  target: string;
  animated?: boolean;
}

// ---------- labels ----------
export const PHASE_LABELS: Record<string, string> = {
  fetch: "Fetching",
  chunk: "Chunking",
  map: "Generating",
  reduce: "Reducing",
  done: "Done",
};

export function shortenSource(source: string): string {
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

function formatChars(n: number): string {
  return n.toLocaleString("en-US");
}

// ---------- custom node components ----------
// Handles are placed on all four sides so the same node renders cleanly whether
// edges flow top→bottom (radial/tree) or left→right (layered).
interface CoreData {
  pipeline: PipelineState | null;
}

const CoreNode = React.memo(function CoreNode({ data }: { data: CoreData }) {
  const p = data.pipeline;
  const phase = p?.phase ?? "fetch";
  const label = PHASE_LABELS[phase] ?? "Fetching";
  return (
    <div className="pf-node pf-core">
      <Handle type="target" position={Position.Top} />
      <Handle type="target" position={Position.Left} id="l" />
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
      <Handle type="source" position={Position.Right} id="r" />
    </div>
  );
});

const SourceNode = React.memo(function SourceNode({
  data,
}: {
  data: { source: SourceState };
}) {
  const { source } = data;
  return (
    <div className={`pf-node pf-source is-${source.state}`}>
      <Handle type="target" position={Position.Top} />
      <Handle type="target" position={Position.Left} id="l" />
      <span className="pf-source-label">{shortenSource(source.source)}</span>
      <Handle type="source" position={Position.Bottom} />
      <Handle type="source" position={Position.Right} id="r" />
    </div>
  );
});

const ChunkNode = React.memo(function ChunkNode({
  data,
}: {
  data: { chunk: ChunkState };
}) {
  const { chunk } = data;
  let content: React.ReactNode = null;
  switch (chunk.state) {
    case "running":
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
      <Handle type="target" position={Position.Left} id="l" />
      <div className="pf-chunk-body">{content}</div>
      <Handle type="source" position={Position.Bottom} />
      <Handle type="source" position={Position.Right} id="r" />
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
      <Handle type="target" position={Position.Left} id="l" />
      <span className="pf-final-star">★</span>
      <span className="pf-final-label">Quiz ready</span>
      {typeof data.questions === "number" && (
        <span className="pf-final-count">{data.questions}</span>
      )}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
});

// Registered ONCE at module scope so it isn't re-created each render.
export const nodeTypes = {
  core: CoreNode,
  source: SourceNode,
  chunk: ChunkNode,
  final: FinalNode,
};

// ---------- core-centric node/edge builders ----------
// Shared by the radial, tree, and force layouts (core in the middle/top; edges
// fan core → source → chunk, chunk → core on completion, core → final).
export function buildCoreNodes(
  sources: SourceState[],
  chunkList: ChunkState[],
  pipeline: PipelineState | null,
  positions: { core: XY; sources: Record<string, XY>; chunks: Record<number, XY>; final: XY }
): RFNode[] {
  const result: RFNode[] = [];

  result.push({
    id: "core",
    type: "core",
    position: positions.core,
    data: { pipeline },
  });

  for (const s of sources) {
    result.push({
      id: `src:${s.source}`,
      type: "source",
      position: positions.sources[s.source] ?? positions.core,
      data: { source: s },
    });
  }

  for (const c of chunkList) {
    result.push({
      id: `chunk:${c.id}`,
      type: "chunk",
      position: positions.chunks[c.id] ?? positions.core,
      data: { chunk: c },
    });
  }

  if (pipeline?.phase === "done") {
    result.push({
      id: "final",
      type: "final",
      position: positions.final,
      data: { questions: pipeline?.questions },
    });
  }

  return result;
}

export function buildCoreEdges(
  sources: SourceState[],
  chunkList: ChunkState[],
  pipeline: PipelineState | null
): RFEdge[] {
  const result: RFEdge[] = [];
  const phase = pipeline?.phase;
  const reducing = phase === "reduce";

  for (const s of sources) {
    result.push({
      id: `e:core:src:${s.source}`,
      source: "core",
      target: `src:${s.source}`,
      animated: s.state === "fetching",
    });
  }

  for (const c of chunkList) {
    const hasSource = sources.some((s) => s.source === c.source);
    result.push({
      id: `e:src:chunk:${c.id}`,
      source: hasSource ? `src:${c.source}` : "core",
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
}

// ---------- shared canvas ----------
// Re-fits the viewport whenever the graph grows (nodes stream in over time, so a
// one-shot fitView would leave new nodes off-screen). `fitKey` lets callers force
// a re-fit on data changes that don't change the node count (e.g. force ticks).
export function FlowCanvas({
  nodes,
  edges,
  fitKey,
}: {
  nodes: RFNode[];
  edges: RFEdge[];
  fitKey?: string | number;
}) {
  const { fitView } = useReactFlow();
  const key = fitKey ?? nodes.length;

  useEffect(() => {
    const t = setTimeout(() => {
      fitView({ padding: 0.2, duration: 300 });
    }, 50);
    return () => clearTimeout(t);
  }, [key, fitView]);

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
