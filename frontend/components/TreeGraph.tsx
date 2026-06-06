"use client";

import React, { useMemo } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import type { SourceState, ChunkState, PipelineState } from "@/lib/types";
import {
  FlowCanvas,
  buildCoreNodes,
  buildCoreEdges,
  type XY,
} from "./pipeline/shared";

interface Props {
  sources: SourceState[];
  chunks: Record<number, ChunkState>;
  pipeline: PipelineState | null;
}

// Vertical hierarchy: core at top → sources → chunks → quiz at the bottom.
const GAP = 92; // horizontal spacing between leaf columns
const Y_CORE = 0;
const Y_SRC = 150;
const Y_CHUNK = 320;
const Y_FINAL = 480;

function computeTreeLayout(
  roster: string[],
  chunkPairs: { id: number; source: string }[]
): { core: XY; sources: Record<string, XY>; chunks: Record<number, XY>; final: XY } {
  const bySource: Record<string, number[]> = {};
  for (const { id, source } of chunkPairs) {
    (bySource[source] ??= []).push(id);
  }
  // Sources in roster order, then any sources that have chunks but aren't yet
  // in the roster (keeps positions defined for every node we render).
  const ordered = [
    ...roster,
    ...Object.keys(bySource).filter((s) => !roster.includes(s)),
  ];

  const chunkX: Record<number, number> = {};
  const srcX: Record<string, number> = {};
  let slot = 0;
  for (const src of ordered) {
    const ids = (bySource[src] ?? []).slice().sort((a, b) => a - b);
    if (ids.length === 0) {
      srcX[src] = slot * GAP;
      slot += 1;
    } else {
      const xs: number[] = [];
      for (const id of ids) {
        chunkX[id] = slot * GAP;
        xs.push(slot * GAP);
        slot += 1;
      }
      srcX[src] = xs.reduce((a, b) => a + b, 0) / xs.length;
    }
  }

  // Center the whole tree on x=0 so the core/final sit above/below the middle.
  const allX = [...Object.values(chunkX), ...Object.values(srcX)];
  const center = allX.length
    ? (Math.min(...allX) + Math.max(...allX)) / 2
    : 0;

  const sources: Record<string, XY> = {};
  for (const s of Object.keys(srcX)) {
    sources[s] = { x: srcX[s] - center, y: Y_SRC };
  }
  const chunks: Record<number, XY> = {};
  for (const id of Object.keys(chunkX).map(Number)) {
    chunks[id] = { x: chunkX[id] - center, y: Y_CHUNK };
  }

  return {
    core: { x: 0, y: Y_CORE },
    final: { x: 0, y: Y_FINAL },
    sources,
    chunks,
  };
}

export default function TreeGraph({ sources, chunks, pipeline }: Props) {
  const chunkList = Object.values(chunks);
  const rosterKey = sources.map((s) => s.source).join("|");
  const chunkKey = chunkList
    .map((c) => `${c.id}:${c.source}`)
    .sort()
    .join("|");

  const layout = useMemo(
    () =>
      computeTreeLayout(
        sources.map((s) => s.source),
        chunkList.map((c) => ({ id: c.id, source: c.source }))
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rosterKey, chunkKey]
  );

  const nodes = useMemo(
    () => buildCoreNodes(sources, chunkList, pipeline, layout),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sources, chunks, pipeline, layout]
  );
  const edges = useMemo(
    () => buildCoreEdges(sources, chunkList, pipeline),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sources, chunks, pipeline]
  );

  return (
    <ReactFlowProvider>
      <div className="pf-graph">
        <FlowCanvas nodes={nodes} edges={edges} />
      </div>
    </ReactFlowProvider>
  );
}
