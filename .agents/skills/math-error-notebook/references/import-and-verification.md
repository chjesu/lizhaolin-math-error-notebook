# Import and question-level verification

Read this file only for imports, source maintenance, coverage audits, or verification.

## Authorization

- Import only public material with an explicit license, official public releases, or material the user confirms they may use.
- Never bypass login, payment, robots controls, or other access restrictions.
- Keep source name, URL/path, license, retrieval context, and original payload.

## Import

```powershell
python -B <skill-dir>\scripts\notebook.py import-file questions.jsonl --source-name "..." --source-url "..." --license "..." --rights-confirmed --json
python -B <skill-dir>\scripts\notebook.py import-url "https://..." --source-name "..." --license "..." --rights-confirmed --json
```

External questions remain unverified by default. An imported `verified` field is ignored as evidence.

For a user-authorized date range of DOCX exams, reuse `scripts/import_recent_docx_batch.py`.
It performs source/file idempotency checks and orchestrates the existing OMML extractor,
builder, pre-import quality gate, and `import-file`; it never verifies imported questions.
The quality gate fails closed when question numbers are missing or duplicated, a question
cannot be parsed, a real solution marker/body is absent, required fields are incomplete,
math delimiters are unbalanced, or choice labels are malformed. A blocked paper is recorded
as `blocked_quality_gate` with its problems and source paragraph ranges and is not written to
the canonical database. Do not override that result; fix or re-extract the source. Then run
`scripts/audit_recent_docx_batch.py` to build all item packets and route structural
exceptions without consuming model context. Image checks cover stems, options, answers, and
solutions; diagram-dependent questions are routed to visual review rather than pure-text
simplified approval.

## Two-stage compact audit workflow

1. Check the canonical DB with `bank-info --json`.
2. Group issues with `audit-summary --json`.
3. List candidates compactly with `audit-queue --source-name "..." --limit 20 --json`.
4. Build one complete audit packet. Writing it to a file keeps the command result compact:

```powershell
python -B <skill-dir>\scripts\notebook.py audit-item <id> --out data/audits/packets/<id>.json --json
```

5. Independently verify stem, options, answer, derivation, knowledge tags, difficulty, structural features, source, and duplicates. Copy `assets/question-review-template.json`, fill every required field, and do not copy the stored answer as an “independent” derivation. Exception: the user-confirmed `2026-07-19-g11-beijing-20` batch and high-quality exam questions imported on or after `2026-07-20` may skip a full independent re-solve. They still require item-level completeness, duplicate, answer/solution-consistency, tag, feature, and provenance checks; any doubt or inconsistency must be resolved by independent derivation.
6. Submit exactly one structured review:

```powershell
python -B <skill-dir>\scripts\notebook.py verify-item <id> data/audits/reviews/<id>.json --json
```

When many item-level reviews are already complete, place their question IDs and
review paths in one manifest and call `verify-review-batch`. It invokes the same
per-item verifier and does not relax any checklist or mathematical-review rule.

To reduce repeated model output, a reviewed item may first be written in a concise
decisions file and expanded with `prepare-review-batch`. Each passing item must still
set `checks_confirmed: true`, fill the answer/solution review fields, record
answer/solution checks, and provide any corrected tags. Full-mode items use an
independent answer and derivation. Simplified-mode items may use a concise reviewed
answer and consistency basis instead of repeating a full re-solve, but must not merely
copy the stored long solution. The expander only copies deterministic question
metadata and creates canonical review files; it never changes the database.

The compact date-based route is programmatic:

```powershell
python -B <skill-dir>\scripts\notebook.py audit-summary --json
python -B <skill-dir>\scripts\notebook.py audit-queue --simplified-only --limit 20 --json
python -B <skill-dir>\scripts\notebook.py prepare-audit-batch --simplified-only --limit 20 --out-dir <dir> --json
```

`audit-item.verification_mode` is `simplified` or `full`. Simplified review reduces
the amount of repeated derivation, not the number of per-item checks or review records.

`pass` and `corrected` can promote only when every checklist item is true and an independent answer/solution is present. `needs_revision` and `reject` are logged but remain unverified. Internally, promotion uses the same field validation as `annotate --verify`.

For older records, structural features may be populated deterministically without changing verification status:

```powershell
python -B <skill-dir>\scripts\notebook.py backfill-features --json
```

Never promote a batch because a source appears trustworthy, a sample passed, or another model marked it verified. Never use bulk SQL to set verification.

Use `coverage --json` to identify curriculum gaps. “All internet questions” is not finite; optimize for curriculum and question-type coverage with transparent provenance.
