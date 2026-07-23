#!/usr/bin/env python3
"""Offline OCR preflight for photographed math work.

The output is assistive evidence for a vision-capable model. OCR text must not
replace inspection of mathematical notation, diagrams, or handwritten work.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = SKILL_DIR.parents[2]
OCR_RUNTIME = PROJECT_ROOT / ".runtime" / "ocr"
OCR_REQUIREMENTS = PROJECT_ROOT / "requirements-ocr.txt"
PADDLE_WORKER = SCRIPT_DIR / "paddle_formula_worker.py"
PADDLE_FORMULA_MODEL = "PP-FormulaNet_plus-M"
BUNDLED_PYTHON = (
    Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime"
    / "dependencies" / "python" / "python.exe"
)
MATH_CUE = re.compile(
    r"(?:[=<>≤≥±×÷√∑∫∞^_]|\\(?:frac|sqrt|sin|cos|tan|log|ln)|"
    r"\b(?:sin|cos|tan|log|ln)\b|[A-Za-z]\s*[+\-*/=])"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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
        image = ImageOps.exif_transpose(source).convert("RGB")
        image = _resize_to_limit(image, max_side)
        image = ImageOps.autocontrast(image, cutoff=1)
        image = ImageEnhance.Contrast(image).enhance(1.08)
        image = ImageEnhance.Sharpness(image).enhance(1.12)
        return image, original_size


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
        return RapidOCR()
    finally:
        logger.disabled = was_disabled


def process_photos(
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
) -> dict[str, Any]:
    paths = [path.resolve() for path in image_paths]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError("photo not found: " + ", ".join(missing))
    if not paths:
        raise ValueError("at least one photo is required")

    key = _batch_key(paths)
    effective_formula_ocr = _formula_ocr_mode(formula_ocr, project_root)
    ocr_profile = {
        "rapidocr": "3.9.2 / PP-OCRv6",
        "formula_ocr_requested": formula_ocr,
        "formula_ocr_effective": effective_formula_ocr,
        "formula_model": (
            PADDLE_FORMULA_MODEL if effective_formula_ocr == "paddle" else None
        ),
    }
    if out_dir is None:
        out_dir = project_root / "data" / "grade-inputs" / f"photo-{key}"
    out_dir = out_dir.resolve()
    packet_path = out_dir / "ocr-packet.json"
    if packet_path.is_file() and not force:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        if (
            int(packet.get("schema_version", 0)) >= 2
            and packet.get("ocr_profile") == ocr_profile
        ):
            return _compact_result(packet, packet_path, cache_hit=True)

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

    packet = {
        "schema_version": 2,
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
        "warnings": [formula_warning] if formula_warning else [],
        "model_workflow": [
            "Read ocr_text before opening an image.",
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
            "preview_pixel_ratio": (
                round(total_preview_pixels / total_original_pixels, 4)
                if total_original_pixels
                else None
            ),
        },
        "database_modified": False,
    }
    packet_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return _compact_result(packet, packet_path, cache_hit=False)


def _compact_result(
    packet: dict[str, Any], packet_path: Path, cache_hit: bool
) -> dict[str, Any]:
    pages = packet.get("pages") or []
    return {
        "status": "ok",
        "packet": str(packet_path.resolve()),
        "pages": len(pages),
        "ocr_characters": packet.get("metrics", {}).get("ocr_characters", 0),
        "detail_crops": packet.get("metrics", {}).get("detail_crops", 0),
        "formula_candidates": packet.get("metrics", {}).get(
            "formula_candidates", 0
        ),
        "formula_ocr_status": packet.get("formula_ocr", {}).get("status"),
        "formula_ocr_device": packet.get("formula_ocr", {}).get("device"),
        "preview_pixel_ratio": packet.get("metrics", {}).get("preview_pixel_ratio"),
        "preview_paths": [page.get("model_preview_path") for page in pages],
        "cache_hit": cache_hit,
        "database_modified": False,
    }
