"use client";

import { useEffect, useRef } from "react";
import type { LogEvent } from "@/lib/types";

interface LogConsoleProps {
  logs: LogEvent[];
}

export default function LogConsole({ logs }: LogConsoleProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  return (
    <div className="log-console">
      {logs.length === 0 && (
        <div className="log-empty">Waiting for output...</div>
      )}
      {logs.map((log, i) => (
        <div className={`log-line log-${log.level}`} key={i}>
          <span className="log-level">[{log.level}]</span>{" "}
          <span className="log-message">{log.message}</span>
          {log.data && (
            <span className="log-data"> {JSON.stringify(log.data)}</span>
          )}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
