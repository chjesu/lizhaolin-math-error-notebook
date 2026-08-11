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
            preflight_mode="ocr",
            engine_factory=fail_engine_factory,
            lock_factory=CacheProducingLock,
        )
        self.assertTrue(result["cache_hit"])
        self.assertTrue(result["ocr_serialized"])
        self.assertEqual(result["ocr_lock_wait_seconds"], 0.25)

    def test_remote_preview_mode_skips_ocr_engine_and_shared_lock(self) -> None:
        from PIL import Image as PILImage

        image_path = self.root / "large-question.png"
        PILImage.new("RGBA", (3000, 1500), (0, 0, 0, 0)).save(image_path)

        def fail_engine_factory():
            raise AssertionError("remote preview mode must not load OCR")

        def fail_lock_factory(_path: Path, _timeout: float):
            raise AssertionError("remote preview mode must not acquire the OCR lock")

        result = photo_ocr.process_photos(
            [image_path],
            self.root,
            out_dir=self.root / "remote-preview",
            max_side=2400,
            preview_side=1600,
            engine_factory=fail_engine_factory,
            lock_factory=fail_lock_factory,
        )

        self.assertEqual(result["preflight_mode"], "remote")
        self.assertFalse(result["ocr_serialized"])
        self.assertEqual(result["ocr_pages"][0]["review_route"], "remote_model_visual_review")
        preview_path = Path(result["preview_paths"][0])
        self.assertTrue(preview_path.is_file())
        with PILImage.open(preview_path) as preview:
            self.assertEqual(preview.size, (1600, 800))
            self.assertEqual(preview.mode, "RGB")
            self.assertGreaterEqual(min(preview.getpixel((0, 0))), 250)

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


class LocalVLMTranscriptionContractTests(unittest.TestCase):
    SHA256 = "a" * 64

    @classmethod
    def valid_payload(cls) -> dict[str, object]:
        return {
            "schema_version": "1",
            "prompt_version": "math-photo-evidence-v1",
            "source_sha256": cls.SHA256,
            "rotation_degrees_ccw": 0,
            "question_blocks": [
                {
                    "printed_question_number": "3",
                    "printed_stem": "已知 $x^2-y^2=2$，求值。",
                    "printed_options": [],
                    "student_work": [
                        {"order": 1, "text": "$x^2-y^2=2$", "certainty": "clear"}
                    ],
                    "student_final_answer": "2",
                    "diagram": {"present": False, "visible_facts": []},
                    "unclear_items": [],
                }
            ],
            "warnings": [],
            "requires_visual_confirmation": False,
        }

    def test_contract_uses_versioned_fixed_prompt(self) -> None:
        contract = photo_ocr.local_vlm_contract()
        prompt_path = Path(contract["prompt_path"])
        prompt = prompt_path.read_text(encoding="utf-8")
        self.assertEqual(contract["prompt_version"], "math-photo-evidence-v1")
        self.assertIn("不判断对错", prompt)
        self.assertIn("只返回下面结构的一个 JSON 对象", prompt)

    def test_valid_transcription_passes_without_grading_fields(self) -> None:
        result = photo_ocr.validate_local_vlm_response(
            self.valid_payload(),
            expected_source_sha256=self.SHA256,
            rapidocr_text="3 已知 x2-y2=2 求值",
        )
        self.assertEqual(result["quality_gate"], "pass")
        self.assertFalse(result["requires_visual_confirmation"])
        self.assertNotIn("solution", result)

    def test_grading_key_is_rejected(self) -> None:
        payload = self.valid_payload()
        payload["verdict"] = "correct"
        with self.assertRaisesRegex(ValueError, "forbidden grading key"):
            photo_ocr.validate_local_vlm_response(payload)

    def test_uncertain_handwriting_forces_visual_review(self) -> None:
        payload = self.valid_payload()
        payload["question_blocks"][0]["student_work"][0]["certainty"] = "uncertain"
        result = photo_ocr.validate_local_vlm_response(payload)
        self.assertEqual(result["quality_gate"], "visual_review_required")
        self.assertIn("uncertain_handwriting", result["visual_review_reasons"])

    def test_clear_described_diagram_does_not_force_duplicate_visual_review(self) -> None:
        payload = self.valid_payload()
        payload["question_blocks"][0]["diagram"] = {
            "present": True,
            "visible_facts": ["直线 l 与圆相切于点 A"],
        }
        result = photo_ocr.validate_local_vlm_response(payload)
        self.assertEqual(result["quality_gate"], "pass")
        self.assertFalse(result["requires_visual_confirmation"])

    def test_response_file_rejects_markdown_fence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            response = Path(directory) / "response.json"
            response.write_text("```json\n{}\n```", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON only"):
                photo_ocr.validate_local_vlm_response_file(response)


class LocalVLMRoutingTests(unittest.TestCase):
    def test_clear_verification_page_skips_slow_local_model(self) -> None:
        page = {
            "ocr_text": "题干与答案解析均完整。" * 5,
            "mean_confidence": 0.96,
            "detail_crops": [{"reason": "math_notation"}],
        }
        self.assertEqual(photo_ocr._local_vlm_trigger_reasons(page, "verify"), [])

    def test_low_confidence_grading_page_routes_to_local_model(self) -> None:
        page = {
            "ocr_text": "学生作答",
            "mean_confidence": 0.7,
            "detail_crops": [{"reason": "low_ocr_confidence"}],
        }
        reasons = photo_ocr._local_vlm_trigger_reasons(page, "grade")
        self.assertIn("low_mean_ocr_confidence", reasons)
        self.assertIn("little_rapidocr_text", reasons)
        self.assertIn("low_confidence_regions", reasons)

    def test_auto_mode_falls_back_when_service_is_unavailable(self) -> None:
        pages = [{"page": 1}]
        with (
            patch.object(
                photo_ocr,
                "_local_vlm_config",
                return_value={"enabled": True, "auto_inference_enabled": True},
            ),
            patch.object(photo_ocr, "_local_vlm_service_available", return_value=False),
        ):
            result = photo_ocr._apply_local_vlm(
                pages, Path.cwd(), Path.cwd(), "auto", "grade"
            )
        self.assertEqual(result["status"], "service_unavailable_fallback")
        self.assertIn("fallback", pages[0]["review_route"])

    def test_required_mode_rejects_unavailable_service(self) -> None:
        with (
            patch.object(photo_ocr, "_local_vlm_config", return_value={"enabled": True}),
            patch.object(photo_ocr, "_local_vlm_service_available", return_value=False),
        ):
            with self.assertRaisesRegex(RuntimeError, "required"):
                photo_ocr._apply_local_vlm(
                    [{"page": 1}], Path.cwd(), Path.cwd(), "required", "verify"
                )

    def test_auto_gate_skips_service_probe_after_failed_benchmark(self) -> None:
        pages = [{"page": 1}]
        with (
            patch.object(
                photo_ocr,
                "_local_vlm_config",
                return_value={"enabled": True, "auto_inference_enabled": False},
            ),
            patch.object(
                photo_ocr,
                "_local_vlm_service_available",
                side_effect=AssertionError("disabled auto route must not probe service"),
            ),
        ):
            result = photo_ocr._apply_local_vlm(
                pages, Path.cwd(), Path.cwd(), "auto", "grade"
            )
        self.assertEqual(result["status"], "auto_inference_disabled_after_benchmark")
        self.assertEqual(pages[0]["review_route"], "rapidocr_then_model_visual_review")


if __name__ == "__main__":
    unittest.main()
