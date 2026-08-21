#!/usr/bin/env python3
"""Create cached, size-controlled photo previews for remote visual review."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "math-photo-preflight/v1"
DEFAULT_MAX_SIDE = 2400
DEFAULT_PREVIEW_SIDE = 2000
JPEG_QUALITY = 88


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
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
    from PIL import Image

    longest = max(image.size)
    if longest <= max_side:
        return image.copy()
    scale = max_side / longest
    size = tuple(max(1, round(value * scale)) for value in image.size)
    return image.resize(size, resample=Image.Resampling.LANCZOS)


def prepare_preview(
    path: Path, max_side: int, preview_side: int
) -> tuple[Any, tuple[int, int], tuple[int, int]]:
    """Apply EXIF orientation, flatten transparency, and resize one image."""
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
        return (
            _resize_to_limit(image, min(max_side, preview_side)),
            original_size,
            oriented_size,
        )


def _compact_result(
    packet: dict[str, Any], packet_path: Path, cache_hit: bool
) -> dict[str, Any]:
    pages = packet.get("pages") or []
    return {
        "status": "ok",
        "packet": str(packet_path.resolve()),
        "pages": len(pages),
        "preview_paths": [page["preview_path"] for page in pages],
        "review_route": "remote_model_visual_review",
        "remote_visual_review_required": True,
        "preview_pixel_ratio": packet.get("metrics", {}).get("preview_pixel_ratio"),
        "cache_hit": cache_hit,
        "database_modified": False,
    }


def _cached_result(
    packet_path: Path,
    profile: dict[str, Any],
    source_files: list[dict[str, str]],
) -> dict[str, Any] | None:
    if not packet_path.is_file():
        return None
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        packet.get("schema") == SCHEMA
        and packet.get("profile") == profile
        and packet.get("source_files") == source_files
        and packet.get("pages")
        and all(
            Path(str(page.get("preview_path") or "")).is_file()
            for page in packet["pages"]
        )
    ):
        return _compact_result(packet, packet_path, cache_hit=True)
    return None


def prepare_photo_previews(
    image_paths: list[Path],
    project_root: Path,
    out_dir: Path | None = None,
    max_side: int = DEFAULT_MAX_SIDE,
    preview_side: int = DEFAULT_PREVIEW_SIDE,
    task: str = "grade",
    force: bool = False,
) -> dict[str, Any]:
    """Normalize photos locally; all content interpretation remains remote."""
    paths = [path.resolve() for path in image_paths]
    if not paths:
        raise ValueError("at least one photo is required")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError("photo not found: " + ", ".join(missing))
    if task not in {"grade", "verify"}:
        raise ValueError("task must be grade or verify")
    if max_side <= 0 or preview_side <= 0:
        raise ValueError("image size limits must be positive")

    source_files = [
        {"path": str(path), "sha256": _sha256(path)} for path in paths
    ]
    profile = {
        "task": task,
        "local_processing": "exif_transpose_white_background_resize_only",
        "max_side": max_side,
        "preview_side": preview_side,
        "jpeg_quality": JPEG_QUALITY,
    }
    if out_dir is None:
        out_dir = (
            project_root.resolve()
            / "data"
            / "grade-inputs"
            / f"photo-{_batch_key(paths)}"
        )
    out_dir = out_dir.resolve()
    packet_path = out_dir / "photo-preflight.json"
    if not force:
        cached = _cached_result(packet_path, profile, source_files)
        if cached:
            return cached

    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, Any]] = []
    original_pixels = 0
    preview_pixels = 0
    for page_number, (path, source_file) in enumerate(
        zip(paths, source_files), start=1
    ):
        image, original_size, oriented_size = prepare_preview(
            path, max_side, preview_side
        )
        page_dir = out_dir / f"page-{page_number:02d}"
        page_dir.mkdir(parents=True, exist_ok=True)
        preview_path = page_dir / "model-preview.jpg"
        image.save(preview_path, "JPEG", quality=JPEG_QUALITY, optimize=True)
        original_pixels += original_size[0] * original_size[1]
        preview_pixels += image.size[0] * image.size[1]
        pages.append(
            {
                "page": page_number,
                "source_path": source_file["path"],
                "source_sha256": source_file["sha256"],
                "original_size": list(original_size),
                "oriented_size": list(oriented_size),
                "preview_size": list(image.size),
                "preview_path": str(preview_path.resolve()),
                "review_route": "remote_model_visual_review",
            }
        )

    packet = {
        "schema": SCHEMA,
        "created_at": now_iso(),
        "profile": profile,
        "source_files": source_files,
        "pages": pages,
        "metrics": {
            "preview_pixel_ratio": (
                round(preview_pixels / original_pixels, 4)
                if original_pixels
                else None
            )
        },
        "quality_rules": [
            "Open every preview_path with a vision-capable remote model.",
            "Request a clearer crop when a key symbol, diagram, or step is unreadable.",
            "Local preprocessing performs no OCR, solving, or grading.",
        ],
        "database_modified": False,
    }
    _write_json_atomic(packet_path, packet)
    return _compact_result(packet, packet_path, cache_hit=False)
