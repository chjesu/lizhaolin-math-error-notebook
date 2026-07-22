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
builder, and `import-file`; it never verifies imported questions. Then run
`scripts/audit_recent_docx_batch.py` to build all item packets and route structural
exceptions without consuming model context.

## Two-stage compact audit workflow

1. Check the canonical DB with `bank-info --json`.
2. Group issues with `audit-summary --json`.
3. List candidates compactly with `audit-queue --source-name "..." --limit 20 --json`.
4. Build one complete audit packet. Writing it to a file keeps the command result compact:

```powershell
python -B <skill-dir>\scripts\notebook.py audit-item <id> --out data/audits/packets/<id>.json --json
```

5. Independently verify stem, options, answer, derivation, knowledge tags, difficulty, structural features, source, and duplicates. Copy `assets/question-review-template.json`, fill every required field, and do not copy the stored answer as an “independent” derivation.
6. Submit exactly one structured review:

```powershell
python -B <skill-dir>\scripts\notebook.py verify-item <id> data/audits/reviews/<id>.json --json
```

When many item-level reviews are already complete, place their question IDs and
review paths in one manifest and call `verify-review-batch`. It invokes the same
per-item verifier and does not relax any checklist or mathematical-review rule.

`pass` and `corrected` can promote only when every checklist item is true and an independent answer/solution is present. `needs_revision` and `reject` are logged but remain unverified. Internally, promotion uses the same field validation as `annotate --verify`.

For older records, structural features may be populated deterministically without changing verification status:

```powershell
python -B <skill-dir>\scripts\notebook.py backfill-features --json
```

Never promote a batch because a source appears trustworthy, a sample passed, or another model marked it verified. Never use bulk SQL to set verification.

Use `coverage --json` to identify curriculum gaps. “All internet questions” is not finite; optimize for curriculum and question-type coverage with transparent provenance.
