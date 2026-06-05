"""Tiny pasted-text-only eval for the quiz pipeline.

Runs the pipeline with pasted text (no URLs), captures the final event, and
asserts the result is a valid quiz. Exit code 0 on PASS, 1 on FAIL.

NOTE: This requires a reachable OPENAI_BASE_URL (an OpenAI-compatible endpoint,
e.g. Ollama) with the configured model available.
"""

import asyncio
import json
import sys
from pathlib import Path

# Allow running as `python scripts/eval.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import GenerateRequest, QuizResult  # noqa: E402
from app.pipeline import run_pipeline  # noqa: E402

SAMPLE_TEXT = (
    "The Eiffel Tower is a wrought-iron lattice tower in Paris, France. It was "
    "designed by the engineer Gustave Eiffel and completed in 1889 for the "
    "World's Fair. The tower stands 330 meters tall and was the tallest "
    "man-made structure in the world until the Chrysler Building was finished "
    "in New York City in 1930.\n\n"
    "The Eiffel Tower has three levels open to visitors. It is painted roughly "
    "every seven years to protect it from rust. Millions of people visit the "
    "tower each year, making it one of the most visited paid monuments in the "
    "world."
)


async def _run():
    req = GenerateRequest(urls=[], text=SAMPLE_TEXT)
    final_event = None
    async for line in run_pipeline(req):
        event = json.loads(line)
        if event.get("type") == "final":
            final_event = event
    return final_event


def main():
    final_event = asyncio.run(_run())

    try:
        assert final_event is not None, "no final event was emitted"
        result_dict = final_event["data"]
        # Validate via the Pydantic model.
        result = QuizResult(**result_dict)
        assert result.status in ("ok", "fail"), "status missing/invalid"
        assert result.status == "ok", f"status was fail: {result.message}"
        questions = result.data.questions
        assert questions, "data.questions is empty"
        for i, q in enumerate(questions):
            assert 2 <= len(q.options) <= 6, f"question {i} has bad option count"
            values = [o.value for o in q.options]
            assert q.answer in values, f"question {i} answer matches no option"
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        if final_event is not None:
            print(json.dumps(final_event, indent=2))
        sys.exit(1)

    print(f"PASS: {len(result.data.questions)} valid questions generated.")
    sys.exit(0)


if __name__ == "__main__":
    main()
