---
name: math-error-notebook
description: Manage a Chinese high-school math error notebook backed by one local verified question bank. Use for grading photographed or typed Grade 10-12 math work, diagnosing the first wrong step, saving structured errors, recommending verified practice, generating/printing practice PDFs, importing authorized questions, recording attempts, reviews, and weakness statistics.
---

# 李兆霖数学错题本

## Fast path

- An installed copy binds to `LIZHAOLIN_MATH_NOTEBOOK_ROOT` when set; otherwise it uses the nearest parent containing `data/math_notebook.db` or a project-local copy of this Skill. If neither exists, it uses the current directory so `init` can create a new notebook. Never search for another database after binding.
- On Windows PowerShell, always pass `-Encoding UTF8` to `Get-Content`, and run Python entry points with `-X utf8`; do not guess encodings after mojibake appears.
- Start routine work with one compact, read-only call: `scripts/notebook.py agent-context --task grade|recommend|verify|import|review|pdf|maintenance --json`. Use `doctor --json` for environment health and `handoff --json` for a compact transfer snapshot.
- Use image input directly for photos; never claim to switch models/providers. The default preflight only controls image size locally, and the current remote vision-capable model must inspect every returned preview.
- Text-only models, including DeepSeek sessions without image input, must hand photo grading to a vision-capable model; they must not infer unseen handwriting from filenames or OCR remnants.
- Only explicit `photo-preflight --preflight-mode ocr` calls share the machine-wide OCR execution lock. Concurrent OCR diagnostics queue through the authoritative entry point; the default remote-preview route loads no OCR model and takes no OCR lock.
- Run deterministic work through `scripts/notebook.py`. Its project installation binds to the sole bank `data/math_notebook.db`; never discover, merge, copy over, or select another same-named DB.
- Do not recursively enumerate `data/audits/`, `data/imports/`, or the question corpus. Use compact CLI queries.
- For multi-step work, start a recoverable manifest with `workflow-start --kind grade|import|verify|recommend|pdf`; update each artifact with `workflow-update`, and resume from `workflow-status` instead of repeating completed stages.
- Use `behavior-cases --category grade|verify|recommend --json` to align a newly connected model with the project's grading and recommendation boundaries; load one full case with `--id` only when needed.
- `--json` is compact by default. Use global `--pretty-json` only for human inspection. `search`, `recommend`, and `audit-queue` omit long solutions/raw records unless `--full` is explicit.
- Before imports or maintenance, run `bank-info --json`. Run `init` then idempotent `seed` only when the canonical bank does not exist.

## First-run onboarding

When the user says the Skill was just installed, asks how to begin, or has not
yet bound a notebook, follow this section and guide them in plain language. Do
not make the user type shell commands unless they explicitly ask for manual
operation.

1. Distinguish an existing notebook from a new blank notebook without scanning
   other drives. An existing notebook is used by opening its project directory
   in Codex or by setting `LIZHAOLIN_MATH_NOTEBOOK_ROOT`.
2. For an existing notebook, run `doctor --json` and `bank-info --json`, then
   report the bound database path, verified/unverified counts, error count, and
   whether it is ready. Never run `init` or `seed` over it.
3. For a new notebook, obtain confirmation that the current directory is the
   intended new location, then run `init`, `seed`, `doctor --json`, and
   `bank-info --json`. Report the actual seed count instead of hard-coding it.
4. End onboarding with three or four copy-ready natural-language examples for
   grading a photo, checking reviews, importing authorized questions, and
   generating a questions-only PDF. State that answers and printing are opt-in.
5. If binding or health checks fail, explain the single next action in plain
   language. Do not create a second database, search the computer for another
   bank, or install the optional download watcher.

## Grade work

1. For photos, first create cached, size-controlled previews. The local step performs only EXIF orientation, white-background conversion for transparent images, JPEG encoding, and resizing. Open **every** returned `preview_path` with the current remote vision-capable model:

```powershell
python -B <skill-dir>\scripts\notebook.py photo-preflight <image...> --json
```

The default is `--preflight-mode remote --task grade`. It does not import or
start RapidOCR, PaddleOCR, or local Qwen, and it does not acquire the shared OCR
lock. `review_route=remote_model_visual_review` means the preview must be viewed,
not merely read as a path. Use the compact result directly; routine grading must
not read the full packet. When a printed question ID is visible, load only
`question <id> --compact --json`; request the full item only if its solution is
genuinely needed.

The slower legacy OCR route is diagnostic-only:

```powershell
python -X utf8 -B <skill-dir>\scripts\notebook.py photo-preflight <image...> --preflight-mode ocr --formula-ocr off --vision-mode off --json
```

