from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".agents" / "skills" / "math-error-notebook" / "scripts" / "codex_task_router.py"
SPEC = importlib.util.spec_from_file_location("codex_task_router", MODULE_PATH)
router = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(router)


class CodexTaskRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = router.load_routing_config()

    def test_fast_standard_and_expert_routes(self) -> None:
        fast = router.select_route(self.config, "tag")
        self.assertEqual((fast["model"], fast["reasoning_effort"]), ("gpt-5.6-luna", "low"))

        standard = router.select_route(self.config, "grade-photo", has_images=True)
        self.assertEqual(standard["model"], "gpt-5.6-terra")

        expert = router.select_route(
            self.config, "grade-photo", risks=["ambiguous_visual"], has_images=True
        )
        self.assertEqual((expert["model"], expert["reasoning_effort"]), ("gpt-5.6-sol", "high"))

    def test_simplified_verification_uses_luna_medium(self) -> None:
        route = router.select_route(self.config, "verify-simplified")
        self.assertEqual(route["model"], "gpt-5.6-luna")
        self.assertEqual(route["reasoning_effort"], "medium")

    def test_image_promotes_fast_task_to_standard(self) -> None:
        route = router.select_route(self.config, "recommend", has_images=True)
        self.assertEqual(route["profile_key"], "standard")
        self.assertEqual(route["reasoning_effort"], "medium")

    def test_adjudication_uses_xhigh(self) -> None:
        route = router.select_route(self.config, "adjudicate")
        self.assertEqual((route["model"], route["reasoning_effort"]), ("gpt-5.6-sol", "xhigh"))

    def test_profile_installation_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            first = router.install_profiles(self.config, target)
            second = router.install_profiles(self.config, target)
            self.assertEqual(len(first["created"]), 3)
            self.assertEqual(len(second["unchanged"]), 3)
            text = (target / "math-standard.config.toml").read_text(encoding="utf-8")
            self.assertIn('model = "gpt-5.6-terra"', text)

    def test_all_output_schemas_are_valid_json_objects(self) -> None:
        for task in self.config["tasks"].values():
            path = router.ROUTING_CONFIG.parent / task["schema"]
            schema = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])

    def test_prompt_embeds_compact_input_without_requiring_shell_read(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            path = Path(directory) / "packet.json"
            path.write_text('{"question":{"stem":"x^2"}}', encoding="utf-8")
            compact = router.compact_input(path)
            route = router.select_route(self.config, "grade-text")
            prompt = router.build_prompt(route, compact, None, 0)
            self.assertIn('输入数据：{"question":{"stem":"x^2"}}', prompt)
            self.assertNotIn(str(path), prompt)

    def test_tag_catalogs_come_from_authoritative_project_assets(self) -> None:
        catalogs = router.local_catalogs()
        self.assertIn("function-properties", {item["code"] for item in catalogs["knowledge_codes"]})
        self.assertIn("careless", {item["code"] for item in catalogs["cause_codes"]})
        self.assertIn("tangent", {item["code"] for item in catalogs["feature_codes"]})

    def test_grade_payload_does_not_record_correct_work_as_error(self) -> None:
        router.validate_payload(
            "grade-text",
            {
                "verdict": "correct",
                "key_verification": "答案与关键步骤一致",
                "error_analysis": None,
                "next_step": "本题结束",
            },
        )
        with self.assertRaises(ValueError):
            router.validate_payload(
                "grade-text",
                {"verdict": "wrong", "error_analysis": None},
            )

    def test_installed_skill_contains_router_and_schemas(self) -> None:
        skill = ROOT / ".agents" / "skills" / "math-error-notebook"
        self.assertTrue((skill / "scripts" / "codex_task_router.py").is_file())
        self.assertTrue((skill / "assets" / "codex-model-routing.json").is_file())
        self.assertTrue((skill / "assets" / "codex-schemas" / "grade-result.schema.json").is_file())


if __name__ == "__main__":
    unittest.main()
