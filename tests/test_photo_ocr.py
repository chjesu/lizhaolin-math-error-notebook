from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / ".agents"
    / "skills"
    / "math-error-notebook"
    / "scripts"
    / "photo_ocr.py"
)
SPEC = importlib.util.spec_from_file_location("math_photo_ocr", MODULE_PATH)
assert SPEC and SPEC.loader
photo_ocr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(photo_ocr)


class PhotoOCRConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_shared_lock_path_is_subject_independent(self) -> None:
        with (
            patch.dict(os.environ, {photo_ocr.OCR_SHARED_LOCK_ENV: ""}),
            patch.object(photo_ocr.tempfile, "gettempdir", return_value=str(self.root)),
        ):
            math_path = photo_ocr.ocr_lock_path(self.root / "math")
            physics_path = photo_ocr.ocr_lock_path(self.root / "physics")
        self.assertEqual(math_path, physics_path)
        self.assertEqual(
            math_path,
            self.root
            / "LiZhaolinErrorNotebooks"
            / "locks"
            / "photo-ocr.lock",
        )

    def test_file_lock_serializes_independent_processes(self) -> None:
        lock_path = self.root / "photo-ocr.lock"
        ready_path = self.root / "ready.txt"
        child_code = "\n".join(
            (
                "import importlib.util",
                "import time",
                "from pathlib import Path",
                f"spec = importlib.util.spec_from_file_location('child_photo_ocr', {str(MODULE_PATH)!r})",
                "module = importlib.util.module_from_spec(spec)",
                "spec.loader.exec_module(module)",
                f"with module.InterProcessFileLock(Path({str(lock_path)!r}), 5.0):",
                f"    Path({str(ready_path)!r}).write_text('ready', encoding='utf-8')",
                "    time.sleep(1.0)",
            )
        )
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            deadline = time.monotonic() + 5.0
            while not ready_path.is_file() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(ready_path.is_file(), "child did not acquire OCR lock")
            with self.assertRaises(photo_ocr.OCRLockTimeout):
                with photo_ocr.InterProcessFileLock(
                    lock_path, timeout_seconds=0.15, poll_seconds=0.03
                ):
                    self.fail("second process unexpectedly acquired the OCR lock")
        finally:
            stdout, stderr = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, stdout + stderr)
        with photo_ocr.InterProcessFileLock(lock_path, timeout_seconds=1.0):
            pass

    def test_waiting_request_rechecks_cache_before_loading_models(self) -> None:
        image_path = self.root / "question.png"
        image_path.write_bytes(b"not-decoded-because-cache-wins")
        out_dir = self.root / "ocr-output"
        packet_path = out_dir / "ocr-packet.json"
        profile = photo_ocr._ocr_profile("off", self.root)
        packet = {
            "schema_version": 2,
            "ocr_profile": profile,
            "pages": [],
            "formula_ocr": {"status": "disabled", "device": None},
            "metrics": {
                "ocr_characters": 0,
                "detail_crops": 0,
                "formula_candidates": 0,
                "preview_pixel_ratio": None,
            },
        }

        class CacheProducingLock:
            def __init__(self, path: Path, timeout_seconds: float) -> None:
                self.path = path
                self.timeout_seconds = timeout_seconds
                self.wait_seconds = 0.25

            def __enter__(self) -> "CacheProducingLock":
                out_dir.mkdir(parents=True, exist_ok=True)
                photo_ocr._write_json_atomic(packet_path, packet)
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:
                return None

        def fail_engine_factory():
            raise AssertionError("OCR engine must not load after cache appears")

        result = photo_ocr.process_photos(
            [image_path],
            self.root,
            out_dir=out_dir,
            formula_ocr="off",
            engine_factory=fail_engine_factory,
            lock_factory=CacheProducingLock,
        )
        self.assertTrue(result["cache_hit"])
        self.assertTrue(result["ocr_serialized"])
        self.assertEqual(result["ocr_lock_wait_seconds"], 0.25)

    def test_rapidocr_uses_bounded_onnx_threads(self) -> None:
        captured: dict[str, object] = {}

        class FakeRapidOCR:
            def __init__(self, params):
                captured.update(params)

        with (
            patch.object(photo_ocr, "_add_runtime"),
            patch.dict(sys.modules, {"rapidocr": SimpleNamespace(RapidOCR=FakeRapidOCR)}),
        ):
            photo_ocr._load_engine()
        self.assertEqual(captured["EngineConfig.onnxruntime.inter_op_num_threads"], 1)
        self.assertLessEqual(
            captured["EngineConfig.onnxruntime.intra_op_num_threads"],
            photo_ocr.OCR_ONNX_MAX_THREADS,
        )

    def test_atomic_packet_write_leaves_only_complete_json(self) -> None:
        packet_path = self.root / "ocr-packet.json"
        payload = {"schema_version": 2, "pages": [{"ocr_text": "x^2"}]}
        photo_ocr._write_json_atomic(packet_path, payload)
        self.assertEqual(json.loads(packet_path.read_text(encoding="utf-8")), payload)
        self.assertEqual(list(self.root.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
