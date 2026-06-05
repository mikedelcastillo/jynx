"use client";

import type { QuizResult } from "@/lib/types";

interface RawJsonPanelProps {
  result: QuizResult;
  open: boolean;
  onClose: () => void;
}

export default function RawJsonPanel({
  result,
  open,
  onClose,
}: RawJsonPanelProps) {
  if (!open) return null;

  return (
    <div className="raw-json-backdrop" onClick={onClose}>
      <div className="raw-json-panel" onClick={(e) => e.stopPropagation()}>
        <div className="raw-json-header">
          <span>Sample results (raw JSON)</span>
          <button type="button" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <pre className="raw-json-content">
          {JSON.stringify(result, null, 2)}
        </pre>
      </div>
    </div>
  );
}
