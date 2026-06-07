"""Instrumented single-URL reproduction to localize the 'working...' hang.

Runs run_pipeline on one URL and prints every NDJSON event with elapsed time.
A heartbeat task prints the elapsed time and the LAST event seen every 5s, so
if events stop flowing we can see EXACTLY which phase stalled (fetch / extract /
chunk / map / reduce). Hard cap at 280s.

Usage: python scripts/repro_hang.py <url> [num_questions]
"""

import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

from app.models import GenerateRequest  # noqa: E402
from app.optimize import optimize_startup  # noqa: E402
from app.pipeline import run_pipeline  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "https://en.wikipedia.org/wiki/Octopus"
NUMQ = int(sys.argv[2]) if len(sys.argv) > 2 else 10

state = {"last": "(none)", "t": 0.0, "count": 0, "done": False}


async def heartbeat(start):
    while not state["done"]:
        await asyncio.sleep(5)
        elapsed = time.monotonic() - start
        since = elapsed - state["t"]
        flag = "  <-- STALLED" if since > 8 else ""
        print(f"  [hb {elapsed:6.1f}s] events={state['count']} "
              f"last='{state['last']}' ({since:.1f}s ago){flag}", flush=True)


async def main():
    await optimize_startup()
    req = GenerateRequest(urls=[URL], text="", num_questions=NUMQ)
    start = time.monotonic()
    hb = asyncio.create_task(heartbeat(start))
    try:
        async for line in run_pipeline(req):
            elapsed = time.monotonic() - start
            try:
                ev = json.loads(line)
            except (ValueError, TypeError):
                continue
            t = ev.get("type")
            if t == "log":
                brief = f"log/{ev.get('level')}: {ev.get('message')}"
            elif t == "chunk":
                brief = f"chunk#{ev.get('id')} {ev.get('state')}"
            elif t == "progress":
                brief = f"progress/{ev.get('phase')} {ev.get('step', '')}"
            elif t == "final":
                d = ev.get("data", {})
                brief = f"FINAL status={d.get('status')} q={len(d.get('data', {}).get('questions', []))}"
            else:
                brief = t
            state.update(last=brief, t=elapsed, count=state["count"] + 1)
            print(f"[{elapsed:6.1f}s] {brief}", flush=True)
    finally:
        state["done"] = True
        hb.cancel()
    print(f"\ntotal {time.monotonic() - start:.1f}s, {state['count']} events", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), timeout=280))
    except asyncio.TimeoutError:
        print(f"\n!!! HARD TIMEOUT 280s — HUNG. last event: '{state['last']}' "
              f"at {state['t']:.1f}s ({state['count']} events)", flush=True)
