"""Deterministic regression test for the first-token watchdog.

Bug: stream_completion's watchdog reset its timeout on EVERY stream event, not
on every CONTENT token. A backend that streams empty / keep-alive / reasoning
frames (no delta.content) kept the watchdog alive forever, so a chunk that
never produced content ran to the full LLM_TIMEOUT instead of aborting at the
first-token budget.

This test feeds a fake stream that emits empty-content frames forever and
asserts stream_completion aborts at the first-token budget (fast), NOT at
LLM_TIMEOUT. No network / no cluster needed.

Run: python scripts/test_watchdog.py   (exit 0 = pass, 1 = fail)
"""

import asyncio
import sys
import time

sys.path.insert(0, ".")

from app import llm  # noqa: E402


class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Event:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _FakeStream:
    """Emits empty-content frames forever (like a queued/thinking backend)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0.05)
        return _Event(None)  # never any content


async def main():
    # Shrink the budgets so the test runs in ~1s instead of ~90s. Patch the
    # names in llm's namespace (stream_completion reads them from there).
    llm.LLM_FIRST_TOKEN_TIMEOUT = 1.0
    llm.LLM_INTERTOKEN_TIMEOUT = 1.0
    llm.LLM_TIMEOUT = 8.0

    async def fake_create(**kwargs):
        return _FakeStream()

    llm._client.chat.completions.create = fake_create

    async def emit(ev):
        pass

    start = time.monotonic()
    raised = None
    try:
        await llm.stream_completion([{"role": "user", "content": "x"}], emit)
    except BaseException as exc:  # noqa: BLE001 - we want to see what propagates
        raised = exc
    elapsed = time.monotonic() - start

    print(f"elapsed={elapsed:.2f}s raised={type(raised).__name__ if raised else None}")

    ok = (
        isinstance(raised, asyncio.TimeoutError)
        and elapsed < llm.LLM_FIRST_TOKEN_TIMEOUT + 1.5  # aborted near first-token budget
        and elapsed < llm.LLM_TIMEOUT - 1.0               # NOT at the overall cap
    )
    if ok:
        print("PASS: watchdog aborted at the first-token (content) budget")
        return 0
    print(
        f"FAIL: expected TimeoutError near {llm.LLM_FIRST_TOKEN_TIMEOUT}s, "
        f"got {type(raised).__name__ if raised else None} at {elapsed:.2f}s "
        f"(LLM_TIMEOUT={llm.LLM_TIMEOUT}s)"
    )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
