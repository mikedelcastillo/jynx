"""FastAPI application and streaming endpoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .models import GenerateRequest
from .optimize import optimize_startup
from .pipeline import run_pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Size chunks to the model's context window before serving (flag-gated,
    # never raises). Re-runs on each uvicorn reload; cached probes keep it fast.
    await optimize_startup()
    yield


app = FastAPI(title="Jynx Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/generate-quiz-stream")
async def generate_quiz_stream(req: GenerateRequest):
    return StreamingResponse(
        run_pipeline(req),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
