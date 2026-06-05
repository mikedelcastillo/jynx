"""Run the backend with `python -m app`.

Binds to config.HOST/config.PORT (0.0.0.0:8000 by default), so the server is
reachable from other devices/containers without passing CLI flags. Auto-reload
is on by default for local dev; disable it with RELOAD=false.
"""

import os

import uvicorn

from . import config

if __name__ == "__main__":
    reload = os.getenv("RELOAD", "true").lower() in ("1", "true", "yes", "on")
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, reload=reload)
