#!/usr/bin/env python3
"""Image preflight for photographed math work.

The default route only normalizes image size for direct review by a remote
vision-capable grading model.  The slower OCR/local-VLM route remains available
as an explicit diagnostic fallback.  OCR text must never replace inspection of
mathematical notation, diagrams, or handwritten work.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = SKILL_DIR.parents[2]
OCR_RUNTIME = PROJECT_ROOT / ".runtime" / "ocr"
OCR_REQUIREMENTS = PROJECT_ROOT / "requirements-ocr.txt"
PADDLE_WORKER = SCRIPT_DIR / "paddle_formula_worker.py"
PADDLE_FORMULA_MODEL = "PP-FormulaNet_plus-M"
LOCAL_VLM_CLIENT = SCRIPT_DIR / "local_vlm_client.py"
LOCAL_VLM_PROMPT_VERSION = "math-photo-evidence-v1"
LOCAL_VLM_SCHEMA_VERSION = "1"
LOCAL_VLM_PROMPT_PATH = SKILL_DIR / "assets" / "local-vlm-transcription-prompt.txt"
LOCAL_VLM_MAX_RESPONSE_BYTES = 100_000
OCR_LOCK_TIMEOUT_SECONDS = 900.0
OCR_ONNX_MAX_THREADS = 4
OCR_SHARED_LOCK_ENV = "LIZHAOLIN_OCR_SHARED_LOCK"
REMOTE_PREVIEW_DEFAULT_MAX_SIDE = 2400
REMOTE_PREVIEW_DEFAULT_SIDE = 2000
REMOTE_PREVIEW_JPEG_QUALITY = 88
BUNDLED_PYTHON = (
    Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime"
    / "dependencies" / "python" / "python.exe"
)
MATH_CUE = re.compile(
    r"(?:[=<>≤≥±×÷√∑∫∞^_]|\\(?:frac|sqrt|sin|cos|tan|log|ln)|"
    r"\b(?:sin|cos|tan|log|ln)\b|[A-Za-z]\s*[+\-*/=])"
)
LOCAL_VLM_FORBIDDEN_KEYS = {
    "verdict",
    "correct_answer",
    "standard_answer",
    "solution",
    "reasoning",
    "analysis",
    "error_cause",
    "first_wrong_step",
}


def local_vlm_contract() -> dict[str, Any]:
    """Return the versioned local-vision transcription contract."""
    if not LOCAL_VLM_PROMPT_PATH.is_file():
        raise RuntimeError(f"local VLM prompt is missing: {LOCAL_VLM_PROMPT_PATH}")
    return {
        "schema_version": LOCAL_VLM_SCHEMA_VERSION,
        "prompt_version": LOCAL_VLM_PROMPT_VERSION,
        "prompt_path": str(LOCAL_VLM_PROMPT_PATH.resolve()),
        "role": "visual_transcription_only",
        "consumer": "DeepSeek/Codex grading model",
        "required_top_level_keys": [
            "schema_version",
            "prompt_version",
            "source_sha256",
            "question_blocks",
            "warnings",
            "requires_visual_confirmation",
        ],
        "forbidden_grading_keys": sorted(LOCAL_VLM_FORBIDDEN_KEYS),
        "quality_rule": (
            "The local model may transcribe visible evidence only; mathematical "
            "judgment remains with the grading model."
        ),
    }


def _require_string_or_none(value: Any, field: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")


def _reject_forbidden_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        forbidden = LOCAL_VLM_FORBIDDEN_KEYS.intersection(value)
        if forbidden:
            names = ", ".join(sorted(forbidden))
            raise ValueError(f"local VLM output contains forbidden grading key(s) at {path}: {names}")
        for key, item in value.items():
            _reject_forbidden_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden_keys(item, f"{path}[{index}]")


def _normalized_compare_text(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value).lower()


def validate_local_vlm_response(
    payload: Any,
    *,
    expected_source_sha256: str | None = None,
    rapidocr_text: str | None = None,
) -> dict[str, Any]:
    """Validate untrusted local-VLM JSON and return compact grading evidence."""
    if not isinstance(payload, dict):
        raise ValueError("local VLM response must be one JSON object")
    _reject_forbidden_keys(payload)
    required = set(local_vlm_contract()["required_top_level_keys"])
    missing = sorted(required.difference(payload))
    if missing:
        raise ValueError("local VLM response is missing: " + ", ".join(missing))
    if payload.get("schema_version") != LOCAL_VLM_SCHEMA_VERSION:
        raise ValueError("unsupported local VLM schema_version")
    if payload.get("prompt_version") != LOCAL_VLM_PROMPT_VERSION:
        raise ValueError("unexpected local VLM prompt_version")
    source_sha256 = payload.get("source_sha256")
    if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256):
        raise ValueError("source_sha256 must contain 64 hexadecimal characters")
    if expected_source_sha256 and source_sha256.lower() != expected_source_sha256.lower():
        raise ValueError("local VLM response source_sha256 does not match the photo")
    rotation = payload.get("rotation_degrees_ccw", 0)
    if rotation not in {0, 90, 180, 270}:
        raise ValueError("rotation_degrees_ccw must be 0, 90, 180, or 270")
    warnings = payload.get("warnings")
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        raise ValueError("warnings must be an array of strings")
    blocks = payload.get("question_blocks")
    if not isinstance(blocks, list):
        raise ValueError("question_blocks must be an array")

    visual_reasons: list[str] = []
    compact_blocks: list[dict[str, Any]] = []
    printed_fragments: list[str] = []
    for block_index, block in enumerate(blocks):
        field = f"question_blocks[{block_index}]"
        if not isinstance(block, dict):
            raise ValueError(f"{field} must be an object")
        for name in ("printed_question_number", "printed_stem", "student_final_answer"):
            _require_string_or_none(block.get(name), f"{field}.{name}")
        stem = block.get("printed_stem")
        if stem:
            printed_fragments.append(stem)
        options = block.get("printed_options", [])
        if not isinstance(options, list):
            raise ValueError(f"{field}.printed_options must be an array")
        for option_index, option in enumerate(options):
            if not isinstance(option, dict) or not isinstance(option.get("label"), str) or not isinstance(option.get("text"), str):
                raise ValueError(f"{field}.printed_options[{option_index}] must contain string label and text")
            printed_fragments.extend((option["label"], option["text"]))
        work = block.get("student_work", [])
        if not isinstance(work, list):
            raise ValueError(f"{field}.student_work must be an array")
        for step_index, step in enumerate(work):
            if not isinstance(step, dict):
                raise ValueError(f"{field}.student_work[{step_index}] must be an object")
            if not isinstance(step.get("order"), int) or not isinstance(step.get("text"), str):
                raise ValueError(f"{field}.student_work[{step_index}] requires integer order and string text")
            if step.get("certainty") not in {"clear", "uncertain"}:
                raise ValueError(f"{field}.student_work[{step_index}].certainty must be clear or uncertain")
            if step["certainty"] == "uncertain":
                visual_reasons.append("uncertain_handwriting")
        unclear = block.get("unclear_items", [])
        if not isinstance(unclear, list) or not all(isinstance(item, str) for item in unclear):
            raise ValueError(f"{field}.unclear_items must be an array of strings")
        if unclear:
            visual_reasons.append("unclear_items")
        diagram = block.get("diagram", {"present": False, "visible_facts": []})
        if not isinstance(diagram, dict) or not isinstance(diagram.get("present"), bool):
            raise ValueError(f"{field}.diagram must contain boolean present")
        facts = diagram.get("visible_facts", [])
        if not isinstance(facts, list) or not all(isinstance(item, str) for item in facts):
            raise ValueError(f"{field}.diagram.visible_facts must be an array of strings")
        compact_blocks.append(
            {
                "printed_question_number": block.get("printed_question_number"),
                "printed_stem": stem,
                "printed_options": options,
                "student_work": work,
                "student_final_answer": block.get("student_final_answer"),
                "diagram": {"present": diagram["present"], "visible_facts": facts},
                "unclear_items": unclear,
            }
        )

    if warnings:
        visual_reasons.append("model_warnings")
    if rapidocr_text:
        left = _normalized_compare_text("".join(printed_fragments))
        right = _normalized_compare_text(rapidocr_text)
        if len(left) >= 12 and len(right) >= 12:
            agreement = SequenceMatcher(None, left, right).ratio()
            if agreement < 0.18:
                visual_reasons.append("rapidocr_disagreement")
        else:
            agreement = None
    else:
        agreement = None
    declared_visual = payload.get("requires_visual_confirmation")
    if not isinstance(declared_visual, bool):
        raise ValueError("requires_visual_confirmation must be boolean")
    if declared_visual:
        visual_reasons.append("model_requested_visual_review")
    visual_reasons = sorted(set(visual_reasons))
    requires_visual = bool(visual_reasons)
    return {
        "status": "ok",
        "schema_version": LOCAL_VLM_SCHEMA_VERSION,
        "prompt_version": LOCAL_VLM_PROMPT_VERSION,
        "source_sha256": source_sha256.lower(),
        "rotation_degrees_ccw": rotation,
        "question_blocks": compact_blocks,
        "warnings": warnings,
        "rapidocr_agreement": round(agreement, 4) if agreement is not None else None,
        "quality_gate": "visual_review_required" if requires_visual else "pass",
        "requires_visual_confirmation": requires_visual,
        "visual_review_reasons": visual_reasons,
        "database_modified": False,
    }


def validate_local_vlm_response_file(
    response_path: Path,
    *,
    packet_path: Path | None = None,
    page_number: int = 1,
) -> dict[str, Any]:
    """Load and validate one local-VLM response against an OCR packet page."""
    response_path = response_path.resolve()
    if not response_path.is_file():
        raise ValueError(f"local VLM response not found: {response_path}")
    if response_path.stat().st_size > LOCAL_VLM_MAX_RESPONSE_BYTES:
        raise ValueError("local VLM response exceeds the 100 KB safety limit")
    raw = response_path.read_text(encoding="utf-8-sig").strip()
    if raw.startswith("```") or raw.endswith("```") or "<think>" in raw.lower():
        raise ValueError("local VLM response must contain JSON only, without fences or thinking text")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"local VLM response is not valid JSON: {exc.msg}") from exc

    expected_sha256: str | None = None
    rapidocr_text: str | None = None
    if packet_path is not None:
        packet_path = packet_path.resolve()
        if not packet_path.is_file():
            raise ValueError(f"OCR packet not found: {packet_path}")
        packet = json.loads(packet_path.read_text(encoding="utf-8-sig"))
        pages = packet.get("pages") or []
        matches = [page for page in pages if page.get("page") == page_number]
        if len(matches) != 1:
            raise ValueError(f"OCR packet does not contain page {page_number}")
        expected_sha256 = matches[0].get("source_sha256")
        rapidocr_text = matches[0].get("ocr_text")
    result = validate_local_vlm_response(
        payload,
        expected_source_sha256=expected_sha256,
        rapidocr_text=rapidocr_text,
    )
    result["response_path"] = str(response_path)
    result["ocr_packet"] = str(packet_path) if packet_path else None
    result["page"] = page_number if packet_path else None
    return result


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _rapidocr_thread_count() -> int:
    cpu_count = os.cpu_count() or 1
    return max(1, min(OCR_ONNX_MAX_THREADS, cpu_count // 2 or 1))


def ocr_lock_path(project_root: Path = PROJECT_ROOT) -> Path:
    """Return the machine-wide OCR lock shared by all three notebooks."""
    del project_root  # Kept for API compatibility with runtime-status callers.
    override = os.environ.get(OCR_SHARED_LOCK_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{OCR_SHARED_LOCK_ENV} must be an absolute path")
        return path
    base = Path(tempfile.gettempdir())
    return base / "LiZhaolinErrorNotebooks" / "locks" / "photo-ocr.lock"


class OCRLockTimeout(RuntimeError):
    pass


class InterProcessFileLock:
    """Cross-platform process lock released automatically on process exit."""

    def __init__(
        self,
        path: Path,
        timeout_seconds: float = OCR_LOCK_TIMEOUT_SECONDS,
        poll_seconds: float = 0.2,
    ) -> None:
        self.path = path
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.poll_seconds = max(0.02, float(poll_seconds))
        self.wait_seconds = 0.0
        self._stream: Any = None

    @staticmethod
    def _ensure_lock_byte(stream: Any) -> None:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()

    @staticmethod
    def _try_lock(stream: Any) -> None:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(stream: Any) -> None:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> "InterProcessFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a+b")
        self._ensure_lock_byte(self._stream)
        started = time.monotonic()
        deadline = started + self.timeout_seconds
        while True:
            try:
                self._try_lock(self._stream)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    self._stream.close()
                    self._stream = None
                    raise OCRLockTimeout(
                        "OCR is busy in another notebook session; "
                        f"waited {self.timeout_seconds:.1f}s for {self.path}"
                    ) from exc
                time.sleep(
                    min(
                        self.poll_seconds,
                        max(0.0, deadline - time.monotonic()),
                    )
                )
        self.wait_seconds = round(time.monotonic() - started, 3)
        owner = json.dumps(
            {"pid": os.getpid(), "acquired_at": now_iso()},
            ensure_ascii=True,
        ).encode("ascii")
        self._stream.seek(1)
        self._stream.truncate()
        self._stream.write(owner)
        self._stream.flush()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._stream is None:
            return
        try:
            self._unlock(self._stream)
        finally:
            self._stream.close()
            self._stream = None


def bundled_python() -> Path | None:
    return BUNDLED_PYTHON if BUNDLED_PYTHON.is_file() else None


def _running_bundled_python() -> bool:
    python = bundled_python()
    return bool(python and Path(sys.executable).resolve() == python.resolve())


def _add_runtime() -> None:
    if OCR_RUNTIME.is_dir() and str(OCR_RUNTIME) not in sys.path:
        sys.path.insert(0, str(OCR_RUNTIME))


def ocr_runtime_status(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    runtime = project_root / ".runtime" / "ocr"
    rapidocr = runtime / "rapidocr"
    onnxruntime = runtime / "onnxruntime"
    models = rapidocr / "models"
    model_files = sorted(models.glob("*.onnx")) if models.is_dir() else []
    return {
        "available": rapidocr.is_dir() and onnxruntime.is_dir() and len(model_files) >= 2,
        "runtime": str(runtime.resolve()),
        "rapidocr": rapidocr.is_dir(),
        "onnxruntime": onnxruntime.is_dir(),
        "models": len(model_files),
        "concurrency": {
            "strategy": "cross_process_file_lock",
            "scope": "machine_wide_shared_across_subjects",
            "lock_path": str(ocr_lock_path(project_root).resolve()),
            "override_env": OCR_SHARED_LOCK_ENV,
            "wait_timeout_seconds": OCR_LOCK_TIMEOUT_SECONDS,
            "onnx_threads": _rapidocr_thread_count(),
        },
    }


def paddle_formula_runtime_status(
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    runtime = project_root / ".runtime" / "paddleocr"
    worker = (
        project_root
        / ".agents"
        / "skills"
        / "math-error-notebook"
        / "scripts"
        / "paddle_formula_worker.py"
    )
    model_dir = (
        project_root
        / ".runtime"
        / "paddle-home"
        / "paddlex"
        / "official_models"
        / PADDLE_FORMULA_MODEL
    )
    packages = {
        "paddle": (runtime / "paddle").is_dir(),
        "paddleocr": (runtime / "paddleocr").is_dir(),
        "paddlex": (runtime / "paddlex").is_dir(),
        "formula_dependencies": (runtime / "latex2mathml").is_dir(),
    }
    return {
        "available": all(packages.values()) and worker.is_file(),
        "runtime": str(runtime.resolve()),
        "model": PADDLE_FORMULA_MODEL,
        "model_cached": model_dir.is_dir()
        and any(model_dir.glob("*.json"))
        and any(model_dir.glob("*.pdiparams")),
        "packages": packages,
        "worker": worker.is_file(),
        "purpose": "assistive_formula_ocr",
    }


def _formula_ocr_mode(mode: str, project_root: Path) -> str:
    if mode not in {"auto", "off", "paddle"}:
        raise ValueError("formula_ocr must be auto, off, or paddle")
    if mode == "off":
        return "off"
    available = paddle_formula_runtime_status(project_root)["available"]
    if mode == "paddle" and not available:
        install = (
            f'"{bundled_python() or Path(sys.executable)}" -m pip install '
            f'--no-cache-dir --target "{project_root / ".runtime" / "paddleocr"}" '
            f'-r "{project_root / "requirements-paddleocr.txt"}"'
        )
        raise RuntimeError(
            "Paddle formula OCR runtime is incomplete. Install it once with: "
            + install
        )
    return "paddle" if available else "off"


def _paddle_environment(project_root: Path) -> dict[str, str]:
    runtime = (project_root / ".runtime" / "paddleocr").resolve()
    home = (project_root / ".runtime" / "paddle-home").resolve()
    cache = home / "cache"
    paddlex_cache = home / "paddlex"
    for path in (home, cache, paddlex_cache):
        path.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CACHE_HOME": str(cache),
            "PADDLE_HOME": str(cache / "paddle"),
            "PADDLE_PDX_CACHE_HOME": str(paddlex_cache),
            "PADDLE_PDX_MODEL_SOURCE": "BOS",
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
            "FLAGS_allocator_strategy": "auto_growth",
            "OMP_NUM_THREADS": str(_rapidocr_thread_count()),
            "MKL_NUM_THREADS": str(_rapidocr_thread_count()),
            "PYTHONPATH": os.pathsep.join(
                part
                for part in (str(runtime), env.get("PYTHONPATH", ""))
                if part
            ),
            "GLOG_minloglevel": "2",
        }
    )
    return env


def run_paddle_formula_ocr(
    crop_paths: list[Path],
    project_root: Path,
    timeout_seconds: int = 180,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if not crop_paths:
        return {
            "status": "skipped",
            "reason": "no_detail_crops",
            "engine": "PaddleOCR 3.7.0 / PP-FormulaNet_plus-M",
            "formulas": [],
        }
    python = bundled_python()
    if not python:
        raise RuntimeError("Codex bundled Python was not found")
    command = [
        str(python),
        "-B",
        str(PADDLE_WORKER),
        "--project-root",
        str(project_root.resolve()),
    ]
    for path in crop_paths:
        command.extend(["--image", str(path.resolve())])
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env=_paddle_environment(project_root),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Paddle formula OCR timed out after {timeout_seconds}s"
        ) from exc
    if completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            "Paddle formula OCR failed: " + diagnostic[-1200:]
        )
    marker = "FORMULA_OCR_JSON="
    payload_line = next(
        (
            line[len(marker) :]
            for line in reversed((completed.stdout or "").splitlines())
            if line.startswith(marker)
        ),
        None,
    )
    if not payload_line:
        raise RuntimeError("Paddle formula OCR returned no structured result")
    try:
        return json.loads(payload_line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Paddle formula OCR returned invalid structured JSON"
        ) from exc


def ensure_ocr_runtime() -> None:
    """Use the bundled Python ABI and project-local OCR dependencies."""
    python = bundled_python()
    if python and not _running_bundled_python():
        completed = subprocess.run(
            [str(python), str(Path(sys.argv[0]).resolve()), *sys.argv[1:]]
        )
        raise SystemExit(completed.returncode)
    _add_runtime()
    try:
        from rapidocr import RapidOCR  # noqa: F401
    except (ImportError, OSError) as exc:
        install = (
            f'"{sys.executable}" -m pip install --no-cache-dir '
            f'--target "{OCR_RUNTIME}" -r "{OCR_REQUIREMENTS}"'
        )
        raise RuntimeError(
            "OCR runtime is incomplete. Install it once with: " + install
        ) from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _batch_key(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.resolve()).encode("utf-8"))
        digest.update(_sha256(path).encode("ascii"))
    return digest.hexdigest()[:16]


def _local_vlm_config(project_root: Path) -> dict[str, Any]:
    config_path = project_root / "config" / "math-error-notebook.json"
    if not config_path.is_file():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    config = payload.get("local_visual_model") if isinstance(payload, dict) else None
    return dict(config) if isinstance(config, dict) else {}


def _local_vlm_service_available(config: dict[str, Any]) -> bool:
    if not config.get("enabled"):
        return False
    endpoint = str(config.get("endpoint") or "").rstrip("/")
    client_python = Path(str(config.get("client_python") or ""))
    if not endpoint or not client_python.is_file() or not LOCAL_VLM_CLIENT.is_file():
        return False
    try:
        with urllib.request.urlopen(endpoint + "/", timeout=0.75) as response:
            return 200 <= int(response.status) < 500
    except (OSError, urllib.error.URLError, ValueError):
        return False


def local_vlm_runtime_status(project_root: Path) -> dict[str, Any]:
    """Return a compact, read-only status for the configured visual service."""
    config = _local_vlm_config(project_root)
    enabled = bool(config.get("enabled"))
    endpoint = str(config.get("endpoint") or "").rstrip("/") or None
    client_python = Path(str(config.get("client_python") or ""))
    return {
        "enabled": enabled,
        "auto_inference_enabled": bool(config.get("auto_inference_enabled")),
        "auto_inference_note": config.get("auto_inference_note"),
        "strategy": config.get("strategy"),
        "model_name": config.get("model_name"),
        "endpoint": endpoint,
        "client_python_available": client_python.is_file(),
        "client_script_available": LOCAL_VLM_CLIENT.is_file(),
        "service_available": _local_vlm_service_available(config) if enabled else False,
        "role": "visual_transcription_only",
    }


def _ocr_profile(
    formula_ocr: str,
    project_root: Path,
    vision_mode: str = "auto",
    task: str = "grade",
) -> dict[str, Any]:
    effective_formula_ocr = _formula_ocr_mode(formula_ocr, project_root)
    config = _local_vlm_config(project_root)
    local_enabled = bool(config.get("enabled")) and vision_mode != "off"
    auto_inference = bool(config.get("auto_inference_enabled"))
    should_probe = local_enabled and (vision_mode == "required" or auto_inference)
    local_available = _local_vlm_service_available(config) if should_probe else False
    return {
        "rapidocr": "3.9.2 / PP-OCRv6",
        "formula_ocr_requested": formula_ocr,
        "formula_ocr_effective": effective_formula_ocr,
        "formula_model": (
            PADDLE_FORMULA_MODEL if effective_formula_ocr == "paddle" else None
        ),
        "vision_mode_requested": vision_mode,
        "vision_task": task,
        "local_vlm_enabled": local_enabled,
        "local_vlm_auto_inference_enabled": (
            auto_inference if local_enabled else False
        ),
        "local_vlm_available": local_available,
        "local_vlm_model": config.get("model_name") if local_enabled else None,
        "local_vlm_prompt_version": (
            LOCAL_VLM_PROMPT_VERSION if local_enabled else None
        ),
    }


def _cached_result(
    packet_path: Path,
    ocr_profile: dict[str, Any],
) -> dict[str, Any] | None:
    if not packet_path.is_file():
        return None
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        int(packet.get("schema_version", 0)) >= 2
        and packet.get("ocr_profile") == ocr_profile
    ):
        return _compact_result(packet, packet_path, cache_hit=True)
    return None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _resize_to_limit(image: Any, max_side: int) -> Any:
    width, height = image.size
    longest = max(width, height)
    if longest <= max_side:
        return image.copy()
    scale = max_side / longest
    return image.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        resample=3,
    )


def prepare_image(path: Path, max_side: int) -> tuple[Any, tuple[int, int]]:
    from PIL import Image, ImageEnhance, ImageOps

    with Image.open(path) as source:
        original_size = source.size
        transposed = ImageOps.exif_transpose(source)
        if "A" in transposed.getbands() or "transparency" in transposed.info:
            rgba = transposed.convert("RGBA")
            white = Image.new("RGBA", rgba.size, "white")
            image = Image.alpha_composite(white, rgba).convert("RGB")
        else:
            image = transposed.convert("RGB")
        image = _resize_to_limit(image, max_side)
        image = ImageOps.autocontrast(image, cutoff=1)
        image = ImageEnhance.Contrast(image).enhance(1.08)
        image = ImageEnhance.Sharpness(image).enhance(1.12)
        return image, original_size


def prepare_remote_preview(
    path: Path,
    max_side: int,
    preview_side: int,
) -> tuple[Any, tuple[int, int], tuple[int, int]]:
    """Flatten and resize one photo without loading OCR or altering its content."""
    from PIL import Image, ImageOps

    with Image.open(path) as source:
        original_size = source.size
        transposed = ImageOps.exif_transpose(source)
        oriented_size = transposed.size
        if "A" in transposed.getbands() or "transparency" in transposed.info:
            rgba = transposed.convert("RGBA")
            white = Image.new("RGBA", rgba.size, "white")
            image = Image.alpha_composite(white, rgba).convert("RGB")
        else:
            image = transposed.convert("RGB")
        image = _resize_to_limit(image, min(max_side, preview_side))
        return image, original_size, oriented_size


def _remote_preview_profile(
    max_side: int,
    preview_side: int,
) -> dict[str, Any]:
    return {
        "preflight_mode": "remote",
        "local_processing": "exif_transpose_white_background_resize_only",
        "max_side": max_side,
        "preview_side": preview_side,
        "jpeg_quality": REMOTE_PREVIEW_JPEG_QUALITY,
        "remote_visual_review_required": True,
    }


def _process_remote_previews(
    image_paths: list[Path],
    project_root: Path,
    out_dir: Path | None,
    max_side: int,
    preview_side: int,
    force: bool,
) -> dict[str, Any]:
    """Create compact previews for the calling remote model; run no OCR/VLM."""
    paths = [path.resolve() for path in image_paths]
    key = _batch_key(paths)
    profile = _remote_preview_profile(max_side, preview_side)
    if out_dir is None:
        out_dir = project_root / "data" / "grade-inputs" / f"photo-{key}"
    out_dir = out_dir.resolve()
    packet_path = out_dir / "ocr-packet.json"
    if not force:
        cached = _cached_result(packet_path, profile)
        if cached:
            return cached

    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, Any]] = []
    total_original_pixels = 0
    total_preview_pixels = 0
    for page_number, path in enumerate(paths, start=1):
        image, original_size, oriented_size = prepare_remote_preview(
            path, max_side, preview_side
        )
        page_dir = out_dir / f"page-{page_number:02d}"
        page_dir.mkdir(parents=True, exist_ok=True)
        preview_path = page_dir / "model-preview.jpg"
        image.save(
            preview_path,
            "JPEG",
            quality=REMOTE_PREVIEW_JPEG_QUALITY,
            optimize=True,
        )
        total_original_pixels += original_size[0] * original_size[1]
        total_preview_pixels += image.size[0] * image.size[1]
        pages.append(
            {
                "page": page_number,
                "source_path": str(path),
                "source_sha256": _sha256(path),
                "original_size": list(original_size),
                "oriented_size": list(oriented_size),
                "normalized_size": list(image.size),
                "rotation_source": "exif_only",
                "normalized_path": str(preview_path.resolve()),
                "model_preview_path": str(preview_path.resolve()),
                "ocr_text": "",
                "ocr_line_count": 0,
                "lines": [],
                "detail_crops": [],
                "formula_ocr": [],
                "review_route": "remote_model_visual_review",
            }
        )

    packet = {
        "schema_version": 4,
        "created_at": now_iso(),
        "engine": "Pillow image normalization only",
        "ocr_profile": profile,
        "preflight_mode": "remote",
        "purpose": "size_control_for_remote_math_photo_grading",
        "batch_key": key,
        "pages": pages,
        "formula_ocr": {"status": "not_run", "device": None},
        "local_visual_model": {
            "status": "not_run",
            "role": "disabled_for_routine_grading",
        },
        "warnings": [],
        "model_workflow": [
            "Open every model_preview_path with the remote vision-capable model.",
            "Separate printed question content from the student's handwriting.",
            "Request a clearer crop when any key symbol, diagram, or step is unreadable.",
            "Do not claim visual review when the current model has no image capability.",
        ],
        "quality_rules": [
            "Local processing changes size and file encoding only; it performs no OCR or grading.",
            "Mathematical notation, diagrams, and handwriting require direct remote visual review.",
            "Unreadable evidence must never be reconstructed silently.",
        ],
        "metrics": {
            "ocr_characters": 0,
            "detail_crops": 0,
            "formula_candidates": 0,
            "local_vlm_attempted_pages": 0,
            "local_vlm_accepted_pages": 0,
            "local_vlm_rejected_pages": 0,
            "preview_pixel_ratio": (
                round(total_preview_pixels / total_original_pixels, 4)
                if total_original_pixels
                else None
            ),
        },
        "database_modified": False,
    }
    _write_json_atomic(packet_path, packet)
    return _compact_result(packet, packet_path, cache_hit=False)


def _box_size(box: list[list[float]]) -> tuple[float, float]:
    def distance(a: list[float], b: list[float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    return distance(box[0], box[1]), distance(box[1], box[2])


def _result_lines(result: Any) -> list[dict[str, Any]]:
    boxes = getattr(result, "boxes", None)
    texts = tuple(getattr(result, "txts", ()) or ())
    scores = tuple(getattr(result, "scores", ()) or ())
    if boxes is None:
        return []
    if hasattr(boxes, "tolist"):
        boxes = boxes.tolist()
    lines: list[dict[str, Any]] = []
    for box, text, score in zip(boxes, texts, scores):
        cleaned = str(text).strip()
        if not cleaned:
            continue
        lines.append(
            {
                "text": cleaned,
                "confidence": round(float(score), 5),
                "box": [[round(float(x), 1), round(float(y), 1)] for x, y in box],
            }
        )
    return lines


def orientation_score(lines: list[dict[str, Any]]) -> float:
    if not lines:
        return 0.0
    weighted_chars = 0.0
    horizontal_weight = 0.0
    for line in lines:
        weight = max(1, len(line["text"])) * float(line["confidence"])
        width, height = _box_size(line["box"])
        weighted_chars += weight
        if width >= height:
            horizontal_weight += weight
    horizontal_ratio = horizontal_weight / weighted_chars if weighted_chars else 0.0
    return weighted_chars * (0.65 + 0.35 * horizontal_ratio)


def _needs_rotation(lines: list[dict[str, Any]]) -> bool:
    if not lines:
        return True
    vertical = 0.0
    total = 0.0
    for line in lines:
        weight = max(1, len(line["text"])) * float(line["confidence"])
        width, height = _box_size(line["box"])
        total += weight
        if height > width * 1.25:
            vertical += weight
    return total == 0 or vertical / total > 0.45


def choose_orientation(
    image: Any, engine: Callable[[Any], Any]
) -> tuple[Any, int, list[dict[str, Any]], float]:
    import numpy as np

    first_result = engine(np.asarray(image))
    first_lines = _result_lines(first_result)
    candidates = [(image, 0, first_lines, orientation_score(first_lines))]
    if _needs_rotation(first_lines):
        for angle in (90, 270):
            rotated = image.rotate(angle, expand=True, fillcolor="white")
            result = engine(np.asarray(rotated))
            lines = _result_lines(result)
            candidates.append((rotated, angle, lines, orientation_score(lines)))
    return max(candidates, key=lambda item: item[3])


def _reading_order(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(line: dict[str, Any]) -> tuple[float, float]:
        xs = [point[0] for point in line["box"]]
        ys = [point[1] for point in line["box"]]
        return min(ys), min(xs)

    return sorted(lines, key=key)


def _crop_bounds(
    box: list[list[float]], image_size: tuple[int, int], pad_x: int, pad_y: int
) -> tuple[int, int, int, int]:
    width, height = image_size
    xs = [point[0] for point in box]
    ys = [point[1] for point in box]
    return (
        max(0, math.floor(min(xs) - pad_x)),
        max(0, math.floor(min(ys) - pad_y)),
        min(width, math.ceil(max(xs) + pad_x)),
        min(height, math.ceil(max(ys) + pad_y)),
    )


def save_detail_crops(
    image: Any,
    lines: list[dict[str, Any]],
    out_dir: Path,
    min_confidence: float,
    limit: int,
) -> list[dict[str, Any]]:
    candidates = [
        line
        for line in lines
        if float(line["confidence"]) < min_confidence or MATH_CUE.search(line["text"])
    ]
    candidates.sort(
        key=lambda line: (
            0 if MATH_CUE.search(line["text"]) else 1,
            float(line["confidence"]),
        )
    )
    saved: list[dict[str, Any]] = []
    for index, line in enumerate(candidates[:limit], start=1):
        line_width, line_height = _box_size(line["box"])
        pad_y = max(24, round(line_height * 1.6))
        pad_x = max(24, round(min(line_width, line_height * 3) * 0.35))
        bounds = _crop_bounds(line["box"], image.size, pad_x, pad_y)
        crop = image.crop(bounds)
        path = out_dir / f"detail-{index:02d}.jpg"
        crop.save(path, "JPEG", quality=90, optimize=True)
        saved.append(
            {
                "path": str(path.resolve()),
                "reason": (
                    "math_notation"
                    if MATH_CUE.search(line["text"])
                    else "low_ocr_confidence"
                ),
                "ocr_text": line["text"],
                "confidence": line["confidence"],
                "bounds": list(bounds),
            }
        )
    return saved


def _load_engine() -> Any:
    _add_runtime()
    from rapidocr import RapidOCR

    logger = logging.getLogger("RapidOCR")
    was_disabled = logger.disabled
    logger.disabled = True
    try:
        return RapidOCR(
            params={
                "EngineConfig.onnxruntime.intra_op_num_threads": (
                    _rapidocr_thread_count()
                ),
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            }
        )
    finally:
        logger.disabled = was_disabled


def _local_vlm_trigger_reasons(page: dict[str, Any], task: str) -> list[str]:
    """Select only pages where local visual transcription is worth its latency."""
    reasons: list[str] = []
    text = str(page.get("ocr_text") or "").strip()
    confidence = page.get("mean_confidence")
    crop_threshold = 0.82 if task == "grade" else 0.7
    low_confidence_crops = [
        crop
        for crop in page.get("detail_crops", [])
        if crop.get("reason") == "low_ocr_confidence"
        or (
            crop.get("confidence") is not None
            and float(crop["confidence"]) < crop_threshold
        )
    ]
    threshold = 0.9 if task == "grade" else 0.78
    minimum_characters = 80 if task == "grade" else 20
    if confidence is None or float(confidence) < threshold:
        reasons.append("low_mean_ocr_confidence")
    if len(text) < minimum_characters:
        reasons.append("little_rapidocr_text")
    if low_confidence_crops:
        reasons.append("low_confidence_regions")
    return reasons


def _run_local_vlm_page(
    page: dict[str, Any],
    page_dir: Path,
    project_root: Path,
    rapidocr_text: str,
) -> dict[str, Any]:
    config = _local_vlm_config(project_root)
    endpoint = str(config.get("endpoint") or "").rstrip("/")
    client_python = Path(str(config.get("client_python") or ""))
    raw_path = page_dir / "local-vlm-response.json"
    timeout = max(15, min(int(config.get("request_timeout_seconds") or 150), 600))
    max_tokens = max(256, min(int(config.get("max_new_tokens") or 1536), 4096))
    command = [
        str(client_python),
        "-X",
        "utf8",
        "-B",
        str(LOCAL_VLM_CLIENT),
        "--url",
        endpoint,
        "--image",
        str(page["normalized_path"]),
        "--prompt",
        str(LOCAL_VLM_PROMPT_PATH),
        "--source-sha256",
        str(page["source_sha256"]),
        "--output",
        str(raw_path),
        "--max-new-tokens",
        str(max_tokens),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "failed",
            "quality_gate": "visual_review_required",
            "error": f"local VLM request failed: {exc}",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-800:]
        return {
            "status": "failed",
            "quality_gate": "visual_review_required",
            "error": "local VLM client failed" + (f": {detail}" if detail else ""),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    try:
        raw = raw_path.read_text(encoding="utf-8-sig").strip()
        if raw.startswith("```") or raw.endswith("```") or "<think>" in raw.lower():
            raise ValueError("response is not JSON-only")
        payload = json.loads(raw)
        evidence = validate_local_vlm_response(
            payload,
            expected_source_sha256=str(page["source_sha256"]),
            rapidocr_text=rapidocr_text,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "status": "rejected",
            "quality_gate": "visual_review_required",
            "error": str(exc),
            "raw_response_path": str(raw_path.resolve()) if raw_path.is_file() else None,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    return {
        "status": "accepted",
        "quality_gate": evidence["quality_gate"],
        "evidence": evidence,
        "raw_response_path": str(raw_path.resolve()),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def _apply_local_vlm(
    pages: list[dict[str, Any]],
    out_dir: Path,
    project_root: Path,
    vision_mode: str,
    task: str,
) -> dict[str, Any]:
    config = _local_vlm_config(project_root)
    enabled = bool(config.get("enabled")) and vision_mode != "off"
    auto_inference = bool(config.get("auto_inference_enabled"))
    available = (
        _local_vlm_service_available(config)
        if enabled and (vision_mode == "required" or auto_inference)
        else False
    )
    summary: dict[str, Any] = {
        "status": "disabled" if not enabled else "ready",
        "mode": vision_mode,
        "task": task,
        "model_name": config.get("model_name") if enabled else None,
        "service_available": available,
        "attempted_pages": 0,
        "accepted_pages": 0,
        "rejected_pages": 0,
        "skipped_pages": 0,
        "role": "visual_transcription_only",
    }
    if not enabled:
        for page in pages:
            page["review_route"] = "rapidocr_then_model_visual_review"
        return summary
    if vision_mode == "auto" and not auto_inference:
        summary["status"] = "auto_inference_disabled_after_benchmark"
        summary["benchmark_note"] = config.get("auto_inference_note")
        for page in pages:
            page["review_route"] = "rapidocr_then_model_visual_review"
        return summary
    if not available:
        if vision_mode == "required":
            raise RuntimeError("local visual model is required but its service is unavailable")
        summary["status"] = "service_unavailable_fallback"
        for page in pages:
            page["review_route"] = "rapidocr_fallback_local_vlm_unavailable"
        return summary
    for page in pages:
        trigger_reasons = (
            ["explicitly_required"]
            if vision_mode == "required"
            else _local_vlm_trigger_reasons(page, task)
        )
        page["local_vlm_trigger_reasons"] = trigger_reasons
        if not trigger_reasons:
            page["review_route"] = "rapidocr_then_model_visual_review"
            summary["skipped_pages"] += 1
            continue
        summary["attempted_pages"] += 1
        page_dir = out_dir / f"page-{int(page['page']):02d}"
        result = _run_local_vlm_page(
            page,
            page_dir,
            project_root,
            str(page.get("ocr_text") or ""),
        )
        page["local_vlm"] = result
        if result["status"] == "accepted":
            summary["accepted_pages"] += 1
            page["review_route"] = (
                "local_vlm_evidence_then_reasoning_model"
                if result["quality_gate"] == "pass"
                else "local_vlm_evidence_plus_model_visual_review"
            )
        else:
            summary["rejected_pages"] += 1
            page["review_route"] = "rapidocr_plus_model_visual_review"
    if summary["rejected_pages"]:
        summary["status"] = "partial_fallback"
    elif summary["attempted_pages"]:
        summary["status"] = "completed"
    else:
        summary["status"] = "not_needed"
    return summary


def _process_photos_unlocked(
    image_paths: list[Path],
    project_root: Path,
    out_dir: Path | None = None,
    max_side: int = 2400,
    preview_side: int = 1100,
    min_confidence: float = 0.86,
    max_detail_crops: int = 6,
    force: bool = False,
    engine_factory: Callable[[], Any] | None = None,
    formula_ocr: str = "auto",
    vision_mode: str = "auto",
    task: str = "grade",
) -> dict[str, Any]:
    paths = [path.resolve() for path in image_paths]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError("photo not found: " + ", ".join(missing))
    if not paths:
        raise ValueError("at least one photo is required")

    key = _batch_key(paths)
    ocr_profile = _ocr_profile(formula_ocr, project_root, vision_mode, task)
    effective_formula_ocr = str(ocr_profile["formula_ocr_effective"])
    if out_dir is None:
        out_dir = project_root / "data" / "grade-inputs" / f"photo-{key}"
    out_dir = out_dir.resolve()
    packet_path = out_dir / "ocr-packet.json"
    if not force:
        cached = _cached_result(packet_path, ocr_profile)
        if cached:
            return cached

    out_dir.mkdir(parents=True, exist_ok=True)
    engine = (engine_factory or _load_engine)()
    pages: list[dict[str, Any]] = []
    total_characters = 0
    total_detail_crops = 0
    total_original_pixels = 0
    total_preview_pixels = 0
    crop_metadata: dict[str, dict[str, Any]] = {}

    for page_number, path in enumerate(paths, start=1):
        image, original_size = prepare_image(path, max_side)
        image, rotation, lines, score = choose_orientation(image, engine)
        lines = _reading_order(lines)
        page_dir = out_dir / f"page-{page_number:02d}"
        page_dir.mkdir(parents=True, exist_ok=True)

        normalized_path = page_dir / "normalized.jpg"
        image.save(normalized_path, "JPEG", quality=92, optimize=True)
        preview = _resize_to_limit(image, preview_side)
        preview_path = page_dir / "model-preview.jpg"
        preview.save(preview_path, "JPEG", quality=82, optimize=True)
        detail_crops = save_detail_crops(
            image, lines, page_dir, min_confidence, max_detail_crops
        )
        for crop in detail_crops:
            crop_metadata[str(Path(crop["path"]).resolve())] = {
                **crop,
                "page": page_number,
            }

        text = "\n".join(line["text"] for line in lines)
        confidences = [float(line["confidence"]) for line in lines]
        total_characters += len(text)
        total_detail_crops += len(detail_crops)
        total_original_pixels += original_size[0] * original_size[1]
        total_preview_pixels += preview.size[0] * preview.size[1]
        pages.append(
            {
                "page": page_number,
                "source_path": str(path),
                "source_sha256": _sha256(path),
                "original_size": list(original_size),
                "normalized_size": list(image.size),
                "rotation_degrees_ccw": rotation,
                "orientation_score": round(score, 3),
                "normalized_path": str(normalized_path.resolve()),
                "model_preview_path": str(preview_path.resolve()),
                "ocr_text": text,
                "ocr_line_count": len(lines),
                "mean_confidence": (
                    round(sum(confidences) / len(confidences), 5)
                    if confidences
                    else None
                ),
                "lines": lines,
                "detail_crops": detail_crops,
                "formula_ocr": [],
            }
        )

    formula_summary: dict[str, Any] = {
        "status": "disabled",
        "engine": None,
        "device": None,
        "formulas": [],
    }
    formula_warning: str | None = None
    if effective_formula_ocr == "paddle":
        try:
            formula_summary = run_paddle_formula_ocr(
                [Path(path) for path in crop_metadata],
                project_root,
            )
        except RuntimeError as exc:
            if formula_ocr == "paddle":
                raise
            formula_warning = str(exc)
            formula_summary = {
                "status": "fallback",
                "engine": "RapidOCR only",
                "device": None,
                "formulas": [],
            }

    formula_count = 0
    for formula in formula_summary.get("formulas", []):
        path = str(Path(formula.get("path", "")).resolve())
        crop = crop_metadata.get(path)
        latex = str(formula.get("latex", "")).strip()
        if not crop or not latex:
            continue
        page_index = int(crop["page"]) - 1
        pages[page_index]["formula_ocr"].append(
            {
                "crop_path": path,
                "latex": latex,
                "bounds": crop["bounds"],
                "crop_reason": crop["reason"],
                "requires_visual_confirmation": True,
            }
        )
        formula_count += 1

    local_vlm_summary = _apply_local_vlm(
        pages,
        out_dir,
        project_root,
        vision_mode,
        task,
    )
    local_vlm_warning = None
    if local_vlm_summary["status"] == "service_unavailable_fallback":
        local_vlm_warning = "local visual model unavailable; RapidOCR fallback used"
    elif local_vlm_summary["status"] == "partial_fallback":
        local_vlm_warning = (
            "one or more local visual responses failed the strict contract; "
            "visual review remains required"
        )

    packet = {
        "schema_version": 3,
        "created_at": now_iso(),
        "engine": "RapidOCR 3.9.2 / PP-OCRv6"
        + (
            f" + {formula_summary.get('engine')}"
            if formula_summary.get("status") == "ok"
            else ""
        ),
        "ocr_profile": ocr_profile,
        "purpose": "assistive_preflight_for_math_photo_grading",
        "batch_key": key,
        "pages": pages,
        "formula_ocr": {
            key: value
            for key, value in formula_summary.items()
            if key != "formulas"
        },
        "local_visual_model": local_vlm_summary,
        "warnings": [
            warning
            for warning in (formula_warning, local_vlm_warning)
            if warning
        ],
        "model_workflow": [
            "Read ocr_text before opening an image.",
            "Use accepted local_vlm evidence only as visual transcription; never as a verdict or solution.",
            "Follow review_route: open the preview only when the route still requires model visual review.",
            "Use formula_ocr LaTeX as a locator, not as trusted transcription.",
            "Open model_preview_path to separate printed question from handwriting.",
            "Open only relevant detail_crops when a formula or step is ambiguous.",
            "Use normalized_path only as a final fallback.",
        ],
        "quality_rules": [
            "OCR is not evidence of mathematical correctness.",
            "Formulas, diagrams, superscripts, subscripts, and handwriting require visual review.",
            "Unreadable symbols must be reported as unclear; never reconstruct them silently.",
        ],
        "metrics": {
            "ocr_characters": total_characters,
            "detail_crops": total_detail_crops,
            "formula_candidates": formula_count,
            "local_vlm_attempted_pages": local_vlm_summary["attempted_pages"],
            "local_vlm_accepted_pages": local_vlm_summary["accepted_pages"],
            "local_vlm_rejected_pages": local_vlm_summary["rejected_pages"],
            "preview_pixel_ratio": (
                round(total_preview_pixels / total_original_pixels, 4)
                if total_original_pixels
                else None
            ),
        },
        "database_modified": False,
    }
    _write_json_atomic(packet_path, packet)
    return _compact_result(packet, packet_path, cache_hit=False)


def process_photos(
    image_paths: list[Path],
    project_root: Path,
    out_dir: Path | None = None,
    max_side: int = REMOTE_PREVIEW_DEFAULT_MAX_SIDE,
    preview_side: int = REMOTE_PREVIEW_DEFAULT_SIDE,
    min_confidence: float = 0.86,
    max_detail_crops: int = 6,
    force: bool = False,
    engine_factory: Callable[[], Any] | None = None,
    formula_ocr: str = "auto",
    vision_mode: str = "auto",
    task: str = "grade",
    preflight_mode: str = "remote",
    lock_timeout_seconds: float = OCR_LOCK_TIMEOUT_SECONDS,
    lock_factory: Callable[[Path, float], Any] | None = None,
) -> dict[str, Any]:
    """Prepare remote previews by default; run serialized OCR only on request."""
    paths = [path.resolve() for path in image_paths]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError("photo not found: " + ", ".join(missing))
    if not paths:
        raise ValueError("at least one photo is required")
    if preflight_mode not in {"remote", "ocr"}:
        raise ValueError("preflight_mode must be remote or ocr")
    key = _batch_key(paths)
    if vision_mode not in {"auto", "off", "required"}:
        raise ValueError("vision_mode must be auto, off, or required")
    if task not in {"grade", "verify"}:
        raise ValueError("task must be grade or verify")
    if preflight_mode == "remote":
        if formula_ocr == "paddle" or vision_mode == "required":
            raise ValueError(
                "formula_ocr=paddle and vision_mode=required need --preflight-mode ocr"
            )
        result = _process_remote_previews(
            paths,
            project_root,
            out_dir,
            max_side,
            preview_side,
            force,
        )
        result["ocr_serialized"] = False
        result["ocr_lock_scope"] = None
        result["ocr_lock_wait_seconds"] = 0.0
        return result
    ocr_profile = _ocr_profile(formula_ocr, project_root, vision_mode, task)
    effective_out_dir = (
        out_dir.resolve()
        if out_dir is not None
        else (project_root / "data" / "grade-inputs" / f"photo-{key}").resolve()
    )
    packet_path = effective_out_dir / "ocr-packet.json"
    if not force:
        cached = _cached_result(packet_path, ocr_profile)
        if cached:
            cached["ocr_serialized"] = True
            cached["ocr_lock_scope"] = "machine_wide_shared_across_subjects"
            cached["ocr_lock_wait_seconds"] = 0.0
            return cached

    factory = lock_factory or InterProcessFileLock
    lock = factory(ocr_lock_path(project_root), lock_timeout_seconds)
    with lock:
        result = _process_photos_unlocked(
            paths,
            project_root,
            effective_out_dir,
            max_side,
            preview_side,
            min_confidence,
            max_detail_crops,
            force,
            engine_factory,
            formula_ocr,
            vision_mode,
            task,
        )
    result["ocr_serialized"] = True
    result["ocr_lock_scope"] = "machine_wide_shared_across_subjects"
    result["ocr_lock_wait_seconds"] = float(getattr(lock, "wait_seconds", 0.0))
    return result


def _compact_result(
    packet: dict[str, Any], packet_path: Path, cache_hit: bool
) -> dict[str, Any]:
    pages = packet.get("pages") or []
    ocr_pages = [
        {
            "page": page.get("page"),
            "text": page.get("ocr_text", ""),
            "preview_path": page.get("model_preview_path"),
            "detail_crops": [
                {
                    "path": crop.get("path"),
                    "reason": crop.get("reason"),
                    "ocr_text": crop.get("ocr_text", ""),
                    "confidence": crop.get("confidence"),
                }
                for crop in page.get("detail_crops", [])
            ],
            "review_route": page.get("review_route"),
            "local_vlm": page.get("local_vlm"),
        }
        for page in pages
    ]
    question_ids = sorted(
        {
            match
            for page in pages
            for match in re.findall(r"\bQ-[A-Za-z0-9]+\b", page.get("ocr_text", ""))
        }
    )
    result = {
        "status": "ok",
        "preflight_mode": packet.get("preflight_mode", "ocr"),
        "packet": str(packet_path.resolve()),
        "pages": len(pages),
        "ocr_pages": ocr_pages,
        "question_ids": question_ids,
        "ocr_characters": packet.get("metrics", {}).get("ocr_characters", 0),
        "detail_crops": packet.get("metrics", {}).get("detail_crops", 0),
        "formula_candidates": packet.get("metrics", {}).get(
            "formula_candidates", 0
        ),
        "formula_ocr_status": packet.get("formula_ocr", {}).get("status"),
        "formula_ocr_device": packet.get("formula_ocr", {}).get("device"),
        "local_visual_model": packet.get("local_visual_model", {}),
        "preview_pixel_ratio": packet.get("metrics", {}).get("preview_pixel_ratio"),
        "preview_paths": [page.get("model_preview_path") for page in pages],
        "cache_hit": cache_hit,
        "database_modified": False,
    }
    if result["preflight_mode"] == "remote":
        result["remote_visual_review_required"] = True
    else:
        result["local_vlm_transcription"] = {
            "prompt_version": LOCAL_VLM_PROMPT_VERSION,
            "prompt_path": str(LOCAL_VLM_PROMPT_PATH.resolve()),
            "role": "visual_transcription_only",
            "validator_command": "photo-vlm-validate",
        }
    return result
