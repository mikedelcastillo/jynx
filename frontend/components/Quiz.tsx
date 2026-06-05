"use client";

import { useState } from "react";
import type { Question } from "@/lib/types";

interface QuizProps {
  questions: Question[];
}

export default function Quiz({ questions }: QuizProps) {
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<number, string>>({});

  if (questions.length === 0) {
    return <p className="quiz-empty">No questions were generated.</p>;
  }

  const current = questions[index];
  const selected = answers[index];

  const score = questions.reduce(
    (acc, q, i) => (answers[i] === q.answer ? acc + 1 : acc),
    0
  );

  function select(value: string) {
    setAnswers((prev) => ({ ...prev, [index]: value }));
  }

  return (
    <div className="quiz">
      <div className="quiz-progress">
        Question {index + 1} of {questions.length}
      </div>
      <h3 className="quiz-question">{current.question}</h3>

      <div className="quiz-options">
        {current.options.map((opt) => {
          const isSelected = selected === opt.value;
          const answered = selected !== undefined;
          const isCorrect = opt.value === current.answer;
          let stateClass = "";
          if (answered && isSelected && isCorrect) stateClass = "option-correct";
          else if (answered && isSelected && !isCorrect)
            stateClass = "option-incorrect";
          else if (answered && isCorrect) stateClass = "option-reveal";

          return (
            <label
              className={`quiz-option ${isSelected ? "selected" : ""} ${stateClass}`}
              key={opt.value}
            >
              <input
                type="radio"
                name={`question-${index}`}
                checked={isSelected}
                onChange={() => select(opt.value)}
              />
              <span>{opt.label}</span>
            </label>
          );
        })}
      </div>

      <div className="quiz-nav">
        <button
          type="button"
          onClick={() => setIndex((i) => Math.max(0, i - 1))}
          disabled={index === 0}
        >
          Previous
        </button>
        <button
          type="button"
          onClick={() =>
            setIndex((i) => Math.min(questions.length - 1, i + 1))
          }
          disabled={index === questions.length - 1}
        >
          Next
        </button>
      </div>

      <div className="quiz-score">
        Score: {score} / {questions.length}
      </div>
    </div>
  );
}
