"""Deterministic test for the map-phase first-content fail-fast guard.

Simulates a backend that accepts the request and holds the stream open but
never produces a content token (queued/wedged). The pipeline must give up at
MAP_FIRST_CONTENT_DEADLINE with a clear message — NOT grind to MAP_DEADLINE.

No network / no cluster needed (uses pasted text, fake stream_completion).
Run: python scripts/test_no_content_guard.py   (exit 0 = pass, 1 = fail)
"""

import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

from app import llm, pipeline  # noqa: E402
from app.models import GenerateRequest  # noqa: E402


async def _silent_stream_completion(*args, **kwargs):
    # Stream stays open but never yields content (on_progress never fires).
    await asyncio.sleep(9999)


async def main():
    # pipeline calls llm.stream_completion via the module reference.
    llm.stream_completion = _silent_stream_completion
    # Shrink the guard window and keep the full deadline far larger so a pass
    # can only come from the guard, not the deadline.
    pipeline.MAP_FIRST_CONTENT_DEADLINE = 3.0
    pipeline.MAP_DEADLINE_SECONDS = 60.0

    text = (
        "Photosynthesis is the process by which green plants convert light "
        "energy into chemical energy stored in glucose. "
    ) * 40
    req = GenerateRequest(urls=[], text=text, num_questions=10)

    final = None
    start = time.monotonic()
    async for line in pipeline.run_pipeline(req):
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if ev.get("type") == "final":
            final = ev.get("data")
    elapsed = time.monotonic() - start

    status = final.get("status") if final else None
    message = final.get("message", "") if final else ""
    print(f"elapsed={elapsed:.1f}s status={status} message={message!r}")

    ok = (
        status == "fail"
        and "no output in time" in message
        and elapsed < pipeline.MAP_FIRST_CONTENT_DEADLINE + 5  # guard, not deadline
    )
    if ok:
        print("PASS: map phase failed fast at the first-content guard")
        return 0
    print("FAIL: expected a fast 'no output in time' fail at the guard window")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
