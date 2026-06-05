import type { StreamEvent } from "./types";

// By default the backend is reached at a RELATIVE path: the browser calls
// `/api/...` on whatever origin served the page, and Next.js `rewrites()`
// proxies it to the backend server-side (see next.config.mjs). That makes the
// app single-origin, so it works unchanged from the server, a laptop, or a
// phone — no IP to configure, no CORS. Set NEXT_PUBLIC_API_BASE_URL only to
// bypass the proxy and talk to a backend on a different host directly.
const base = process.env.NEXT_PUBLIC_API_BASE_URL || "";

export async function* streamQuiz(
  body: { urls: string[]; text: string },
  signal?: AbortSignal
): AsyncGenerator<StreamEvent> {
  let res: Response;
  try {
    res = await fetch(`${base}/api/generate-quiz-stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    yield {
      type: "log",
      level: "error",
      message: `Request failed: ${
        err instanceof Error ? err.message : String(err)
      }`,
    };
    return;
  }

  if (!res.ok || !res.body) {
    yield {
      type: "log",
      level: "error",
      message: `Server responded with status ${res.status} ${res.statusText}`,
    };
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      // Keep the last (possibly partial) line in the buffer.
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          yield JSON.parse(trimmed) as StreamEvent;
        } catch {
          yield {
            type: "log",
            level: "warn",
            message: `Could not parse stream line: ${trimmed}`,
          };
        }
      }
    }

    // Flush any remaining buffered content.
    const trailing = buffer.trim();
    if (trailing) {
      try {
        yield JSON.parse(trailing) as StreamEvent;
      } catch {
        yield {
          type: "log",
          level: "warn",
          message: `Could not parse trailing stream line: ${trailing}`,
        };
      }
    }
  } catch (err) {
    if (signal?.aborted) return;
    yield {
      type: "log",
      level: "error",
      message: `Stream error: ${
        err instanceof Error ? err.message : String(err)
      }`,
    };
  }
}
