"""Regression test: reasoning tokens count as stream liveness.

Bug: stream_completion's watchdog reset its budget only on non-empty
`delta.content`. A reasoning model (e.g. qwen3 via Ollama) streams its
chain-of-thought in a separate `reasoning` field with `content == ""` for the
whole (often long) thinking phase, so the watchdog misread an actively-thinking,
GPU-busy model as a silent/stalled stream and aborted it at the first-token
budget — before the real JSON answer was ever emitted.

This test feeds a fake stream that emits `reasoning` frames (empty content) for
LONGER than the first-token budget, then real content, and asserts
stream_completion SURVIVES the thinking phase and returns the content.

Contrast with test_watchdog.py: a stream that is truly silent (no content AND no
reasoning) must still abort fast. That remains correct and is unaffected.

Run: python scripts/test_reasoning_liveness.py   (exit 0 = pass, 1 = fail)
"""

import asyncio
import sys
import time

sys.path.insert(0, ".")

from app import llm  # noqa: E402


class _Delta:
    def __init__(self, content=None, reasoning=None):
        self.content = content
        self.reasoning = reasoning


class _Choice:
    def __init__(self, **kw):
        self.delta = _Delta(**kw)


class _Event:
    def __init__(self, **kw):
        self.choices = [_Choice(**kw)]


class _ReasoningThenContentStream:
    """Thinks (reasoning frames, empty content) past the first-token budget,
    then streams the real JSON answer as content, then ends."""

    def __init__(self):
        self._frames = (
            # ~1.8s of "thinking" — well past the shrunken 1.0s first-token budget.
            [_Event(content="", reasoning="tok") for _ in range(18)]
            + [_Event(content=c) for c in ('{"questions":', "[]", "}")]
        )
        self._i = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0.1)
        if self._i >= len(self._frames):
            raise StopAsyncIteration
        ev = self._frames[self._i]
        self._i += 1
        return ev


async def main():
    # Shrink budgets so the thinking phase (1.8s) clearly exceeds the
    # first-token budget (1.0s) but stays under LLM_TIMEOUT.
    llm.LLM_FIRST_TOKEN_TIMEOUT = 1.0
    llm.LLM_INTERTOKEN_TIMEOUT = 1.0
    llm.LLM_TIMEOUT = 10.0

    async def fake_create(**kwargs):
        return _ReasoningThenContentStream()

    llm._client.chat.completions.create = fake_create

    async def emit(ev):
        pass

    progress = []

    async def on_progress(c):
        progress.append(c)

    start = time.monotonic()
    raised = None
    result = None
    try:
        result = await llm.stream_completion(
            [{"role": "user", "content": "x"}], emit, on_progress=on_progress
        )
    except BaseException as exc:  # noqa: BLE001 - we want to see what propagates
        raised = exc
    elapsed = time.monotonic() - start

    print(
        f"elapsed={elapsed:.2f}s raised={type(raised).__name__ if raised else None} "
        f"result={result!r} progress_calls={len(progress)}"
    )

    ok = (
        raised is None
        and result == '{"questions":[]}'
        and elapsed > llm.LLM_FIRST_TOKEN_TIMEOUT  # survived past the budget
    )
    if ok:
        print("PASS: reasoning counted as liveness; content returned after thinking")
        return 0
    print(
        "FAIL: expected stream_completion to survive the thinking phase and return "
        f'\'{{"questions":[]}}\', got raised={type(raised).__name__ if raised else None} '
        f"result={result!r}"
    )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
