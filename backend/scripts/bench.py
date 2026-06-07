"""Throwaway reliability benchmark: drive run_pipeline over fixed Wikipedia
URLs for N rounds and report latency / question count / chunk outcomes.

Usage:
    python scripts/bench.py [out.json] [rounds] [num_questions]

Runs the pipeline in-process (same code path the server uses, including the
startup chunk-size optimizer) against the live OPENAI_BASE_URL. Writes a JSON
summary to `out.json` (default: bench_result.json) and prints a table.
"""

import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

from app.models import GenerateRequest  # noqa: E402
from app.optimize import optimize_startup  # noqa: E402
from app.pipeline import run_pipeline  # noqa: E402

URLS = [
    "https://en.wikipedia.org/wiki/Octopus",
    "https://en.wikipedia.org/wiki/Roman_Empire",
    "https://en.wikipedia.org/wiki/Photosynthesis",
]

# A chunk id is "final" once it reaches one of these; intermediate states
# (queued/running/retrying) are overwritten as the chunk progresses.
TERMINAL = {"done", "failed", "skipped"}


async def run_round(num_questions: int) -> dict:
    req = GenerateRequest(urls=URLS, text="", num_questions=num_questions)
    chunk_state: dict = {}        # id -> last state seen
    retry_events = 0              # count of "retrying" events
    status = "?"
    questions = 0

    start = time.monotonic()
    async for line in run_pipeline(req):
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        t = ev.get("type")
        if t == "chunk":
            state = ev.get("state")
            if state == "retrying":
                retry_events += 1
            chunk_state[ev.get("id")] = state
        elif t == "final":
            data = ev.get("data", {})
            status = data.get("status", "?")
            questions = len(data.get("data", {}).get("questions", []))
    elapsed = time.monotonic() - start

    done = sum(1 for s in chunk_state.values() if s == "done")
    failed = sum(1 for s in chunk_state.values() if s == "failed")
    skipped = sum(1 for s in chunk_state.values() if s == "skipped")
    return {
        "seconds": round(elapsed, 1),
        "status": status,
        "questions": questions,
        "chunks": len(chunk_state),
        "done": done,
        "failed": failed,
        "skipped": skipped,
        "retries": retry_events,
    }


async def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "bench_result.json"
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    num_questions = int(sys.argv[3]) if len(sys.argv) > 3 else 10

    await optimize_startup()

    results = []
    for i in range(rounds):
        print(f"[round {i + 1}/{rounds}] running...", flush=True)
        r = await run_round(num_questions)
        results.append(r)
        print(f"  {r}", flush=True)

    def mean(key):
        vals = [r[key] for r in results]
        return round(sum(vals) / len(vals), 1)

    summary = {
        "rounds": results,
        "mean": {
            k: mean(k)
            for k in ("seconds", "questions", "chunks", "done", "failed", "skipped", "retries")
        },
        "fails": sum(1 for r in results if r["status"] != "ok"),
    }
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)

    print("\n=== summary ===")
    print(f"{'round':>5} {'sec':>6} {'status':>6} {'q':>3} {'chunks':>6} "
          f"{'done':>4} {'fail':>4} {'skip':>4} {'retry':>5}")
    for i, r in enumerate(results):
        print(f"{i + 1:>5} {r['seconds']:>6} {r['status']:>6} {r['questions']:>3} "
              f"{r['chunks']:>6} {r['done']:>4} {r['failed']:>4} {r['skipped']:>4} {r['retries']:>5}")
    m = summary["mean"]
    print(f"{'mean':>5} {m['seconds']:>6} {'':>6} {m['questions']:>3} "
          f"{m['chunks']:>6} {m['done']:>4} {m['failed']:>4} {m['skipped']:>4} {m['retries']:>5}")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