Use `--formula-ocr paddle` or `--vision-mode required` only with
`--preflight-mode ocr` and only when explicitly diagnosing those local services.
OCR/formula output remains untrusted and never replaces visual inspection.

When explicitly diagnosing a local visual model, it may act only as a
**transcription layer**. Get the fixed, versioned prompt contract
from the authoritative CLI, give that prompt plus one preview/crop and its
`source_sha256` to the local model, then validate the JSON before the grading
model reads it:

```powershell
python -X utf8 -B <skill-dir>\scripts\notebook.py photo-vlm-contract --json
python -X utf8 -B <skill-dir>\scripts\notebook.py photo-vlm-validate <response.json> --packet <ocr-packet.json> --page 1 --json
```

The local model is forbidden to solve, grade, supply a standard answer, diagnose
an error, or emit chain-of-thought. The validator rejects grading fields, wrong
prompt/schema versions, non-JSON wrappers, and source-hash mismatches. It also
compares printed transcription with RapidOCR and returns either `quality_gate=pass`
or `visual_review_required`. Only the validated compact JSON may enter the
DeepSeek/Codex grading context; it does not relax visual review for ambiguous
formulae, handwriting, or diagrams.

For image-backed bank audits, `prepare-audit-batch --visual-evidence auto`
creates the same size-controlled previews and routes them to the remote visual
reviewer without OCR. `off` skips the evidence packet. `required` is reserved for
an explicit legacy OCR/local-Qwen diagnostic.

2. Separate printed content from handwriting; reconstruct steps and identify the first substantive error.
3. If a key symbol, condition, diagram, or step is unreadable, state it and request a clearer crop. Never fabricate. Use `unclear` for insufficient evidence; use `careless` only with direct evidence.
4. Get compact codes instead of loading full catalogs:

```powershell
python -B <skill-dir>\scripts\notebook.py causes --json
python -B <skill-dir>\scripts\notebook.py knowledge --text "相关主题" --json
python -B <skill-dir>\scripts\notebook.py features --text "相切" --json
```

5. Copy `assets/error-analysis-template.json`, fill it with LaTeX, validate it, then persist:

```powershell
python -B <skill-dir>\scripts\notebook.py grade-preview <analysis.json> --json
python -B <skill-dir>\scripts\notebook.py grade-commit <analysis.json> --copy-image --json
```

6. Lead with the first wrong step, cause, correct method, prevention cue, and saved error ID. Read `references/error-taxonomy.md` only when cause selection is ambiguous; read `references/data-contract.md` only if the template or validation fails.

### Grading response standard

- Grade every visible question separately.
- If an answer is wrong or partially correct, show the complete original question, the student's answer, the first substantive error, a complete correct derivation, and the final answer.
- When two or more genuinely different applicable methods exist, list them separately; do not present cosmetic algebra rearrangements as different methods.
- If the original stem, symbol, condition, or diagram is unclear, do not reconstruct it silently. State what is unreadable and request a clearer crop.
- For a fully correct answer, a concise verdict plus the key verification is sufficient.
- End every grading response with a short **下一步** block addressed to the child. State an immediate action, a concrete quantity or completion condition, and what to submit afterward. For wrong/partial work, normally require: cover the solution, redo the original from the first wrong step, complete the assigned verified recommendations, then send clear photos. For correct work, state whether to continue to the next assigned item or stop until the recorded review date. For unclear work, request only the exact missing crop or condition. Never end with generic advice such as “多练习” or “认真检查”.

## Recommend and print

Preview compact, same-type verified matches with 1-3 discriminative keywords; save only after relevance review:

```powershell
python -B <skill-dir>\scripts\notebook.py recommend <error-id> --feature tangent --keyword "圆" --limit 3 --json
python -B <skill-dir>\scripts\notebook.py recommend-packet <error-id> --feature tangent --keyword "圆" --limit 3 --out <packet.json> --json
python -B <skill-dir>\scripts\notebook.py assign-recommendations <error-id> <packet.json> --save --json
```

- Default output and recommendation packets hide answers/solutions. Ranking, keyword tokenization, obvious placeholder rejection, difficulty progression, knowledge, cause, attempt history, and audited structural features run locally. Review the compact packet for relevance, then pass that same packet to `assign-recommendations`; do not create a second plan file. Use `question <id> --json` only for an ambiguous shortlisted item, or `recommend-packet --full` only when complete solutions are genuinely needed.
- Questions with any recorded practice attempt are excluded from recommendation candidates.
- If automatic matches are weak, save a reviewed verified set with `assign-recommendations`; never substitute invented questions silently.
- If fewer than three verified matches exist, report the shortfall and offer adjacent-topic search or generated questions labeled unverified.
- After saving, create the A4 PDF (questions only by default; add `--with-answers` when the user wants an answer page) and print only when the user asks:

