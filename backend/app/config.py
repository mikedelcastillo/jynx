"""Configuration loaded from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load the shared project-root .env first (one file configures the whole
# project), then a backend-local .env if present. Neither overrides variables
# already set in the real environment (e.g. those provided by Docker Compose).
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ROOT_ENV)
load_dotenv()

# OpenAI-compatible endpoint settings (the only place these defaults live).
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:40114/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ollama")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen2.5:14b")

# Limits / tuning constants.
REQUEST_TIMEOUT = 10  # seconds
MAX_HTML_BYTES = 3_000_000
MAX_EXTRACTED_CHARS = 40_000
MAX_CONTEXT_CHARS = 16_000
TARGET_CHUNK_WORDS = 900
NUM_QUESTIONS = 8
