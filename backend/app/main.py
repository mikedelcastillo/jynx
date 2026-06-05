"""FastAPI application and streaming endpoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .models import GenerateRequest
from .pipeline import run_pipeline

app = FastAPI(title="Jynx Backend")

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
