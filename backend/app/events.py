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


def final(quiz_result_dict):
    """Return the terminal `final` event carrying the normalized quiz result."""
    return {"type": "final", "data": quiz_result_dict}


def to_ndjson(event):
    """Serialize an event dict to a newline-terminated JSON string."""
    return json.dumps(event) + "\n"
