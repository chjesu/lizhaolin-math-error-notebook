import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".agents/skills/math-error-notebook/scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "deepseek_worker",
    SCRIPT_DIR / "deepseek_worker.py",
)
deepseek_worker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(deepseek_worker)

DEEPSEEK_RUNTIME = ROOT / ".runtime/deepseek"
if DEEPSEEK_RUNTIME.is_dir():
    sys.path.insert(0, str(DEEPSEEK_RUNTIME))
try:
    SAFE_SPEC = importlib.util.spec_from_file_location(
        "deepseek_safe_init",
        SCRIPT_DIR / "safe_init.py",
    )
    safe_init = importlib.util.module_from_spec(SAFE_SPEC)
    assert SAFE_SPEC.loader is not None
    SAFE_SPEC.loader.exec_module(safe_init)
except ModuleNotFoundError:
    safe_init = None


CATALOGS = {
    "knowledge_codes": [{"code": "k1", "name": "知识点"}],
    "cause_codes": [{"code": "c1", "name": "错因"}],
    "feature_codes": [{"code": "f1", "name": "特征"}],
}


class DeepSeekWorkerTests(unittest.TestCase):
    def test_text_evidence_is_accepted(self):
        evidence = deepseek_worker.validate_grade_evidence(
            {
                "problem_text": "题目",
                "student_answer": "答案",
                "student_work_has_steps": True,
            }
        )
        self.assertEqual(evidence["problem_text"], "题目")

    def test_image_evidence_requires_completed_visual_review(self):
        with self.assertRaisesRegex(ValueError, "source_type=vision_review"):
            deepseek_worker.validate_grade_evidence(
                {
                    "problem_text": "题目",
                    "student_answer": "答案",
                    "image_path": "work.jpg",
                }
            )
        with self.assertRaisesRegex(ValueError, "visual_review_completed=true"):
            deepseek_worker.validate_grade_evidence(
                {
                    "source_type": "vision_review",
                    "problem_text": "题目",
                    "student_answer": "答案",
                    "image_path": "work.jpg",
                }
            )

    def test_model_cannot_choose_image_or_question_identity(self):
        template = deepseek_worker.read_json(deepseek_worker.ERROR_TEMPLATE)
        candidate = dict(template)
        candidate["image_path"] = "invented.jpg"
        candidate["question_id"] = "invented-id"
        evidence = {
            "problem_text": "题目",
            "student_answer": "答案",
            "image_path": "trusted.jpg",
            "question_id": "trusted-id",
        }
        parsed = deepseek_worker.parse_grade_candidate(
            json.dumps(candidate, ensure_ascii=False), template, evidence
        )
        self.assertEqual(parsed["image_path"], "trusted.jpg")
        self.assertEqual(parsed["question_id"], "trusted-id")

    def test_final_answer_only_cannot_produce_commit_ready_cause(self):
        candidate = {"question_id": "Q-1", "confidence": 0.99}
        escalations = deepseek_worker.grade_escalations(
            candidate, {"student_work_has_steps": False}, 0.9
        )
        self.assertEqual(
            escalations[0]["reason"],
            "no_student_steps_cause_requires_human_review",
        )

    def test_grade_preview_warning_requires_review(self):
        escalations = deepseek_worker.grade_gate_escalations(
            {"warnings": ["unclear_with_high_confidence"]}, "Q-1"
        )
        self.assertEqual(
            escalations[0]["reason"],
            "grade_preview_warning:unclear_with_high_confidence",
        )

    def test_visual_verification_is_escalated(self):
        self.assertTrue(
            deepseek_worker._visual_dependency(
                {"question": {"stem": "观察图形 ![图](question.png)"}}
            )
        )

    def test_valid_verification_candidate_is_prepared(self):
        packet = {"question": {"id": "Q-1"}}
        content = json.dumps(
            {
                "items": [
                    {
                        "question_id": "Q-1",
                        "verdict": "pass",
                        "checks_confirmed": True,
                        "independent_answer": "$1$",
                        "independent_solution": "由题意可得 $1$。",
                        "answer_check": "match",
                        "solution_check": "match",
                        "knowledge_codes": ["k1"],
                        "target_causes": ["c1"],
                        "feature_codes": ["f1"],
                        "grade": 11,
                        "difficulty": 3,
                        "question_type": "解答题",
                        "correction": {},
                        "review_note": "题干、答案和解析自洽。",
                        "confidence": 0.96,
                    }
                ]
            },
            ensure_ascii=False,
        )
        decision, escalations = deepseek_worker.parse_verify_candidate(
            content, [packet], CATALOGS, 0.9, "test-reviewer"
        )
        self.assertEqual(decision["items"][0]["question_id"], "Q-1")
        self.assertEqual(escalations, [])

    def test_low_confidence_verification_is_not_accepted(self):
        content = json.dumps(
            {"items": [{"question_id": "Q-1", "confidence": 0.5}]}
        )
        decision, escalations = deepseek_worker.parse_verify_candidate(
            content, [{"question": {"id": "Q-1"}}], CATALOGS, 0.9, "reviewer"
        )
        self.assertEqual(decision["items"], [])
        self.assertEqual(escalations[0]["question_id"], "Q-1")

    def test_recommendations_are_limited_to_packet_ids(self):
        packet = {
            "schema": "math-recommendation-review-packet/v1",
            "error_id": "E-1",
            "items": [{"question_id": "Q-1", "score": 8.0}],
        }
        content = json.dumps(
            {
                "items": [
                    {
                        "question_id": "Q-1",
                        "score": 9,
                        "reason": "结构一致",
                        "confidence": 0.9,
                    }
                ]
            },
            ensure_ascii=False,
        )
        selected, escalations = deepseek_worker.parse_recommend_candidate(
            content, packet, 0.8, 1
        )
        self.assertEqual(selected["items"][0]["question_id"], "Q-1")
        self.assertEqual(escalations, [])
        bad = json.dumps(
            {"items": [{"question_id": "Q-X", "reason": "无", "confidence": 1}]}
        )
        with self.assertRaisesRegex(ValueError, "unexpected"):
            deepseek_worker.parse_recommend_candidate(bad, packet, 0.8, 1)

    def test_tag_candidate_uses_only_catalog_codes(self):
        content = json.dumps(
            {
                "knowledge_codes": ["k1"],
                "target_causes": ["c1"],
                "feature_codes": ["f1"],
                "grade": 11,
                "difficulty": 2.5,
                "question_type": "选择题",
                "review_note": "依据题干",
                "confidence": 0.9,
            },
            ensure_ascii=False,
        )
        candidate, escalations = deepseek_worker.parse_tag_candidate(
            content, CATALOGS, 0.85, "Q-1"
        )
        self.assertEqual(candidate["knowledge_codes"], ["k1"])
        self.assertEqual(escalations, [])

    def test_thinking_defaults_match_task_risk(self):
        self.assertTrue(deepseek_worker.choose_thinking("grade", "auto"))
        self.assertTrue(deepseek_worker.choose_thinking("verify", "auto"))
        self.assertFalse(deepseek_worker.choose_thinking("recommend", "auto"))
        self.assertFalse(deepseek_worker.choose_thinking("tag", "auto"))

    @unittest.skipIf(safe_init is None, "optional DeepSeek client is not installed")
    def test_protocol_wrapper_requires_reasoning_content_for_tool_history(self):
        history = safe_init.validate_history(
            [
                {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
                {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            ]
        )
        self.assertFalse(history["ok"])
        self.assertEqual(history["violations"][0]["rule"], "C2")

    @unittest.skipIf(safe_init is None, "optional DeepSeek client is not installed")
    def test_protocol_wrapper_rejects_runaway_output_budget(self):
        with self.assertRaisesRegex(ValueError, "max_tokens"):
            safe_init._validate_request(
                [{"role": "user", "content": "test"}],
                safe_init.MAX_OUTPUT_TOKENS + 1,
                False,
            )


if __name__ == "__main__":
    unittest.main()
