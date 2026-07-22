# Data contract

## Error analysis JSON

Required fields:

- `problem_text`: complete reconstructed problem.
- `cause_code`: one code from `error-taxonomy.md`.
- `cause_detail`: evidence-based diagnosis and correction.
- `difficulty`: numeric value from 1.0 to 5.0.
- `confidence`: numeric value from 0.0 to 1.0.

Recommended fields:

- `student_answer`, `correct_answer`, `correct_solution`, `first_wrong_step`.
- `evidence`: array of exact observations from the work.
- `knowledge_codes`: array from `assets/knowledge-points.json`.
- `feature_codes`: structural signature codes returned by `features --json`.
- `question_type`: used with structural features for recommendation reranking.
- `image_path`: local source image path when available.
- `question_id`: linked bank question when known.

## Question import record

Preferred fields:

```json
{
  "id": "source-stable-id",
  "stem": "problem text",
  "options": ["A", "B", "C", "D"],
  "answer": "answer",
  "solution": "reviewed solution",
  "grade": 10,
  "semester": 1,
  "question_type": "选择题",
  "difficulty": 3.0,
  "knowledge_codes": ["function-properties"],
  "target_causes": ["concept_confusion"],
  "feature_codes": ["parameter-range", "case-analysis"],
  "source_year": "2026",
  "verified": false
}
```

The importer also accepts `problem` or `question` instead of `stem`, `label` instead of `answer`, and `analysis` instead of `solution`. Difficulty uses the 1–5 scale. External material must carry source-level name, URL, and license through CLI arguments.

## Verification

A verified question has:

1. Legible and complete conditions.
2. A checked final answer.
3. Valid knowledge tags and difficulty.
4. Traceable source and use permission.
5. No unresolved duplicate or contradiction.

Model-generated questions are unverified until independently checked. A verification review must follow `assets/question-review-template.json`, include an independent answer and solution, and explicitly confirm stem, source, duplicate, answer, and solution checks.
