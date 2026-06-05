"""Pydantic v2 models for the quiz output and request."""

from typing import List, Literal

from pydantic import BaseModel, Field, model_validator


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