```powershell
python -B <skill-dir>\scripts\practice_sheet.py <error-id> --print
```

For a formal exam assembled from reviewed verified-bank items, save one
`math-exam-packet/v1` JSON file under `practice/` with section titles, positive
per-question scores, a matching total score, and verified `question_id` values,
then use `practice_sheet.py --exam-packet <packet.json>`. The loader rejects
missing, duplicate, unverified, or score-inconsistent items. `display_stem` is
allowed only for layout-preserving notation fixes that do not change meaning.
Exam PDFs also default to no answers and no print.

Printer/output preferences live in `config/math-error-notebook.json`. The script emits only a compact artifact summary.

After grading practice, record it with `attempt <question-id> --error-id <error-id> --correct|--wrong`; include `--cause-code` when wrong, then adapt recommendations.
If a previously recorded attempt was misgraded, correct that same row with
`correct-attempt <attempt-id> --correct|--wrong`; do not add a second attempt.

## Import and verify

Read `references/import-and-verification.md` only for question import, source repair, coverage audit, or verification work. Use `prepare-audit-batch` to create per-item `audit-item` packets and safe pending review skeletons, then complete each review and call `verify-item`; the preparation command never changes verification state. To reduce repeated output, expand concise, reviewed decisions with `prepare-review-batch`, then submit the resulting item-level reviews with `verify-review-batch`; both reuse the same quality gate. For the user-confirmed reliable batch `2026-07-19-g11-beijing-20` and high-quality exam questions imported on or after `2026-07-20`, a full independent re-solve of every question may be skipped. Use `audit-summary` for the eligible count and `audit-queue/prepare-audit-batch --simplified-only` for the eligible queue. Every item still requires checks of completeness, duplicates, answer/solution consistency, tags, provenance, and a recorded `audit-item → verify-item` review; any inconsistency restores the independent-derivation requirement. For older sources, external `verified` values, source reputation, sampling, and bulk SQL never justify verification.

## Review and progress

Use `daily-review-packet --limit 12 --out <packet.json> --json` to collapse accumulated schedules into one task per active error. Stages 1-2 include the original plus two same-level reviewed recommendations, stages 3-4 include the original plus one reviewed variation, and stages 5-6 include the original plus one reviewed recommendation with a preference for slightly higher difficulty. Only saved, reviewed, verified recommendations enter its printable section; resolve any reported recommendation gaps before PDF generation. Generate one questions-only review PDF with `practice_sheet.py --daily-packet <packet.json>`. Its layout is fixed: main numbers count error groups, each group starts with `错题编号` and `错题回顾`, and its nested exercises are labeled `同类型推荐题 1/2` with `题库编号`, difficulty, recommendation reason, and source in stable positions. A second recommendation never consumes another main number; the next main number begins only at the next error group. Do not replace this hierarchy with flat per-question numbering. Use `due --json`, `review <error-id> --result correct|partial|wrong`, `stats --json`, and `coverage --json`. Wrong/partial review starts an adaptive cycle. If the latest review itself was misgraded, use `correct-review <error-id> --result correct|partial|wrong`; it corrects that row and repairs the schedule instead of advancing a second stage. When the user explicitly confirms an error is mastered, use `master-error <error-id> --json` to retain completed review history while cancelling only its pending stages.

## Local full-text retrieval

`search` automatically uses the canonical database's SQLite FTS5 trigram index for suitable text queries and falls back to `LIKE` when unavailable or too short. `recommend` may use the same index only as a small ranking feature after the verified/knowledge/cause/attempt filters. Rebuild it with `rebuild-search-index --json`; the command creates a timestamped database backup first. Do not add a separate vector database unless measured retrieval failures remain after FTS5 and feature-based ranking.

## Invariants

- Recommend only verified questions and show source plus reason.
- Preserve provenance, license, verification status, and original import record.
- Import only authorized/open/user-owned material; never bypass authentication, paywalls, robots controls, or access limits.
- Store error reports in `errors/YYYY-MM/`, practice in `practice/`, PDFs in `output/pdf/`, and original structured imports in `data/raw/`.
- Do not expose hidden answers before submission/request, and never call model-generated content verified.
- `delete-error` refuses linked practice attempts by default; when the user explicitly removes that error, use `--detach-attempts` to preserve the attempts as standalone history rather than deleting them.
