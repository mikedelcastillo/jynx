"""Pydantic v2 models for the quiz output and request."""

from typing import List, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .config import DEFAULT_NUM_QUESTIONS, MAX_NUM_QUESTIONS, MIN_NUM_QUESTIONS


class Option(BaseModel):
    label: str
    value: str


class Question(BaseModel):
    question: str
    options: List[Option] = Field(..., min_length=2, max_length=6)
    answer: str

    @model_validator(mode="after")
    def answer_matches_option(self) -> "Question":
        values = [opt.value for opt in self.options]
        if self.answer not in values:
            raise ValueError(
                "answer must exactly match one of the option values"
            )
        return self


class QuizData(BaseModel):
    questions: List[Question]


class QuizResult(BaseModel):
    status: Literal["ok", "fail"]
    message: str
    data: QuizData


class GenerateRequest(BaseModel):
    urls: List[str] = []
    text: str = ""
    num_questions: int = DEFAULT_NUM_QUESTIONS

    @field_validator("num_questions", mode="before")
    @classmethod
    def clamp_num_questions(cls, value):
        """Clamp to [MIN, MAX] instead of rejecting — an out-of-range value
        must never 422 the stream. Non-ints fall back to the default."""
        try:
            value = int(value)
        except (TypeError, ValueError):
            return DEFAULT_NUM_QUESTIONS
        return max(MIN_NUM_QUESTIONS, min(MAX_NUM_QUESTIONS, value))
