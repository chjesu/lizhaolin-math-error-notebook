"""Delegate bounded text-only notebook work to DeepSeek through safe_init.

The worker never writes the notebook database.  It emits candidate files and
runs only read-only/local preparation gates.  Database changes still go through
the next authoritative ``notebook.py`` command after review.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from math_notebook_project_paths import resolve_project_root


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = resolve_project_root(SKILL_DIR)
NOTEBOOK = SCRIPT_DIR / "notebook.py"
ERROR_TEMPLATE = SKILL_DIR / "assets/error-analysis-template.json"
DEEPSEEK_RUNTIME = PROJECT_ROOT / ".runtime/deepseek"
TASKS = ("grade", "verify", "recommend", "tag")
MIN_CONFIDENCE = {"grade": 0.9, "verify": 0.9, "recommend": 0.8, "tag": 0.85}
MAX_VERIFY_ITEMS = 20
MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]+\)")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON from {path}: {exc}") from exc


def write_json(path: Path, payload: Any, force: bool = False) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"output already exists: {path}; pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def run_notebook_json(*arguments: str) -> Any:
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-B",
        str(NOTEBOOK),
        *arguments,
        "--json",
    ]
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"notebook command failed: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"notebook returned invalid JSON: {result.stdout!r}") from exc


def load_catalogs() -> dict[str, Any]:
    return {
        "cause_codes": run_notebook_json("causes"),
        "knowledge_codes": run_notebook_json("knowledge"),
        "feature_codes": run_notebook_json("features"),
    }


def catalog_code_set(catalog: Any) -> set[str]:
    if isinstance(catalog, dict):
        catalog = catalog.get("items") or []
    if not isinstance(catalog, list):
        return set()
    return {
        str(item.get("code") or "").strip()
        for item in catalog
        if isinstance(item, dict) and str(item.get("code") or "").strip()
    }


def validate_codes(values: Any, allowed: set[str], field: str, required: bool) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field} must be an array")
    result = [str(value).strip() for value in values if str(value).strip()]
    if required and not result:
        raise ValueError(f"{field} must not be empty")
    unknown = sorted(set(result) - allowed)
    if unknown:
        raise ValueError(f"{field} contains unknown codes: {unknown}")
    return list(dict.fromkeys(result))


def confidence_value(item: dict[str, Any], minimum: float) -> float:
    try:
        confidence = float(item.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ValueError("model output requires numeric confidence") from exc
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return confidence


def parse_object(content: Any) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("DeepSeek returned empty content")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("DeepSeek did not return a plain JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("DeepSeek response must be a JSON object")
    return payload


def validate_grade_evidence(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise ValueError("grade evidence must be a JSON object")
    if not str(evidence.get("problem_text") or "").strip():
        raise ValueError("evidence.problem_text is required")
    if "student_answer" not in evidence:
        raise ValueError("evidence.student_answer is required; use an empty string if blank")
    source_type = evidence.get("source_type", "text")
    if source_type not in {"text", "vision_review"}:
        raise ValueError("evidence.source_type must be text or vision_review")
    if evidence.get("image_path") and source_type != "vision_review":
        raise ValueError("image-backed evidence must use source_type=vision_review")
    if source_type == "vision_review":
        if evidence.get("visual_review_completed") is not True:
            raise ValueError("vision_review requires visual_review_completed=true")
        visible = evidence.get("visual_evidence") or []
        if not isinstance(visible, list) or not any(str(item).strip() for item in visible):
            raise ValueError("vision_review requires non-empty visual_evidence")
    if not isinstance(evidence.get("student_work_has_steps"), bool):
        raise ValueError("evidence.student_work_has_steps must be true or false")
    return evidence


def parse_grade_candidate(
    content: Any, template: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    candidate = parse_object(content)
    expected = set(template)
    if set(candidate) != expected:
        missing = sorted(expected - set(candidate))
        extra = sorted(set(candidate) - expected)
        raise ValueError(f"DeepSeek response keys mismatch; missing={missing}, extra={extra}")
    candidate["image_path"] = evidence.get("image_path")
    candidate["question_id"] = evidence.get("question_id")
    return candidate


def grade_escalations(
    candidate: dict[str, Any], evidence: dict[str, Any], minimum: float
) -> list[dict[str, str]]:
    question_id = str(candidate.get("question_id") or "")
    reasons: list[str] = []
    if confidence_value(candidate, minimum) < minimum:
        reasons.append(f"model_confidence_below_{minimum}")
    if not evidence["student_work_has_steps"]:
        reasons.append("no_student_steps_cause_requires_human_review")
    return [{"question_id": question_id, "reason": reason} for reason in reasons]


def grade_gate_escalations(
    gate: dict[str, Any], question_id: str
) -> list[dict[str, str]]:
    return [
        {
            "question_id": question_id,
            "reason": f"grade_preview_warning:{warning}",
        }
        for warning in gate.get("warnings", [])
    ]


def _resolve_packet_path(value: Any, manifest_path: Path) -> Path:
    packet_path = Path(str(value or ""))
    if not packet_path.is_absolute():
        packet_path = manifest_path.parent / packet_path
    return packet_path.resolve()


def _visual_dependency(packet: dict[str, Any]) -> bool:
    if packet.get("visual_review_images") or packet.get("visual_evidence"):
        return True
    question = packet.get("question") or {}
    values = [
        question.get("stem"),
        question.get("stored_answer"),
        question.get("stored_solution"),
        *(question.get("options") or []),
    ]
    return any(MARKDOWN_IMAGE.search(str(value or "")) for value in values)


def load_verification_packets(
    path: Path, payload: Any
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not isinstance(payload, dict):
        raise ValueError("verification input must be an audit packet or batch manifest")
    if payload.get("schema") == "math-question-audit-packet/v1":
        packets = [payload]
    elif payload.get("schema") == "math-audit-work-batch/v1":
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("audit manifest requires an items array")
        packets = [read_json(_resolve_packet_path(item.get("packet"), path)) for item in items]
    else:
        raise ValueError("unsupported verification packet schema")
    if len(packets) > MAX_VERIFY_ITEMS:
        raise ValueError(f"verification batch exceeds {MAX_VERIFY_ITEMS} items; split it")

    eligible: list[dict[str, Any]] = []
    escalations: list[dict[str, str]] = []
    for packet in packets:
        question_id = str(((packet.get("question") or {}).get("id")) or "").strip()
        if not question_id:
            raise ValueError("audit packet question.id is required")
        if _visual_dependency(packet):
            escalations.append({
                "question_id": question_id,
                "reason": "visual_dependency_requires_remote_review",
            })
        else:
            eligible.append(packet)
    return eligible, escalations


def compact_audit_packet(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "verification_mode": packet.get("verification_mode"),
        "question": packet.get("question"),
        "source_record": packet.get("source_record"),
        "automatic_checks": packet.get("automatic_checks"),
        "required_review": packet.get("required_review"),
        "packet_sha256": packet.get("packet_sha256"),
    }


def validate_recommendation_packet(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != "math-recommendation-review-packet/v1":
        raise ValueError("recommend task requires math-recommendation-review-packet/v1")
    if not str(payload.get("error_id") or "").strip():
        raise ValueError("recommendation packet error_id is required")
    if not isinstance(payload.get("items"), list) or not payload["items"]:
        raise ValueError("recommendation packet must contain candidates")
    return payload


def normalize_tag_input(payload: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if not isinstance(payload, dict):
        raise ValueError("tag input must be a JSON object")
    packet = payload if payload.get("schema") == "math-question-audit-packet/v1" else None
    question = (packet or {}).get("question") or payload.get("question") or payload
    if not isinstance(question, dict) or not str(question.get("stem") or "").strip():
        raise ValueError("tag input requires question.stem")
    question_id = str(question.get("id") or payload.get("question_id") or "").strip()
    if packet and _visual_dependency(packet):
        return question, [{
            "question_id": question_id,
            "reason": "visual_dependency_requires_remote_review",
        }]
    if MARKDOWN_IMAGE.search(str(question.get("stem") or "")):
        return question, [{
            "question_id": question_id,
            "reason": "visual_dependency_requires_remote_review",
        }]
    return question, []


def build_messages(task: str, data: Any, catalogs: dict[str, Any]) -> list[dict[str, str]]:
    common = [
        "Output exactly one plain JSON object with no Markdown wrapper.",
        "Never claim to have seen an image; visual evidence is handled by another model.",
        "Never request database access or invent question IDs, paths, sources, or code values.",
    ]
    if task == "grade":
        contract = {
            "task": "grade typed work or trusted visual transcription",
            "output_template": read_json(ERROR_TEMPLATE),
            "allowed_codes": catalogs,
            "rules": common + [
                "Return exactly the template keys.",
                "Use LaTeX and preserve the complete problem and student work.",
                "Identify the first substantive error, not a later consequence.",
                "Use careless only with direct evidence; use unclear when evidence is insufficient.",
                "If student_work_has_steps=false, do not infer a specific cause or hidden step from the final answer.",
            ],
        }
    elif task == "verify":
        contract = {
            "task": "review text-only question audit packets",
            "output_shape": {
                "items": [{
                    "question_id": "Q-...",
                    "verdict": "pass|corrected|needs_revision|reject",
                    "checks_confirmed": True,
                    "independent_answer": "LaTeX answer",
                    "independent_solution": "reviewed derivation",
                    "answer_check": "match|corrected",
                    "solution_check": "match|corrected|completed",
                    "knowledge_codes": ["code"],
                    "target_causes": ["code"],
                    "feature_codes": ["code"],
                    "grade": 11,
                    "difficulty": 3.0,
                    "question_type": "解答题",
                    "correction": {},
                    "review_note": "evidence and limitations",
                    "confidence": 0.95,
                }]
            },
            "allowed_codes": catalogs,
            "rules": common + [
                "Return one item for every supplied question ID and no others.",
                "Full mode requires an independent derivation; simplified mode still checks every required item.",
                "Any ambiguity, missing condition, possible duplicate conflict, or answer inconsistency must not pass.",
                "Pass or corrected requires every field in the output shape and checks_confirmed=true.",
            ],
        }
    elif task == "recommend":
        contract = {
            "task": "select truly similar practice from supplied verified candidates",
            "output_shape": {"items": [{
                "question_id": "Q-...",
                "score": 9.5,
                "reason": "same cause, structure and difficulty",
                "confidence": 0.9,
            }]},
            "rules": common + [
                "Select only supplied IDs; omission means rejection.",
                "Prefer matching error cause and solution structure over shared topic words.",
                "Return at most the requested limit, best match first, and explain each reason.",
            ],
        }
    else:
        contract = {
            "task": "suggest metadata for one text-only question",
            "output_shape": {
                "knowledge_codes": ["code"],
                "target_causes": ["code"],
                "feature_codes": ["code"],
                "grade": 11,
                "difficulty": 3.0,
                "question_type": "解答题",
                "review_note": "short basis",
                "confidence": 0.9,
            },
            "allowed_codes": catalogs,
            "rules": common + [
                "Choose only listed codes and do not alter the question text or answer.",
                "Use difficulty from 1 to 5 and grade 10, 11 or 12.",
            ],
        }
    return [
        {
            "role": "system",
            "content": "You are a bounded Chinese high-school math notebook reviewer. "
            + json.dumps(contract, ensure_ascii=False, separators=(",", ":")),
        },
        {"role": "user", "content": json.dumps(data, ensure_ascii=False, separators=(",", ":"))},
    ]


def choose_thinking(task: str, mode: str) -> bool:
    if mode == "on":
        return True
    if mode == "off":
        return False
    return task in {"grade", "verify"}


def choose_model(task: str, requested: str | None) -> str:
    if requested:
        return requested
    if os.getenv("DEEPSEEK_MODEL"):
        return str(os.environ["DEEPSEEK_MODEL"])
    if task in {"recommend", "tag"}:
        return os.getenv("DEEPSEEK_FAST_MODEL", "deepseek-v4-flash")
    return os.getenv("DEEPSEEK_REASONING_MODEL", "deepseek-v4-pro")


def call_deepseek(
    task: str, data: Any, catalogs: dict[str, Any], model: str, thinking: bool
) -> tuple[str, dict[str, Any]]:
    if not os.getenv("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    if DEEPSEEK_RUNTIME.is_dir():
        sys.path.insert(0, str(DEEPSEEK_RUNTIME))
    try:
        from safe_init import safe_deepseek_call
    except ModuleNotFoundError as exc:
        if exc.name == "openai":
            raise RuntimeError(
                "missing dependency; install scripts/requirements-deepseek.txt "
                "into the project .runtime/deepseek directory"
            ) from exc
        raise
    max_tokens = {
        "grade": 4096,
        "verify": min(16_384, max(4096, len(data) * 1600)),
        "recommend": 2048,
        "tag": 2048,
    }[task]
    response = safe_deepseek_call(
        model=model,
        enable_thinking=thinking,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=build_messages(task, data, catalogs),
    )
    return response.get("content"), response.get("_dsk_usage") or {}


def parse_verify_candidate(
    content: Any,
    packets: list[dict[str, Any]],
    catalogs: dict[str, Any],
    minimum: float,
    reviewer: str,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload = parse_object(content)
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("verify response requires an items array")
    packet_map = {
        str(packet["question"]["id"]): packet
        for packet in packets
    }
    seen: set[str] = set()
    accepted: list[dict[str, Any]] = []
    escalations: list[dict[str, str]] = []
    knowledge = catalog_code_set(catalogs["knowledge_codes"])
    causes = catalog_code_set(catalogs["cause_codes"])
    features = catalog_code_set(catalogs["feature_codes"])
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("each verify item must be an object")
        question_id = str(raw.get("question_id") or "").strip()
        if question_id not in packet_map or question_id in seen:
            raise ValueError(f"unexpected or duplicate verification ID: {question_id}")
        seen.add(question_id)
        confidence = confidence_value(raw, minimum)
        if confidence < minimum:
            escalations.append({
                "question_id": question_id,
                "reason": f"model_confidence_below_{minimum}",
            })
            continue
        verdict = str(raw.get("verdict") or "").strip()
        if verdict not in {"pass", "corrected", "needs_revision", "reject"}:
            raise ValueError(f"{question_id}: invalid verdict")
        decision: dict[str, Any] = {
            "question_id": question_id,
            "verdict": verdict,
            "packet_sha256": str(packet_map[question_id].get("packet_sha256") or ""),
            "review_note": str(raw.get("review_note") or "").strip(),
            "confidence": confidence,
        }
        if not re.fullmatch(r"[0-9a-f]{64}", decision["packet_sha256"]):
            raise ValueError(f"{question_id}: audit packet_sha256 is required")
        if verdict in {"pass", "corrected"}:
            if raw.get("checks_confirmed") is not True:
                raise ValueError(f"{question_id}: checks_confirmed must be true")
            for field in ("independent_answer", "independent_solution"):
                if not str(raw.get(field) or "").strip():
                    raise ValueError(f"{question_id}: {field} is required")
            if raw.get("answer_check") not in {"match", "corrected"}:
                raise ValueError(f"{question_id}: invalid answer_check")
            if raw.get("solution_check") not in {"match", "corrected", "completed"}:
                raise ValueError(f"{question_id}: invalid solution_check")
            try:
                grade = int(raw.get("grade"))
                difficulty = float(raw.get("difficulty"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{question_id}: grade and difficulty are required") from exc
            if grade not in {10, 11, 12} or not 1 <= difficulty <= 5:
                raise ValueError(f"{question_id}: grade or difficulty is out of range")
            question_type = str(raw.get("question_type") or "").strip()
            if not question_type:
                raise ValueError(f"{question_id}: question_type is required")
            correction = raw.get("correction") or {}
            if not isinstance(correction, dict):
                raise ValueError(f"{question_id}: correction must be an object")
            decision.update({
                "checks_confirmed": True,
                "independent_answer": str(raw["independent_answer"]).strip(),
                "independent_solution": str(raw["independent_solution"]).strip(),
                "answer_check": raw["answer_check"],
                "solution_check": raw["solution_check"],
                "knowledge_codes": validate_codes(
                    raw.get("knowledge_codes"), knowledge, "knowledge_codes", True
                ),
                "target_causes": validate_codes(
                    raw.get("target_causes"), causes, "target_causes", False
                ),
                "feature_codes": validate_codes(
                    raw.get("feature_codes"), features, "feature_codes", False
                ),
                "grade": grade,
                "difficulty": difficulty,
                "question_type": question_type,
                "correction": correction,
            })
        accepted.append(decision)
    for question_id in sorted(set(packet_map) - seen):
        escalations.append({"question_id": question_id, "reason": "model_omitted_item"})
    return {"reviewer": reviewer, "items": accepted}, escalations


def parse_recommend_candidate(
    content: Any,
    packet: dict[str, Any],
    minimum: float,
    limit: int,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload = parse_object(content)
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("recommend response requires an items array")
    candidates = {str(item.get("question_id")): item for item in packet["items"]}
    selected: list[dict[str, Any]] = []
    escalations: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("each recommendation must be an object")
        question_id = str(raw.get("question_id") or "").strip()
        if question_id not in candidates or question_id in seen:
            raise ValueError(f"unexpected or duplicate recommendation ID: {question_id}")
        seen.add(question_id)
        confidence = confidence_value(raw, minimum)
        if confidence < minimum:
            escalations.append({
                "question_id": question_id,
                "reason": f"model_confidence_below_{minimum}",
            })
            continue
        reason = str(raw.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"{question_id}: recommendation reason is required")
        try:
            score = float(raw.get("score", candidates[question_id].get("score", 9.0)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{question_id}: invalid score") from exc
        if not 0 <= score <= 10:
            raise ValueError(f"{question_id}: score must be between 0 and 10")
        selected.append({
            "question_id": question_id,
            "score": score,
            "reason": reason,
            "model_confidence": confidence,
        })
        if len(selected) >= limit:
            break
    return {
        "schema": "math-reviewed-recommendations/v1",
        "error_id": packet["error_id"],
        "reviewer": "deepseek-harness",
        "items": selected,
    }, escalations


def parse_tag_candidate(
    content: Any,
    catalogs: dict[str, Any],
    minimum: float,
    question_id: str,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    raw = parse_object(content)
    confidence = confidence_value(raw, minimum)
    if confidence < minimum:
        return None, [{
            "question_id": question_id,
            "reason": f"model_confidence_below_{minimum}",
        }]
    try:
        grade = int(raw.get("grade"))
        difficulty = float(raw.get("difficulty"))
    except (TypeError, ValueError) as exc:
        raise ValueError("tag output requires grade and difficulty") from exc
    if grade not in {10, 11, 12} or not 1 <= difficulty <= 5:
        raise ValueError("tag grade or difficulty is out of range")
    question_type = str(raw.get("question_type") or "").strip()
    if not question_type:
        raise ValueError("tag question_type is required")
    return {
        "schema": "math-question-tag-candidate/v1",
        "question_id": question_id or None,
        "knowledge_codes": validate_codes(
            raw.get("knowledge_codes"),
            catalog_code_set(catalogs["knowledge_codes"]),
            "knowledge_codes",
            True,
        ),
        "target_causes": validate_codes(
            raw.get("target_causes"),
            catalog_code_set(catalogs["cause_codes"]),
            "target_causes",
            False,
        ),
        "feature_codes": validate_codes(
            raw.get("feature_codes"),
            catalog_code_set(catalogs["feature_codes"]),
            "feature_codes",
            False,
        ),
        "grade": grade,
        "difficulty": difficulty,
        "question_type": question_type,
        "review_note": str(raw.get("review_note") or "").strip(),
        "confidence": confidence,
        "database_modified": False,
    }, []


def run_task(args: argparse.Namespace) -> dict[str, Any]:
    if args.input is None or args.out is None:
        raise ValueError("input and --out are required unless --self-test is used")
    args.input = (
        args.input.resolve()
        if args.input.is_absolute()
        else (PROJECT_ROOT / args.input).resolve()
    )
    args.out = (
        args.out.resolve()
        if args.out.is_absolute()
        else (PROJECT_ROOT / args.out).resolve()
    )
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    payload = read_json(args.input)
    catalogs = load_catalogs()
    model = choose_model(args.task, args.model)
    thinking = choose_thinking(args.task, args.thinking)
    minimum = args.min_confidence or MIN_CONFIDENCE[args.task]
    if not 0 < minimum <= 1:
        raise ValueError("--min-confidence must be between 0 and 1")
    escalations: list[dict[str, str]] = []

    if args.task == "grade":
        evidence = validate_grade_evidence(payload)
        content, usage = call_deepseek("grade", evidence, catalogs, model, thinking)
        candidate = parse_grade_candidate(content, read_json(ERROR_TEMPLATE), evidence)
        escalations.extend(grade_escalations(candidate, evidence, minimum))
        write_json(args.out, candidate, args.force)
        gate = run_notebook_json("grade-preview", str(args.out))
        escalations.extend(
            grade_gate_escalations(
                gate, str(candidate.get("question_id") or "")
            )
        )
        next_command = None if escalations else (
            "notebook.py grade-commit " + str(args.out) + " --copy-image --json"
        )
    elif args.task == "verify":
        packets, escalations = load_verification_packets(args.input, payload)
        usage = {}
        if packets:
            data = [compact_audit_packet(packet) for packet in packets]
            content, usage = call_deepseek("verify", data, catalogs, model, thinking)
            candidate, model_escalations = parse_verify_candidate(
                content, packets, catalogs, minimum, f"deepseek-harness/{model}"
            )
            escalations.extend(model_escalations)
        else:
            candidate = {"reviewer": f"deepseek-harness/{model}", "items": []}
        candidate["escalations"] = escalations
        write_json(args.out, candidate, args.force)
        if candidate["items"]:
            review_dir = args.out.parent / f"{args.out.stem}-reviews"
            gate = run_notebook_json(
                "prepare-review-batch", str(args.out), "--out-dir", str(review_dir.resolve())
            )
            next_command = "notebook.py verify-review-batch " + gate["manifest"] + " --json"
        else:
            gate = {"prepared": 0, "database_modified": False}
            next_command = None
    elif args.task == "recommend":
        packet = validate_recommendation_packet(payload)
        model_input = {
            "error_id": packet["error_id"],
            "requested_limit": args.limit,
            "items": packet["items"],
        }
        content, usage = call_deepseek("recommend", model_input, catalogs, model, thinking)
        candidate, escalations = parse_recommend_candidate(
            content, packet, minimum, args.limit
        )
        candidate["escalations"] = escalations
        write_json(args.out, candidate, args.force)
        gate = {"candidate_ids_validated": True, "database_modified": False}
        next_command = (
            f"notebook.py assign-recommendations {packet['error_id']} "
            f"{args.out} --save --json"
            if candidate["items"] else None
        )
    else:
        question, escalations = normalize_tag_input(payload)
        usage = {}
        question_id = str(question.get("id") or payload.get("question_id") or "").strip()
        if escalations:
            candidate = None
        else:
            content, usage = call_deepseek("tag", question, catalogs, model, thinking)
            candidate, model_escalations = parse_tag_candidate(
                content, catalogs, minimum, question_id
            )
            escalations.extend(model_escalations)
        output = candidate or {
            "schema": "math-question-tag-candidate/v1",
            "question_id": question_id or None,
            "database_modified": False,
        }
        output["escalations"] = escalations
        write_json(args.out, output, args.force)
        gate = {"catalog_codes_validated": candidate is not None, "database_modified": False}
        next_command = "review candidate, then use notebook.py annotate" if candidate else None

    return {
        "status": "candidate_ready" if not escalations else "review_required",
        "task": args.task,
        "model": model,
        "thinking": thinking,
        "candidate_path": str(args.out),
        "local_gate": gate,
        "escalations": escalations,
        "usage": usage,
        "database_modified": False,
        "next_command": next_command,
    }


def self_test() -> dict[str, Any]:
    evidence = validate_grade_evidence({
        "source_type": "text",
        "problem_text": "解方程 $x+1=2$。",
        "student_answer": "$x=2$",
        "student_work_has_steps": True,
    })
    template = read_json(ERROR_TEMPLATE)
    candidate = dict(template)
    candidate.update({
        "problem_text": evidence["problem_text"],
        "student_answer": evidence["student_answer"],
        "correct_answer": "$x=1$",
        "correct_solution": "两边同时减去 1，得 $x=1$。",
        "first_wrong_step": "学生未将常数 1 移至等号右侧。",
        "cause_code": "algebra_transform",
        "cause_detail": "移项错误。",
        "evidence": ["从 $x+1=2$ 直接写成 $x=2$。"],
        "knowledge_codes": ["algebra-operations"],
        "feature_codes": ["solution"],
        "question_type": "解答题",
        "difficulty": 1.0,
        "confidence": 0.99,
    })
    parsed = parse_grade_candidate(
        json.dumps(candidate, ensure_ascii=False), template, evidence
    )
    preview_path = PROJECT_ROOT / ".tmp/deepseek-worker-self-test.json"
    write_json(preview_path, parsed, force=True)
    try:
        preview = run_notebook_json("grade-preview", str(preview_path))
    finally:
        preview_path.unlink(missing_ok=True)
    if not preview.get("valid") or preview.get("database_modified") is not False:
        raise AssertionError(f"unexpected grade-preview result: {preview}")
    return {
        "ok": True,
        "tasks": list(TASKS),
        "database_modified": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delegate bounded text-only notebook tasks to DeepSeek."
    )
    parser.add_argument("input", type=Path, nargs="?")
    parser.add_argument("--task", choices=TASKS, default="grade")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--thinking", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--min-confidence", type=float)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = self_test() if args.self_test else run_task(args)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (FileExistsError, RuntimeError, ValueError, AssertionError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
