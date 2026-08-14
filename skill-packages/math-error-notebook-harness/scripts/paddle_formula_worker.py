#!/usr/bin/env python3
"""Isolated PaddleOCR formula recognizer used by photo_ocr.py.

The marker-prefixed JSON line is intentionally the only machine-readable
stdout contract. Paddle/PaddleX may emit informational lines around it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


MODEL_NAME = "PP-FormulaNet_plus-M"
OUTPUT_MARKER = "FORMULA_OCR_JSON="


def result_payload(result: Any) -> dict[str, Any]:
    data = result.json
    if callable(data):
        data = data()
    record = data.get("res", data)
    return {
        "path": str(Path(record.get("input_path", "")).resolve()),
        "latex": str(record.get("rec_formula", "")).strip(),
        "requires_visual_confirmation": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--image", type=Path, action="append", required=True)
    parser.add_argument("--device", default="auto")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    import paddle
    from paddleocr import FormulaRecognition

    device = args.device
    if device == "auto":
        device = (
            "gpu:0"
            if paddle.device.is_compiled_with_cuda()
            and paddle.device.cuda.device_count() > 0
            else "cpu"
        )
    started = time.perf_counter()
    model = FormulaRecognition(model_name=MODEL_NAME, device=device)
    initialized = time.perf_counter()
    formulas: list[dict[str, Any]] = []
    for image in args.image:
        for result in model.predict(input=str(image.resolve()), batch_size=1):
            formulas.append(result_payload(result))
    finished = time.perf_counter()
    payload = {
        "status": "ok",
        "engine": f"PaddleOCR 3.7.0 / {MODEL_NAME}",
        "model": MODEL_NAME,
        "device": device,
        "init_seconds": round(initialized - started, 3),
        "predict_seconds": round(finished - initialized, 3),
        "formulas": formulas,
        "gpu_memory_allocated_mb": (
            round(paddle.device.cuda.memory_allocated() / 1024 / 1024, 1)
            if device.startswith("gpu")
            else 0.0
        ),
        "gpu_memory_reserved_mb": (
            round(paddle.device.cuda.memory_reserved() / 1024 / 1024, 1)
            if device.startswith("gpu")
            else 0.0
        ),
        "database_modified": False,
    }
    print(OUTPUT_MARKER + json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
