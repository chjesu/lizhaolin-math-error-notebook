---
name: math-error-notebook
description: Manage a Chinese high-school math error notebook backed by one local verified question bank. Use for grading photographed or typed Grade 10-12 math work, diagnosing the first wrong step, saving structured errors, recommending verified practice, generating/printing practice PDFs, importing authorized questions, recording attempts, reviews, and weakness statistics.
---

# 李兆霖数学错题本

## Fast path

- Start routine work with one compact, read-only call: `scripts/notebook.py agent-context --task grade|recommend|verify|import|review|pdf|maintenance --json`. Use `doctor --json` for environment health and `handoff --json` for a compact transfer snapshot.
- Use image input directly for photos; never claim to switch models/providers.
- Run deterministic work through `scripts/notebook.py`. Its project installation binds to the sole bank `data/math_notebook.db`; never discover, merge, copy over, or select another same-named DB.
- Do not recursively enumerate `data/audits/`, `data/imports/`, or the question corpus. Use compact CLI queries.
- `--json` is compact by default. Use global `--pretty-json` only for human inspection. `search`, `recommend`, and `audit-queue` omit long solutions/raw records unless `--full` is explicit.
- Before imports or maintenance, run `bank-info --json`. Run `init` then idempotent `seed` only when the canonical bank does not exist.

## Grade work

1. Separate printed content from handwriting; reconstruct steps and identify the first substantive error.
2. If a key symbol, condition, diagram, or step is unreadable, state it and request a clearer crop. Never fabricate. Use `unclear` for insufficient evidence; use `careless` only with direct evidence.
3. Get compact codes instead of loading full catalogs:

```powershell
python -B <skill-dir>\scripts\notebook.py causes --json
python -B <skill-dir>\scripts\notebook.py knowledge --text "相关主题" --json
python -B <skill-dir>\scripts\notebook.py features --text "相切" --json
```

4. Copy `assets/error-analysis-template.json`, fill it with LaTeX, validate it, then persist:

```powershell
python -B <skill-dir>\scripts\notebook.py grade-preview <analysis.json> --json
python -B <skill-dir>\scripts\notebook.py grade-commit <analysis.json> --copy-image --json
```

5. Lead with the first wrong step, cause, correct method, prevention cue, and saved error ID. Read `references/error-taxonomy.md` only when cause selection is ambiguous; read `references/data-contract.md` only if the template or validation fails.

## Recommend and print

Preview compact, same-type verified matches with 1-3 discriminative keywords; save only after relevance review:

```powershell
python -B <skill-dir>\scripts\notebook.py recommend <error-id> --feature tangent --keyword "圆" --limit 3 --json
python -B <skill-dir>\scripts\notebook.py recommend-packet <error-id> --feature tangent --keyword "圆" --limit 3 --out <packet.json> --json
python -B <skill-dir>\scripts\notebook.py assign-recommendations <error-id> <reviewed-plan.json> --save --json
```

- Default output hides answers/solutions. Ranking uses knowledge, cause, difficulty, attempt history, and auditable structural features. Use `question <id> --json` only for shortlisted items; use `search ... --json` for compact fallback search.
- If automatic matches are weak, save a reviewed verified set with `assign-recommendations`; never substitute invented questions silently.
- If fewer than three verified matches exist, report the shortfall and offer adjacent-topic search or generated questions labeled unverified.
- After saving, create the A4 PDF (questions only by default; add `--with-answers` when the user wants an answer page) and print only when the user asks:

```powershell
python -B <skill-dir>\scripts\practice_sheet.py <error-id> --print
```

Printer/output preferences live in `config/math-error-notebook.json`. The script emits only a compact artifact summary.

After grading practice, record it with `attempt <question-id> --error-id <error-id> --correct|--wrong`; include `--cause-code` when wrong, then adapt recommendations.

## Import and verify

Read `references/import-and-verification.md` only for question import, source repair, coverage audit, or verification work. Use `prepare-audit-batch` to create per-item `audit-item` packets and safe pending review skeletons, then complete each review and call `verify-item`; the preparation command never changes verification state. To reduce repeated output, expand concise, independently reviewed decisions with `prepare-review-batch`, then submit the resulting item-level reviews with `verify-review-batch`; both reuse the same quality gate. For the user-confirmed reliable batch `2026-07-19-g11-beijing-20`, a full independent re-solve of every question may be skipped, but each item still requires checks of completeness, duplicates, answer/solution consistency, tags, provenance, and a recorded `audit-item → verify-item` review. This exception applies only to that batch. For all other sources, external `verified` values, source reputation, sampling, and bulk SQL never justify verification.

## Review and progress

Use `due --json`, `review <error-id> --result correct|partial|wrong`, `stats --json`, and `coverage --json`. Wrong/partial review starts an adaptive cycle.

## Invariants

- Recommend only verified questions and show source plus reason.
- Preserve provenance, license, verification status, and original import record.
- Import only authorized/open/user-owned material; never bypass authentication, paywalls, robots controls, or access limits.
- Store error reports in `errors/YYYY-MM/`, practice in `practice/`, PDFs in `output/pdf/`, and original structured imports in `data/raw/`.
- Do not expose hidden answers before submission/request, and never call model-generated content verified.
