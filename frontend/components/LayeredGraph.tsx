"use client";

import React, { useMemo } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import type { SourceState, ChunkState, PipelineState } from "@/lib/types";
import { FlowCanvas, type RFNode, type RFEdge } from "./pipeline/shared";

interface Props {
  sources: SourceState[];
  chunks: Record<number, ChunkState>;
  pipeline: PipelineState | null;
}

// Left-to-right pipeline: sources → chunks → reduce(core) → quiz.
const X_SRC = 0;
const X_CHUNK = 260;
const X_CORE = 560;
const X_FINAL = 820;
const GAP_Y = 82;

export default function LayeredGraph({ sources, chunks, pipeline }: Props) {
  const chunkList = Object.values(chunks);
  const rosterKey = sources.map((s) => s.source).join("|");
  const chunkKey = chunkList
    .map((c) => `${c.id}:${c.source}`)
    .sort()
    .join("|");

  const layout = useMemo(() => {
    const bySource: Record<string, number[]> = {};
    for (const c of chunkList) (bySource[c.source] ??= []).push(c.id);
    const ordered = [
      ...sources.map((s) => s.source),
      ...Object.keys(bySource).filter(
        (s) => !sources.some((r) => r.source === s)
      ),
    ];

    const chunkY: Record<number, number> = {};
    const srcY: Record<string, number> = {};
    let slot = 0;
    for (const src of ordered) {
      const ids = (bySource[src] ?? []).slice().sort((a, b) => a - b);
      if (ids.length === 0) {
        srcY[src] = slot * GAP_Y;
        slot += 1;
      } else {
        const ys: number[] = [];
        for (const id of ids) {
          chunkY[id] = slot * GAP_Y;
          ys.push(slot * GAP_Y);
          slot += 1;
        }
        srcY[src] = ys.reduce((a, b) => a + b, 0) / ys.length;
      }
    }
    const allY = [...Object.values(chunkY), ...Object.values(srcY)];
    const center = allY.length
      ? (Math.min(...allY) + Math.max(...allY)) / 2
      : 0;
    return { chunkY, srcY, center };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rosterKey, chunkKey]);

  const nodes = useMemo<RFNode[]>(() => {
    const { chunkY, srcY, center } = layout;
    const result: RFNode[] = [];

    for (const s of sources) {
      result.push({
        id: `src:${s.source}`,
        type: "source",
        position: { x: X_SRC, y: (srcY[s.source] ?? 0) - center },
        data: { source: s },
      });
    }
    for (const c of chunkList) {
      result.push({
        id: `chunk:${c.id}`,
        type: "chunk",
        position: { x: X_CHUNK, y: (chunkY[c.id] ?? 0) - center },
        data: { chunk: c },
      });
    }
    result.push({
      id: "core",
      type: "core",
      position: { x: X_CORE, y: 0 },
      data: { pipeline },
    });
    if (pipeline?.phase === "done") {
      result.push({
        id: "final",
        type: "final",
        position: { x: X_FINAL, y: 0 },
        data: { questions: pipeline?.questions },
      });
    }
    return result;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources, chunks, pipeline, layout]);

  const edges = useMemo<RFEdge[]>(() => {
    const result: (RFEdge & { sourceHandle?: string; targetHandle?: string })[] =
      [];
    const reducing = pipeline?.phase === "reduce";
    for (const c of chunkList) {
      const hasSource = sources.some((s) => s.source === c.source);
      if (hasSource) {
        result.push({
          id: `e:src:chunk:${c.id}`,
          source: `src:${c.source}`,
          target: `chunk:${c.id}`,
          sourceHandle: "r",
          targetHandle: "l",
          animated: c.state === "running" || c.state === "retrying",
        });
      }
      if (c.state === "done") {
        result.push({
          id: `e:chunk:core:${c.id}`,
          source: `chunk:${c.id}`,
          target: "core",
          sourceHandle: "r",
          targetHandle: "l",
          animated: reducing,
        });
      }
    }
    if (pipeline?.phase === "done") {
      result.push({
        id: "e:core:final",
        source: "core",
        target: "final",
        sourceHandle: "r",
        targetHandle: "l",
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
