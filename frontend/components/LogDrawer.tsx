"use client";

import { useState } from "react";
import LogConsole from "@/components/LogConsole";
import type { LogEvent } from "@/lib/types";

interface LogDrawerProps {
  logs: LogEvent[];
}

export default function LogDrawer({ logs }: LogDrawerProps) {
  const [open, setOpen] = useState(false);
  const latest = logs[logs.length - 1]?.message;

  return (
    <div className={`log-drawer${open ? " open" : ""}`}>
      <button
        type="button"
        className="log-drawer-bar"
        onClick={() => setOpen((o) => !o)}
      >
        <span>{open ? "▾" : "▴"}</span>
        <span>Logs</span>
        <span className="log-drawer-count">{logs.length}</span>
        <span className="log-drawer-latest">{latest}</span>
      </button>
      {open && (
        <div className="log-drawer-panel">
          <LogConsole logs={logs} />
        </div>
      )}
    </div>
  );
}
