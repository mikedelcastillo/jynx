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

interface PipelineGraphProps {
  sources: SourceState[]; // fetch-phase source roster, in arrival order
  chunks: Record<number, ChunkState>; // keyed by chunk id
  pipeline: PipelineState | null; // null before fetch starts
}

// ---------- radial layout ----------
const Rs = 150; // inner ring radius (sources)
const Rc = 280; // outer ring radius (chunks)
const CORE_POS: XY = { x: 0, y: 0 };
const FINAL_POS: XY = { x: 0, y: 110 };

// Pure helper: compute stable radial positions from the source/chunk roster.
function computeRadialLayout(
  sourceLabels: string[],
  chunkPairs: { id: number; source: string }[]
): { sources: Record<string, XY>; chunks: Record<number, XY> } {
  const sources: Record<string, XY> = {};
  const chunks: Record<number, XY> = {};

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

  // Assign fallback angles to sources that have chunks but aren't in the roster.
  const extraSources = Object.keys(bySource).filter((s) => !(s in sourceAngle));
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
    const spread = Math.min(0.34 * Math.max(0, m - 1), sector * 0.8);
    ids.forEach((id, i) => {
      const offset = m === 1 ? 0 : (i / (m - 1) - 0.5) * spread;
      const angle = theta + offset;
      chunks[id] = { x: Rc * Math.cos(angle), y: Rc * Math.sin(angle) };
    });
  }

  return { sources, chunks };
}

export default function PipelineGraph({
  sources,
  chunks,
  pipeline,
}: PipelineGraphProps) {
  const chunkList = Object.values(chunks);

  // Memoize layout on roster identity so positions stay stable across data-only
  // updates (sorted source labels + sorted chunk ids).
  const sourceLabels = sources.map((s) => s.source);
  const sortedSourceKey = sourceLabels.slice().sort().join("|");
  const sortedChunkKey = chunkList
    .map((c) => c.id)
    .slice()
    .sort((a, b) => a - b)
    .join("|");

  const layout = useMemo(
    () =>
      computeRadialLayout(
        sourceLabels,
        chunkList.map((c) => ({ id: c.id, source: c.source }))
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sortedSourceKey, sortedChunkKey]
  );

  const nodes = useMemo(
    () =>
      buildCoreNodes(sources, chunkList, pipeline, {
        core: CORE_POS,
        final: FINAL_POS,
        sources: layout.sources,
        chunks: layout.chunks,
      }),
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
