"""Helpers that build streaming event dicts and serialize them to NDJSON."""

import json

# Allowed log levels: info, warn, error, success.


def log(message, level="info", **data):
    """Return a log event. `data` is always present (possibly empty)."""
    return {
        "type": "log",
        "level": level,
        "message": message,
        "data": data,
    }


def chunk(id, source, state, **fields):
    """Return a `chunk` event: one map task's lifecycle transition.

    `state` is one of queued|running|retrying|done|failed. Extra per-state
    fields (chars, count, attempt, error) are passed through.
    """
    return {"type": "chunk", "id": id, "source": source, "state": state, **fields}


def progress(phase, **fields):
    """Return a `progress` event: a pipeline stage/aggregate snapshot.

    `phase` is one of fetch|chunk|map|reduce|done. Extra fields carry the
    per-phase payload (e.g. source/state for fetch; total/done/running for map).
    """
    return {"type": "progress", "phase": phase, **fields}


def final(quiz_result_dict):
    """Return the terminal `final` event carrying the normalized quiz result."""
    return {"type": "final", "data": quiz_result_dict}


def to_ndjson(event):
    """Serialize an event dict to a newline-terminated JSON string."""
    return json.dumps(event) + "\n"
