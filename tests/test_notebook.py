import importlib.util
import json
import re
import tempfile
import unittest
from argparse import Namespace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "skills" / "math-error-notebook" / "scripts" / "notebook.py"
SPEC = importlib.util.spec_from_file_location("math_notebook", SCRIPT)
notebook = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(notebook)

PRACTICE_SCRIPT = ROOT / ".agents" / "skills" / "math-error-notebook" / "scripts" / "practice_sheet.py"
PRACTICE_SPEC = importlib.util.spec_from_file_location("practice_sheet", PRACTICE_SCRIPT)
practice_sheet = importlib.util.module_from_spec(PRACTICE_SPEC)
assert PRACTICE_SPEC.loader
PRACTICE_SPEC.loader.exec_module(practice_sheet)

PHOTO_OCR_SCRIPT = ROOT / ".agents" / "skills" / "math-error-notebook" / "scripts" / "photo_ocr.py"
PHOTO_OCR_SPEC = importlib.util.spec_from_file_location("photo_ocr", PHOTO_OCR_SCRIPT)
photo_ocr = importlib.util.module_from_spec(PHOTO_OCR_SPEC)
assert PHOTO_OCR_SPEC.loader
PHOTO_OCR_SPEC.loader.exec_module(photo_ocr)


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "data" / "test.db"
        self.conn = notebook.connect(self.db)
        notebook.init_database(self.conn)
        records = notebook.read_json_records(notebook.DEFAULT_SEED)
        self.seed_count = len(records)
        result = notebook.import_records(
            self.conn, records, "内置原创示例题", None, "Project-Original", True
        )
        self.assertEqual(result["inserted"], len(records))

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def create_error(self):
        analysis = {
            "problem_text": "已知 sin x=3/5，求 cos 2x。",
            "student_answer": "cos 2x=1-sin²x=16/25",
            "correct_answer": "7/25",
            "correct_solution": "cos 2x=1-2sin²x=7/25",
            "first_wrong_step": "漏掉二倍角公式中的系数 2",
            "cause_code": "knowledge_gap",
            "cause_detail": "未准确掌握二倍角公式。",
            "evidence": ["学生写成 1-sin²x"],
            "knowledge_codes": ["trig-identities"],
            "difficulty": 2.5,
            "confidence": 0.98,
        }
        return notebook.record_error(self.conn, analysis, self.root, False)

    def test_error_record_and_recommendation(self):
        error_id = self.create_error()
        report = list((self.root / "errors").rglob(f"{error_id}.md"))
        self.assertEqual(len(report), 1)
        items = notebook.recommend(self.conn, error_id, 5, True, self.root)
        self.assertGreaterEqual(len(items), 2)
        self.assertTrue(all(item["question_id"] for item in items))
        self.assertEqual(len(list((self.root / "practice").glob("*.md"))), 1)

    def test_delete_error_can_preserve_and_detach_attempts(self):
        error_id = self.create_error()
        question_id = notebook.recommend(self.conn, error_id, 1, False, self.root)[0][
            "question_id"
        ]
        self.conn.execute(
            """INSERT INTO attempts(
                   id,question_id,error_id,submitted_answer,is_correct,
                   cause_code,attempted_at,note
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "ATT-delete-error-detach",
                question_id,
                error_id,
                "7/25",
                1,
                None,
                notebook.now_iso(),
                "keep attempt history",
            ),
        )
        self.conn.commit()

        with self.assertRaisesRegex(ValueError, "recorded practice attempts"):
            notebook.delete_error(self.conn, error_id, self.root)

        result = notebook.delete_error(
            self.conn, error_id, self.root, detach_attempts=True
        )
        self.assertTrue(result["deleted"])
        self.assertEqual(result["detached_attempts"], 1)
        attempt = self.conn.execute(
            "SELECT error_id,note FROM attempts WHERE id=?",
            ("ATT-delete-error-detach",),
        ).fetchone()
        self.assertIsNone(attempt["error_id"])
        self.assertIn("Detached from deleted error", attempt["note"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM errors WHERE id=?", (error_id,)
            ).fetchone()[0],
            0,
        )

    def test_grade_preview_validates_without_writing(self):
        analysis = {
            "problem_text": r"求 $f(x)=x^2$ 的导数。",
            "student_answer": r"$f'(x)=x$",
            "correct_answer": r"$f'(x)=2x$",
            "correct_solution": r"由幂函数求导公式得 $f'(x)=2x$。",
            "first_wrong_step": "幂函数求导时漏乘指数 2",
            "cause_code": "knowledge_gap",
            "cause_detail": "没有正确使用幂函数求导公式。",
            "evidence": ["学生将导数写成 x"],
            "knowledge_codes": ["derivatives"],
            "feature_codes": ["formula-substitution"],
            "difficulty": 1.5,
            "confidence": 0.95,
        }
        before = self.conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
        result = notebook.validate_error_analysis(self.conn, analysis)
        after = self.conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
        self.assertTrue(result["valid"])
        self.assertEqual(result["feature_codes"], ["formula-substitution"])
        self.assertEqual(before, after)

    def test_grade_preview_rejects_careless_without_evidence(self):
        analysis = {
            "problem_text": "计算 $1+1$。",
            "cause_code": "careless",
            "cause_detail": "声称粗心但没有学生步骤证据。",
            "evidence": [],
            "knowledge_codes": [],
            "difficulty": 1,
            "confidence": 0.8,
        }
        with self.assertRaisesRegex(ValueError, "careless requires direct evidence"):
            notebook.validate_error_analysis(self.conn, analysis)

    def test_embedded_image_marker_requires_visual_review(self):
        row = {
            "stem": "完成作答。![题图](data/images/graph.png)",
            "answer": "1",
            "solution": "由函数性质可得。",
            "question_type": "解答题",
            "options_json": None,
        }
        self.assertIn("diagram_reference", notebook.question_issue_codes(row, 1, 1))
        row["stem"] = "完成作答。"
        row["answer"] = "![答案图](data/images/answer.png)"
        self.assertIn("diagram_reference", notebook.question_issue_codes(row, 1, 1))

    def test_image_only_choice_options_require_visual_review(self):
        row = {
            "stem": "下列图像中符合条件的是（　　）",
            "answer": "A",
            "solution": "由图像性质可知选 A。",
            "question_type": "单选题",
            "options_json": '["![A](data/images/a.png)", "B", "C", "D"]',
        }
        self.assertIn("diagram_reference", notebook.question_issue_codes(row, 1, 1))

    def test_delete_rejected_questions_requires_reject_and_confirmation(self):
        source_name = "Delete test source"
        record = {
            "id": "Q-rejected-delete-test",
            "stem": "Test rejected question",
            "answer": "1",
            "solution": "Test solution",
            "grade": 10,
            "question_type": "solution",
            "difficulty": 2,
            "knowledge_codes": ["sets"],
        }
        question_id = notebook.normalize_question(
            record, source_name, None, "Project-Original", False
        )["id"]
        notebook.import_records(
            self.conn,
            [record],
            source_name,
            None,
            "Project-Original",
            False,
        )
        with self.assertRaisesRegex(ValueError, "latest review is not reject"):
            notebook.delete_rejected_questions(self.conn, [question_id], False)

        self.conn.execute(
            """INSERT INTO verification_reviews(
               id,question_id,verdict,reviewer,review_sha256,review_json,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                "VR-delete-test",
                question_id,
                "reject",
                "test",
                "sha",
                "{}",
                "2026-07-26T18:00:00+08:00",
            ),
        )
        self.conn.commit()

        preview = notebook.delete_rejected_questions(
            self.conn, [question_id], False
        )
        self.assertFalse(preview["database_modified"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM questions WHERE id=?", (question_id,)
            ).fetchone()[0],
            1,
        )

        result = notebook.delete_rejected_questions(
            self.conn, [question_id], True
        )
        self.assertTrue(result["database_modified"])
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM questions WHERE id=?", (question_id,)
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM verification_reviews WHERE question_id=?",
                (question_id,),
            ).fetchone()[0],
            0,
        )

    def test_recommendation_preview_is_compact_and_read_only(self):
        error_id = self.create_error()
        items = notebook.recommend(self.conn, error_id, 3, False, self.root)
        self.assertGreaterEqual(len(items), 1)
        self.assertNotIn("answer", items[0])
        self.assertNotIn("solution", items[0])
        count = self.conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE error_id=?", (error_id,)
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_recommendation_excludes_attempted_questions(self):
        error_id = self.create_error()
        first = notebook.recommend(self.conn, error_id, 1, False, self.root)[0]
        attempted_id = first["question_id"]
        self.conn.execute(
            """INSERT INTO attempts(
                   id,question_id,error_id,submitted_answer,is_correct,
                   cause_code,attempted_at,note
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "ATT-recommendation-exclusion",
                attempted_id,
                error_id,
                None,
                1,
                None,
                notebook.now_iso(),
                "test attempted-question exclusion",
            ),
        )
        self.conn.commit()

        items = notebook.recommend(self.conn, error_id, 10, False, self.root)
        self.assertNotIn(attempted_id, [item["question_id"] for item in items])

    def test_correct_attempt_updates_existing_row_without_duplication(self):
        error_id = self.create_error()
        question_id = notebook.recommend(self.conn, error_id, 1, False, self.root)[0]["question_id"]
        self.conn.execute(
            """INSERT INTO attempts(
                   id,question_id,error_id,submitted_answer,is_correct,
                   cause_code,attempted_at,note
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "ATT-misgraded",
                question_id,
                error_id,
                "sqrt(14)",
                0,
                "calculation",
                notebook.now_iso(),
                "initial grading",
            ),
        )
        self.conn.commit()
        args = Namespace(
            attempt_id="ATT-misgraded",
            correct=True,
            wrong=False,
            answer="sqrt(5)",
            cause_code=None,
            note="visual recheck",
        )

        result = notebook.correct_attempt(self.conn, args)

        self.assertEqual(result["previous_result"], "wrong")
        self.assertEqual(result["result"], "correct")
        row = self.conn.execute(
            """SELECT submitted_answer,is_correct,cause_code,note
               FROM attempts WHERE id=?""",
            ("ATT-misgraded",),
        ).fetchone()
        self.assertEqual(row["submitted_answer"], "sqrt(5)")
        self.assertEqual(row["is_correct"], 1)
        self.assertIsNone(row["cause_code"])
        self.assertIn("visual recheck", row["note"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM attempts WHERE id=?", ("ATT-misgraded",)
            ).fetchone()[0],
            1,
        )

    def test_recommendation_packet_is_compact_reusable_and_read_only(self):
        error_id = self.create_error()
        packet_path = self.root / "recommendation-packet.json"
        result = notebook.recommendation_packet(
            self.conn, error_id, 2, self.root, None, None, packet_path
        )
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        self.assertEqual(result["database_modified"], False)
        self.assertEqual(result["content_mode"], "compact")
        self.assertGreaterEqual(result["candidates"], 1)
        self.assertNotIn("answer", packet["items"][0])
        self.assertNotIn("solution", packet["items"][0])
        saved = self.conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE error_id=?", (error_id,)
        ).fetchone()[0]
        self.assertEqual(saved, 0)
        assigned = notebook.assign_recommendations(
            self.conn, error_id, packet["items"], self.root, False
        )
        self.assertEqual(len(assigned), len(packet["items"]))

    def test_recommendation_packet_full_is_explicit(self):
        error_id = self.create_error()
        packet_path = self.root / "recommendation-packet-full.json"
        result = notebook.recommendation_packet(
            self.conn, error_id, 1, self.root, None, None, packet_path, True
        )
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        self.assertEqual(result["content_mode"], "full")
        self.assertIn("answer", packet["items"][0])
        self.assertIn("solution", packet["items"][0])

    def test_recommendation_local_keyword_split_and_placeholder_filter(self):
        self.assertEqual(
            notebook.normalize_recommendation_keywords(["双曲线 离心率，焦点"]),
            ["双曲线", "离心率", "焦点"],
        )
        error_id = self.create_error()
        malformed = {
            "id": "Q-placeholder-local-filter",
            "stem": "The introductory phrase should read: replace this placeholder.",
            "answer": "Matches the stored final answer.",
            "solution": "placeholder",
            "grade": 10,
            "question_type": "填空题",
            "difficulty": 2.5,
            "knowledge_codes": ["trig-identities"],
            "verified": True,
        }
        notebook.import_records(
            self.conn, [malformed], "占位题过滤测试", None, "Project-Original", True
        )
        items = notebook.recommend(
            self.conn, error_id, 10, False, self.root, ["三角 恒等"], False, False
        )
        self.assertNotIn("Q-placeholder-local-filter", [item["question_id"] for item in items])

    def test_search_defaults_to_compact_rows(self):
        rows = notebook.search_questions(
            self.conn,
            Namespace(
                knowledge=None,
                grade=None,
                difficulty_min=None,
                difficulty_max=None,
                text=None,
                verified=True,
                limit=1,
                full=False,
            ),
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("stem", rows[0])
        self.assertNotIn("solution", rows[0])
        self.assertNotIn("raw_json", rows[0])
        self.assertNotIn("answer", rows[0])

    def test_question_detail_loads_one_full_item_without_raw_by_default(self):
        row = self.conn.execute("SELECT id,grade FROM questions LIMIT 1").fetchone()
        question_id = row["id"]
        item = notebook.question_detail(self.conn, question_id)
        self.assertIn("answer", item)
        self.assertIn("solution", item)
        self.assertNotIn("raw_json", item)
        self.assertIn("knowledge_codes", item)

    def test_question_detail_compact_omits_long_solution(self):
        row = self.conn.execute("SELECT id FROM questions LIMIT 1").fetchone()
        item = notebook.question_detail(self.conn, row["id"], compact=True)
        self.assertIn("answer", item)
        self.assertIn("stem", item)
        self.assertIn("knowledge_codes", item)
        self.assertNotIn("solution", item)
        self.assertNotIn("raw_json", item)
        self.assertNotIn("created_at", item)

    def test_compact_metadata_lookups(self):
        points = notebook.list_knowledge_points(self.conn, "圆", 11)
        self.assertTrue(any(item["code"] == "line-circle" for item in points))
        causes = notebook.list_cause_codes("概念")
        self.assertEqual(causes, [{"code": "concept_confusion", "name": "概念理解不准确"}])
        self.conn.execute(
            "INSERT INTO sources(name,license,rights_confirmed) VALUES(?,?,1)",
            ("内置原创示例题", "Project-Original"),
        )
        self.conn.commit()
        sources = notebook.list_sources(self.conn, "内置原创")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["questions"], self.seed_count)
        self.assertEqual(sources[0]["verified"], self.seed_count)

    def test_review_wrong_starts_new_cycle(self):
        error_id = self.create_error()
        result = notebook.mark_review(self.conn, error_id, "wrong", "公式仍不熟", date.today())
        self.assertEqual(result["next_due"], (date.today() + timedelta(days=1)).isoformat())
        cycle = self.conn.execute(
            "SELECT MAX(cycle) FROM review_schedule WHERE error_id=?", (error_id,)
        ).fetchone()[0]
        self.assertEqual(cycle, 2)

    def test_mark_error_mastered_keeps_completed_history_and_cancels_pending_stages(self):
        error_id = self.create_error()
        notebook.mark_review(self.conn, error_id, "correct", None, date.today())

        result = notebook.mark_error_mastered(self.conn, error_id)

        self.assertEqual(result["status"], "mastered")
        self.assertEqual(result["cancelled_pending_stages"], len(notebook.REVIEW_INTERVALS) - 1)
        self.assertEqual(
            self.conn.execute("SELECT status FROM errors WHERE id=?", (error_id,)).fetchone()[0],
            "mastered",
        )
        rows = self.conn.execute(
            "SELECT completed_at FROM review_schedule WHERE error_id=?", (error_id,)
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["completed_at"])

    def test_correct_review_restores_original_schedule_without_duplication(self):
        error_id = self.create_error()
        base = date(2026, 8, 8)
        original_due_dates = [
            row["due_date"]
            for row in self.conn.execute(
                "SELECT due_date FROM review_schedule WHERE error_id=? ORDER BY cycle,stage",
                (error_id,),
            ).fetchall()
        ]
        notebook.mark_review(self.conn, error_id, "partial", "initial grading", base)
        self.assertEqual(
            self.conn.execute(
                "SELECT MAX(cycle) FROM review_schedule WHERE error_id=?", (error_id,)
            ).fetchone()[0],
            2,
        )

        result = notebook.correct_review(
            self.conn, error_id, "correct", "visual recheck", base
        )

        self.assertEqual(result["previous_result"], "partial")
        self.assertEqual(result["result"], "correct")
        rows = self.conn.execute(
            """SELECT cycle,stage,due_date,result,completed_at
               FROM review_schedule WHERE error_id=? ORDER BY cycle,stage""",
            (error_id,),
        ).fetchall()
        self.assertEqual([row["cycle"] for row in rows], [1] * len(notebook.REVIEW_INTERVALS))
        self.assertEqual(rows[0]["result"], "correct")
        self.assertIsNotNone(rows[0]["completed_at"])
        self.assertTrue(all(row["completed_at"] is None for row in rows[1:]))
        self.assertEqual(
            [row["due_date"] for row in rows],
            original_due_dates,
        )

    def test_verified_bank_and_coverage(self):
        summary = notebook.stats(self.conn)
        self.assertEqual(summary["questions"], self.seed_count)
        self.assertEqual(summary["verified_questions"], self.seed_count)
        coverage = notebook.coverage(self.conn)
        self.assertEqual(len(coverage), 33)
        self.assertTrue(all(row["question_count"] >= 2 for row in coverage))

    def test_project_skill_uses_project_canonical_database(self):
        self.assertEqual(notebook.DEFAULT_DB.resolve(), (ROOT / "data" / "math_notebook.db").resolve())

    def test_bank_info_reports_identity_and_integrity(self):
        info = notebook.bank_info(self.conn, self.db)
        self.assertEqual(info["project_name"], "李兆霖数学错题本")
        self.assertEqual(info["canonical_path"], str(self.db.resolve()))
        self.assertEqual(info["integrity_check"], "ok")
        self.assertEqual(info["foreign_key_violations"], 0)
        self.assertEqual(info["questions"], self.seed_count)
        self.assertEqual(info["verified_questions"], self.seed_count)
        self.assertEqual(len(info["sha256"]), 64)

    def test_duplicate_seed_is_idempotent(self):
        records = notebook.read_json_records(notebook.DEFAULT_SEED)
        result = notebook.import_records(
            self.conn, records, "内置原创示例题", None, "Project-Original", True
        )
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
            self.seed_count,
        )

    def test_external_question_requires_audited_solution_before_verification(self):
        result = notebook.import_records(
            self.conn,
            [{"id": "remote-1", "problem": "求函数 y=x^2 的导数。", "answer": "2x"}],
            "Open Test Bank",
            "https://example.test/questions.json",
            "MIT",
            False,
        )
        self.assertEqual(result["inserted"], 1)
        question_id = self.conn.execute(
            "SELECT id FROM questions WHERE source_name='Open Test Bank'"
        ).fetchone()[0]
        solution_file = self.root / "solution.md"
        solution_file.write_text("由幂函数求导公式，$(x^2)'=2x$。", encoding="utf-8")
        payload = notebook.annotate_question(
            self.conn,
            Namespace(
                question_id=question_id,
                knowledge=["derivatives"],
                target_cause=["knowledge_gap"],
                difficulty=1.5,
                grade=11,
                question_type="填空题",
                stem_file=None,
                answer_file=None,
                solution_file=solution_file,
                replace_tags=True,
                verify=True,
            ),
        )
        self.assertEqual(payload["verified"], 1)
        self.assertEqual(payload["knowledge_codes"], ["derivatives"])

    def test_imported_record_cannot_self_verify(self):
        record = {
            "stem": "外部记录不得自行标记为已验证",
            "answer": "A",
            "grade": 12,
            "question_type": "选择题",
            "difficulty": 3,
            "verified": True,
        }
        result = notebook.import_records(
            self.conn, [record], "外部测试", None, "User-Provided-Authorized", False
        )
        self.assertEqual(result["inserted"], 1)
        stored = self.conn.execute(
            "SELECT verified,solution FROM questions WHERE source_name='外部测试'"
        ).fetchone()
        self.assertEqual(stored["verified"], 0)
        self.assertIsNone(stored["solution"])

    def test_repair_embedded_options(self):
        record = {
            "stem": "测试题（ ）\n\nA. 甲\n\nB. 乙\n\nC. 丙\n\nD. 丁",
            "answer": "A",
            "solution": "选择甲。",
            "grade": 12,
            "question_type": "单选题",
            "difficulty": 2,
        }
        notebook.import_records(
            self.conn, [record], "选项测试", None, "Project-Original", True
        )
        result = notebook.repair_embedded_options(self.conn, verified_only=True)
        self.assertEqual(result["updated"], 1)
        stored = self.conn.execute(
            "SELECT options_json FROM questions WHERE source_name='选项测试'"
        ).fetchone()
        self.assertEqual(json.loads(stored["options_json"]), ["甲", "乙", "丙", "丁"])

    def test_repair_options_from_raw_import_field(self):
        raw_record = {
            "stem": "Raw-options question",
            "options_json": json.dumps(["A. one", "B. two", "C. three", "D. four"]),
            "answer": "A",
            "solution": "Choose A.",
            "grade": 12,
            "question_type": "single choice",
            "difficulty": 2,
        }
        notebook.import_records(
            self.conn, [raw_record], "raw-options-source", None, "User-Provided", False
        )
        result = notebook.repair_embedded_options(
            self.conn, verified_only=False, source_name="raw-options-source"
        )
        self.assertEqual(result["updated"], 1)
        stored = self.conn.execute(
            "SELECT options_json FROM questions WHERE source_name='raw-options-source'"
        ).fetchone()
        self.assertEqual(
            json.loads(stored["options_json"]),
            ["A. one", "B. two", "C. three", "D. four"],
        )

    def test_audit_packet_and_structured_verification(self):
        notebook.import_records(
            self.conn,
            [{
                "stem": "已知圆与直线相切，求圆的方程。",
                "answer": "x^2+y^2=1",
                "grade": 11,
                "question_type": "解答题",
                "difficulty": 2.5,
                "knowledge_codes": ["line-circle"],
            }],
            "用户授权测试卷",
            None,
            "User-Provided-Authorized",
            False,
        )
        question_id = self.conn.execute(
            "SELECT id FROM questions WHERE source_name='用户授权测试卷'"
        ).fetchone()[0]
        packet = notebook.audit_item(self.conn, question_id)
        self.assertEqual(packet["question"]["id"], question_id)
        self.assertIn("missing_solution", packet["automatic_checks"]["issues"])

        review = {
            "question_id": question_id,
            "verdict": "pass",
            "reviewer": "unit-test",
            "packet_sha256": packet["packet_sha256"],
            "checklist": {
                "stem_complete": True,
                "source_checked": True,
                "duplicate_checked": True,
                "answer_derived": True,
                "solution_checked": True,
            },
            "independent_answer": "x^2+y^2=1",
            "independent_solution": "由圆心和半径得到 $x^2+y^2=1$。",
            "answer_check": "match",
            "solution_check": "completed",
            "knowledge_codes": ["line-circle"],
            "target_causes": ["method_choice"],
            "feature_codes": ["tangent", "coordinate-method"],
            "grade": 11,
            "difficulty": 2.5,
            "question_type": "解答题",
            "correction": {},
        }
        review_path = self.root / "review.json"
        review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8-sig")
        result = notebook.apply_verification_review(self.conn, question_id, review_path)
        self.assertEqual(result["verified"], 1)
        self.assertIn("tangent", result["feature_codes"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM verification_reviews WHERE question_id=?", (question_id,)
            ).fetchone()[0],
            1,
        )

    def test_prepare_audit_batch_creates_pending_skeletons_without_db_write(self):
        notebook.import_records(
            self.conn,
            [{
                "stem": "求函数 $f(x)=x^2$ 的导数。",
                "answer": "$2x$",
                "solution": "由幂函数求导公式得到。",
                "knowledge_codes": ["derivatives"],
                "target_causes": ["knowledge_gap"],
            }],
            "审核批次脚手架测试", None, "User-Provided-Authorized", False,
        )
        before = self.conn.execute(
            "SELECT COUNT(*) FROM verification_reviews"
        ).fetchone()[0]
        out_dir = self.root / "audit-work"
        result = notebook.prepare_audit_batch(
            self.conn, "审核批次脚手架测试", 5, out_dir, "unit-test", False
        )
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        packet = json.loads(
            Path(manifest["items"][0]["packet"]).read_text(encoding="utf-8")
        )
        review_path = Path(manifest["items"][0]["review"])
        review = json.loads(review_path.read_text(encoding="utf-8"))
        after = self.conn.execute(
            "SELECT COUNT(*) FROM verification_reviews"
        ).fetchone()[0]
        self.assertEqual(result["prepared"], 1)
        self.assertEqual(review["verdict"], "pending")
        self.assertEqual(
            packet["packet_sha256"],
            notebook.audit_item(
                self.conn, manifest["items"][0]["question_id"]
            )["packet_sha256"],
        )
        self.assertFalse(review["checklist"]["answer_derived"])
        self.assertEqual(before, after)

    def test_prepare_audit_batch_creates_opaque_png_visual_preview(self):
        from PIL import Image as PILImage

        source = self.root / "transparent-audit.png"
        image = PILImage.new("RGBA", (20, 10), (0, 0, 0, 0))
        image.putpixel((8, 5), (0, 0, 0, 255))
        image.save(source)
        notebook.import_records(
            self.conn,
            [{
                "stem": f"观察图形。![图]({source.as_posix()})",
                "answer": "1",
                "solution": "由图可得。",
                "knowledge_codes": ["algebra-operations"],
                "target_causes": ["knowledge_gap"],
            }],
            "透明图审核预览测试", None, "User-Provided-Authorized", False,
        )
        out_dir = self.root / "audit-png-work"
        result = notebook.prepare_audit_batch(
            self.conn, "透明图审核预览测试", 5, out_dir, "unit-test", False
        )
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        visual = manifest["items"][0]["visual_review_images"][0]
        self.assertEqual(visual["status"], "opaque_preview")
        with PILImage.open(visual["review_path"]) as preview:
            self.assertEqual(preview.mode, "RGB")
            self.assertGreaterEqual(min(preview.getpixel((0, 0))), 250)
            self.assertLessEqual(max(preview.getpixel((8, 5))), 5)

    def test_simplified_verification_queue_uses_import_date(self):
        notebook.import_records(
            self.conn,
            [
                {
                    "stem": "简化通道测试题：求 $1+1$。",
                    "answer": "$2$",
                    "solution": "直接计算得 $2$。",
                    "knowledge_codes": ["algebra-operations"],
                },
                {
                    "stem": "完整通道测试题：求 $2+2$。",
                    "answer": "$4$",
                    "solution": "直接计算得 $4$。",
                    "knowledge_codes": ["algebra-operations"],
                },
            ],
            "验证日期筛选测试", None, "User-Provided-Authorized", False,
        )
        rows = self.conn.execute(
            "SELECT id,stem FROM questions WHERE source_name='验证日期筛选测试'"
        ).fetchall()
        ids = {row["stem"].split("：", 1)[0]: row["id"] for row in rows}
        self.conn.execute(
            "UPDATE questions SET created_at='2026-07-20T00:00:00+08:00' WHERE id=?",
            (ids["简化通道测试题"],),
        )
        self.conn.execute(
            "UPDATE questions SET created_at='2026-07-19T23:59:59+08:00' WHERE id=?",
            (ids["完整通道测试题"],),
        )
        self.conn.commit()

        queue = notebook.audit_queue(
            self.conn, 10, "验证日期筛选测试", simplified_only=True
        )
        self.assertEqual([row["id"] for row in queue], [ids["简化通道测试题"]])
        self.assertEqual(queue[0]["verification_mode"], "simplified")
        self.assertEqual(
            notebook.audit_item(
                self.conn, ids["完整通道测试题"]
            )["verification_mode"],
            "full",
        )
        summary = notebook.audit_summary(self.conn)
        self.assertGreaterEqual(summary["simplified_eligible"], 1)
        self.assertGreaterEqual(summary["full_review_required"], 1)

    def test_agent_context_and_handoff_are_compact_read_only_snapshots(self):
        context = notebook.agent_context(
            self.conn, self.db, ROOT, "verify"
        )
        self.assertEqual(context["task"], "verify")
        self.assertTrue(any("UTF-8" in rule for rule in context["critical_rules"]))
        self.assertIn("required_reference", context)
        self.assertIn("prepare-audit-batch", " ".join(context["commands"]))
        self.assertEqual(
            context["simplified_verification_policy"]["created_from"],
            "2026-07-20",
        )
        handoff = notebook.handoff_snapshot(self.conn, self.db, ROOT)
        self.assertIn("unverified_issues", handoff)
        self.assertIn("simplified_eligible", handoff["verification_workload"])
        self.assertLessEqual(len(handoff["top_pending_sources"]), 5)
        self.assertEqual(handoff["defaults"], {"answers": False, "print": False})
        health = notebook.doctor(self.conn, self.db, ROOT)
        self.assertTrue(health["checks"]["utf8_project_files"])
        self.assertNotIn("deepseek_harness", health)
        self.assertEqual(health["text_encoding"]["policy"], "UTF-8")
        self.assertEqual(
            health["text_encoding"]["powershell_read_command"],
            "Get-Content -Raw -Encoding UTF8 <path>",
        )
        grade_context = notebook.agent_context(
            self.conn, self.db, ROOT, "grade"
        )
        self.assertFalse(any("deepseek" in command.lower() for command in grade_context["commands"]))
        self.assertTrue(
            any("下一步" in rule for rule in grade_context["critical_rules"])
        )
        self.assertTrue(
            any("UTF-8" in rule for rule in grade_context["critical_rules"])
        )

    def test_verification_requires_all_review_checks(self):
        notebook.import_records(
            self.conn,
            [{
                "stem": "求 $f(x)=x^2$ 的导数。",
                "answer": "$2x$",
                "solution": "由幂函数求导公式得到 $2x$。",
                "knowledge_codes": ["derivatives"],
            }],
            "审核约束测试",
            None,
            "User-Provided-Authorized",
            False,
        )
        question_id = self.conn.execute(
            "SELECT id FROM questions WHERE source_name='审核约束测试'"
        ).fetchone()[0]
        review = {
            "question_id": question_id,
            "verdict": "pass",
            "reviewer": "unit-test",
            "packet_sha256": notebook.audit_item(
                self.conn, question_id
            )["packet_sha256"],
            "checklist": {"stem_complete": True},
            "independent_answer": "$2x$",
            "independent_solution": "独立求导。",
            "answer_check": "match",
            "solution_check": "match",
            "knowledge_codes": ["derivatives"],
            "grade": 12,
            "difficulty": 2,
            "question_type": "解答题",
        }
        review_path = self.root / "incomplete-review.json"
        review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checklist not confirmed"):
            notebook.apply_verification_review(self.conn, question_id, review_path)
        verified = self.conn.execute(
            "SELECT verified FROM questions WHERE id=?", (question_id,)
        ).fetchone()[0]
        self.assertEqual(verified, 0)

    def test_verification_review_batch_uses_item_level_verifier(self):
        notebook.import_records(
            self.conn,
            [
                {
                    "id": "batch-pass",
                    "stem": "求 $f(x)=x^2$ 的导数。",
                    "answer": "$2x$",
                    "solution": "由幂函数求导公式得到 $2x$。",
                    "knowledge_codes": ["derivatives"],
                    "target_causes": ["knowledge_gap"],
                    "grade": 11,
                    "question_type": "解答题",
                    "difficulty": 1.5,
                },
                {
                    "id": "batch-revise",
                    "stem": "占位审核题。",
                    "answer": "需补全",
                    "solution": "需补全。",
                    "knowledge_codes": ["derivatives"],
                },
            ],
            "批量审核测试", None, "User-Provided-Authorized", False,
        )
        rows = self.conn.execute(
            "SELECT id,stem FROM questions WHERE source_name='批量审核测试' ORDER BY stem"
        ).fetchall()
        ids = {row["stem"]: row["id"] for row in rows}
        pass_id = ids["求 $f(x)=x^2$ 的导数。"]
        revise_id = ids["占位审核题。"]
        # A later adverse review must revoke an earlier verified state.
        self.conn.execute("UPDATE questions SET verified=1 WHERE id=?", (revise_id,))
        self.conn.commit()
        pass_review = {
            "question_id": pass_id,
            "verdict": "pass",
            "reviewer": "unit-test",
            "packet_sha256": notebook.audit_item(
                self.conn, pass_id
            )["packet_sha256"],
            "checklist": {
                "stem_complete": True,
                "source_checked": True,
                "duplicate_checked": True,
                "answer_derived": True,
                "solution_checked": True,
            },
            "independent_answer": "$2x$",
            "independent_solution": "独立使用幂函数求导公式得 $2x$。",
            "answer_check": "match",
            "solution_check": "match",
            "knowledge_codes": ["derivatives"],
            "target_causes": ["knowledge_gap"],
            "feature_codes": ["formula-substitution"],
            "grade": 11,
            "difficulty": 1.5,
            "question_type": "解答题",
            "correction": {},
        }
        revise_review = {
            "question_id": revise_id,
            "verdict": "needs_revision",
            "reviewer": "unit-test",
            "packet_sha256": notebook.audit_item(
                self.conn, revise_id
            )["packet_sha256"],
            "review_note": "答案和解析均为占位文本。",
        }
        pass_path = self.root / "pass.review.json"
        revise_path = self.root / "revise.review.json"
        pass_path.write_text(json.dumps(pass_review, ensure_ascii=False), encoding="utf-8")
        revise_path.write_text(json.dumps(revise_review, ensure_ascii=False), encoding="utf-8")
        manifest = self.root / "review-manifest.json"
        manifest.write_text(
            json.dumps({
                "items": [
                    {"question_id": pass_id, "review": str(pass_path)},
                    {"question_id": revise_id, "review": str(revise_path)},
                ]
            }, ensure_ascii=False),
            encoding="utf-8-sig",
        )
        result = notebook.apply_verification_review_batch(self.conn, manifest)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["verified"], 1)
        self.assertEqual(result["needs_revision"], 1)
        self.assertEqual(result["failed"], 0)
        revise_verified = self.conn.execute(
            "SELECT verified FROM questions WHERE id=?", (revise_id,)
        ).fetchone()[0]
        self.assertEqual(revise_verified, 0)

    def test_prepare_review_batch_expands_concise_decision_without_writing_db(self):
        row = self.conn.execute("SELECT id,grade FROM questions LIMIT 1").fetchone()
        question_id = row["id"]
        packet_sha256 = notebook.audit_item(
            self.conn, question_id
        )["packet_sha256"]
        before = self.conn.execute("SELECT COUNT(*) FROM verification_reviews").fetchone()[0]
        decisions = self.root / "decisions.json"
        decisions.write_text(json.dumps({
            "reviewer": "unit-test",
            "items": [{
                "question_id": question_id,
                "verdict": "pass",
                "packet_sha256": packet_sha256,
                "checks_confirmed": True,
                "independent_answer": "独立答案",
                "independent_solution": "独立推导过程",
                "answer_check": "match",
                "solution_check": "match",
                "review_note": "逐项检查完成",
            }],
        }, ensure_ascii=False), encoding="utf-8-sig")
        out_dir = self.root / "expanded-reviews"
        result = notebook.prepare_verification_reviews(self.conn, decisions, out_dir)
        review = json.loads(next(out_dir.glob("*.review.json")).read_text(encoding="utf-8"))
        after = self.conn.execute("SELECT COUNT(*) FROM verification_reviews").fetchone()[0]
        self.assertEqual(result["prepared"], 1)
        self.assertTrue(review["checklist"]["answer_derived"])
        self.assertEqual(review["grade"], row["grade"])
        self.assertEqual(before, after)

    def test_stale_verification_review_is_rejected_without_writing(self):
        row = self.conn.execute("SELECT id FROM questions LIMIT 1").fetchone()
        question_id = row["id"]
        packet_sha256 = notebook.audit_item(
            self.conn, question_id
        )["packet_sha256"]
        review = {
            "question_id": question_id,
            "verdict": "needs_revision",
            "reviewer": "unit-test",
            "packet_sha256": packet_sha256,
            "review_note": "旧审核不得覆盖新状态。",
        }
        review_path = self.root / "stale.review.json"
        review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
        self.conn.execute(
            "UPDATE questions SET difficulty=difficulty+0.1 WHERE id=?",
            (question_id,),
        )
        self.conn.commit()

        with self.assertRaisesRegex(ValueError, "stale review snapshot"):
            notebook.apply_verification_review(self.conn, question_id, review_path)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM verification_reviews WHERE question_id=?",
                (question_id,),
            ).fetchone()[0],
            0,
        )

    def test_stale_concise_decision_is_rejected(self):
        row = self.conn.execute("SELECT id FROM questions LIMIT 1").fetchone()
        question_id = row["id"]
        packet_sha256 = notebook.audit_item(
            self.conn, question_id
        )["packet_sha256"]
        decisions = self.root / "stale-decisions.json"
        decisions.write_text(json.dumps({
            "reviewer": "unit-test",
            "items": [{
                "question_id": question_id,
                "verdict": "needs_revision",
                "packet_sha256": packet_sha256,
                "review_note": "旧决策不得扩展。",
            }],
        }, ensure_ascii=False), encoding="utf-8")
        self.conn.execute(
            "UPDATE questions SET difficulty=difficulty+0.1 WHERE id=?",
            (question_id,),
        )
        self.conn.commit()

        with self.assertRaisesRegex(ValueError, "stale decision snapshot"):
            notebook.prepare_verification_reviews(
                self.conn, decisions, self.root / "stale-expanded"
            )

    def test_structural_features_are_used_in_recommendation_reason(self):
        notebook.import_records(
            self.conn,
            [{
                "stem": "唯一结构标志：已知复数参数，讨论轨迹。",
                "answer": "单位圆",
                "solution": "化为模长等式可得单位圆。",
                "knowledge_codes": ["complex-numbers"],
                "feature_codes": ["locus"],
                "target_causes": ["method_choice"],
                "difficulty": 3,
            }],
            "结构推荐测试",
            None,
            "Project-Original",
            True,
        )
        analysis = {
            "problem_text": "讨论复数参数对应点的轨迹。",
            "student_answer": "",
            "correct_answer": "单位圆",
            "correct_solution": "由模长等式得到。",
            "first_wrong_step": "未识别轨迹条件",
            "cause_code": "method_choice",
            "cause_detail": "未选择复平面轨迹方法",
            "evidence": ["未建立模长等式"],
            "knowledge_codes": ["complex-numbers"],
            "feature_codes": ["locus"],
            "difficulty": 3,
            "confidence": 0.9,
        }
        error_id = notebook.record_error(self.conn, analysis, self.root, False)
        items = notebook.recommend(
            self.conn,
            error_id,
            1,
            False,
            self.root,
            keywords=["唯一结构标志"],
            features=["locus"],
        )
        self.assertEqual(items[0]["source_name"], "结构推荐测试")
        self.assertIn("结构特征", items[0]["reason"])

    def test_practice_sheet_preserves_nested_math_structure(self):
        source = (
            r"$|MN|=2\sqrt{r^{2}-d^{2}}$，"
            r"$S_{\triangle AMN}=\frac{1}{2}|MN|d$，"
            r"$\angle ACB=\frac{\pi}{2}$"
        )
        rendered = practice_sheet.clean_math(source)
        tokens = re.findall(r"ZZMATH\d{4}ZZ", rendered)
        self.assertEqual(len(tokens), 3)
        self.assertEqual(rendered, "，".join(tokens))
        self.assertEqual(
            [practice_sheet._MATH_REGISTRY[t] for t in tokens],
            [
                (r"|MN|=2\sqrt{r^{2}-d^{2}}", False),
                (r"S_{\triangle AMN}=\frac{1}{2}|MN|d", False),
                (r"\angle ACB=\frac{\pi}{2}", False),
            ],
        )
        single = practice_sheet.clean_math(r"$S=\frac12 ABd$")
        self.assertRegex(single, r"^ZZMATH\d{4}ZZ$")
        self.assertEqual(practice_sheet._MATH_REGISTRY[single], (r"S=\frac12 ABd", False))

    def test_practice_sheet_supports_common_math_delimiters_and_escaped_dollar(self):
        rendered = practice_sheet.clean_math(
            r"价格为 \$5，行内 \(x+1\)，展示 \[\frac{1}{2}\]，以及 $$y^2$$。"
        )
        tokens = re.findall(r"ZZMATH\d+ZZ", rendered)
        self.assertEqual(len(tokens), 3)
        self.assertIn("$5", rendered)
        registered = [practice_sheet._MATH_REGISTRY[token] for token in tokens]
        self.assertEqual(
            registered,
            [("x+1", False), (r"\frac{1}{2}", True), ("y^2", True)],
        )

    def test_practice_sheet_extracts_bare_latex_fractions(self):
        rendered = practice_sheet.clean_math(
            r"A. \frac{\sqrt6}{2}；B. \frac{17}{16}；日期 2025/2026"
        )
        tokens = re.findall(r"ZZMATH\d+ZZ", rendered)
        self.assertEqual(len(tokens), 2)
        self.assertEqual(
            [practice_sheet._MATH_REGISTRY[token] for token in tokens],
            [(r"\frac{\sqrt6}{2}", False), (r"\frac{17}{16}", False)],
        )
        self.assertIn("日期 2025/2026", rendered)
        self.assertNotIn("(17)/(16)", rendered)

    def test_practice_sheet_truncation_keeps_math_token_atomic(self):
        tokenized = practice_sheet.clean_math(r"前文 $\frac{1}{2}$ 后文")
        token = re.search(r"ZZMATH\d+ZZ", tokenized).group(0)
        truncated, changed = practice_sheet.truncate_clean_text(tokenized, len("前文 ") + 2)
        self.assertTrue(changed)
        self.assertNotIn("ZZMATH", truncated)
        self.assertNotIn(token[:5], truncated)

    def test_practice_sheet_renders_uncached_formula(self):
        latex = r"\frac{987654321}{\sqrt{123456789}}"
        url, width, height = practice_sheet._render_math_image(latex, 11.37)
        self.assertTrue(Path(url).is_file())
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)

    def test_practice_sheet_renders_sqrt_as_inline_image(self):
        html_text = practice_sheet.paragraph_text(r"最短路径为 $\sqrt{41}-1$")
        self.assertIn("<img src=", html_text)
        self.assertIn(".png", html_text)
        self.assertNotIn("ZZMATH", html_text)

    def test_practice_sheet_normalizes_bare_math_args(self):
        cases = {
            r"\frac12": r"\frac{1}{2}",
            r"\frac{\sqrt{10}}5": r"\frac{\sqrt{10}}{5}",
            r"\frac{\sqrt6}{2}": r"\frac{\sqrt{6}}{2}",
            r"\dfrac\pi2": r"\dfrac{\pi}{2}",
            r"S=\frac12 ABd": r"S=\frac{1}{2} ABd",
            r"\sqrt3\sin x": r"\sqrt{3}\sin x",
            r"\vec a": r"\vec{a}",
            r"\frac{1}{2}": r"\frac{1}{2}",
            r"\sqrt[3]{8}": r"\sqrt[3]{8}",
        }
        for source, expected in cases.items():
            self.assertEqual(practice_sheet._normalize_math_args(source), expected)

    def test_practice_sheet_normalizes_slash_fractions(self):
        cases = {
            r"x^2/a^2+y^2/b^2=1": r"\frac{x^2}{a^2}+\frac{y^2}{b^2}=1",
            r"\sqrt3a/12": r"\frac{\sqrt{3}a}{12}",
            r"(0,\sqrt3/2]": r"(0,\frac{\sqrt{3}}{2}]",
            r"x^2/5+y^2=1": r"\frac{x^2}{5}+y^2=1",
            r"(a+b)/c": r"\frac{(a+b)}{c}",
            r"x/(a+b)": r"\frac{x}{(a+b)}",
            r"\frac{1}{2}": r"\frac{1}{2}",
            r"\mathrm{m/s}": r"\mathrm{m/s}",
        }
        for source, expected in cases.items():
            self.assertEqual(practice_sheet._normalize_render_latex(source), expected)

        # Slash normalization is confined to extracted math, never prose/date text.
        self.assertEqual(practice_sheet.clean_math("日期 2025/2026"), "日期 2025/2026")

    def test_practice_sheet_normalizes_line_symbol_to_ell(self):
        cases = {
            "l": r"\ell",
            r"l_1\perp l_2": r"\ell_1\perp \ell_2",
            r"y=k l+b": r"y=k \ell+b",
            r"\lim_{x\to0}x": r"\lim_{x\to0}x",
            r"\left(l+1\right)": r"\left(\ell+1\right)",
            r"\mathrm{l}": r"\mathrm{l}",
            r"\ell": r"\ell",
        }
        for source, expected in cases.items():
            self.assertEqual(practice_sheet._normalize_line_symbol(source), expected)

        # Both spellings must reach the PDF renderer as the same canonical
        # LaTeX, without requiring the native PDF runtime in this unit test.
        self.assertEqual(
            practice_sheet._normalize_render_latex("l"),
            practice_sheet._normalize_render_latex(r"\ell"),
        )
        self.assertEqual(practice_sheet._latex_to_text(r"\ell"), "ℓ")

    def test_practice_sheet_renders_tex_shorthand_as_images(self):
        # 题库中存在 TeX 简写（单 token 参数不带花括号），mathtext 原生拒绝，
        # 规范化后必须走图片渲染而不是退回 "frac√105" 式文本。
        for latex in (r"e=\frac{\sqrt{10}}5", r"\frac12", r"\sqrt3", r"\vec a"):
            url, width, height = practice_sheet._render_math_image(latex, 11.37)
            self.assertTrue(Path(url).is_file())
            self.assertGreater(width, 0)
            self.assertGreater(height, 0)
        html_text = practice_sheet.paragraph_text(
            r"若椭圆$\frac{x^2}{5}+\frac{y^2}{m}=1$的离心率为$e=\frac{\sqrt{10}}5$，求实数$m$。"
        )
        self.assertIn("<img src=", html_text)
        self.assertNotIn("frac", html_text)
        self.assertNotIn("√", html_text)

    def test_practice_sheet_renders_fullwidth_punct_math_as_image(self):
        # 数学段内混入全角标点不应整段退回文本式写法。
        html_text = practice_sheet.paragraph_text(
            r"点$\left(\sqrt{2}，\frac{\sqrt{2}}{2}\right)$在椭圆上"
        )
        self.assertIn("<img src=", html_text)
        self.assertNotIn("√", html_text)
        # 含真实中文的数学段仍然回退纯文本。
        self.assertNotIn("<img", practice_sheet.paragraph_text(r"$点P在圆外$"))

    def test_practice_sheet_falls_back_for_cjk_math(self):
        html_text = practice_sheet.paragraph_text(r"段 $点P在圆外$ 回退")
        self.assertNotIn("<img", html_text)
        self.assertIn("点P在圆外", html_text)

    def test_practice_sheet_pdf_defaults_to_questions_only(self):
        from pypdf import PdfReader

        error_id = self.create_error()
        notebook.recommend(self.conn, error_id, 3, True, self.root)
        error, _knowledge_codes = notebook.fetch_error(self.conn, error_id)
        rows = self.conn.execute(
            """SELECT r.rank,r.reason,q.id AS question_id,q.stem,q.options_json,
                      q.answer,q.solution,q.difficulty,q.source_name
               FROM recommendations r JOIN questions q ON q.id=r.question_id
               WHERE r.error_id=? AND q.verified=1 ORDER BY r.rank""",
            (error_id,),
        ).fetchall()
        self.assertGreaterEqual(len(rows), 1)
        knowledge_names = ["三角恒等变换"]

        default_pdf = self.root / "practice-default.pdf"
        practice_sheet.create_pdf(
            default_pdf, error_id, error, rows, 1200,
            knowledge_names=knowledge_names,
        )
        text = "\n".join(
            page.extract_text() for page in PdfReader(str(default_pdf)).pages
        )
        self.assertIn("李兆霖数学错题本", text)
        self.assertIn("错因针对性练习", text)
        self.assertIn("错题原题", text)
        self.assertIn("知识点", text)
        self.assertIn("三角恒等变换", text)
        self.assertNotIn("错因分析", text)
        self.assertNotIn("作答提醒", text)
        self.assertNotIn("答案与解析", text)

        with_answers_pdf = self.root / "practice-answers.pdf"
        practice_sheet.create_pdf(
            with_answers_pdf, error_id, error, rows, 1200,
            include_answers=True,
            knowledge_names=knowledge_names,
        )
        a_text = "\n".join(
            page.extract_text() for page in PdfReader(str(with_answers_pdf)).pages
        )
        self.assertIn("错题原题", a_text)
        self.assertIn("答案与解析", a_text)

    def test_exam_packet_loads_verified_questions_and_renders_without_answers(self):
        from pypdf import PdfReader

        question_id = self.conn.execute(
            "SELECT id FROM questions WHERE verified=1 ORDER BY id LIMIT 1"
        ).fetchone()[0]
        packet = self.root / "exam-packet.json"
        packet.write_text(json.dumps({
            "schema": "math-exam-packet/v1",
            "exam_id": "test-midterm",
            "title": "高二上学期数学期中考试卷",
            "duration_minutes": 120,
            "total_score": 10,
            "knowledge": ["空间向量与立体几何"],
            "show_provenance": False,
            "sections": [{
                "title": "一、测试题",
                "instruction": "写出必要步骤。",
                "questions": [{
                    "question_id": question_id,
                    "score": 10,
                    "reason": "已验证期中范围题",
                    "answer_space_mm": 5,
                }],
            }],
        }, ensure_ascii=False), encoding="utf-8")

        meta, items, knowledge, exam_id = practice_sheet.load_exam_packet(
            self.db, packet
        )
        self.assertEqual(exam_id, "test-midterm")
        self.assertEqual(meta["total_score"], 10)
        self.assertEqual(items[0]["question_id"], question_id)
        self.assertEqual(knowledge, ["空间向量与立体几何"])

        output = self.root / "exam.pdf"
        practice_sheet.create_pdf(
            output,
            exam_id,
            {"problem_text": "", "cause_code": None},
            items,
            1200,
            knowledge_names=knowledge,
            exam_meta=meta,
        )
        text = "\n".join(
            page.extract_text() for page in PdfReader(str(output)).pages
        )
        self.assertIn("高二上学期数学期中考试卷", text)
        self.assertIn("满分：10 分", text)
        self.assertNotIn("答案与解析", text)

    def test_practice_sheet_split_stem_images(self):
        cleaned, paths = practice_sheet.split_stem_images(
            r"如图1 ![原题图](data/imports/x/media/image1.png) 所示，![图](a b.png) 完"
        )
        self.assertEqual(paths, ["data/imports/x/media/image1.png", "a b.png"])
        self.assertNotIn("![", cleaned)
        self.assertNotIn("题图见原题", practice_sheet.clean_math(cleaned))

    def test_practice_sheet_diagram_is_cropped_and_never_upscaled(self):
        from PIL import Image as PILImage, ImageDraw

        source = self.root / "diagram-with-margin.png"
        image = PILImage.new("RGB", (600, 400), "white")
        ImageDraw.Draw(image).rectangle((260, 170, 340, 230), outline="black", width=4)
        image.save(source)
        prepared_dir = self.root / "prepared"
        result = practice_sheet.prepare_diagram_image(source, prepared_dir, 500, 500)
        self.assertIsNotNone(result)
        prepared, width, height = result
        self.assertTrue(prepared.is_file())
        with PILImage.open(prepared) as cropped:
            self.assertLess(cropped.width, 120)
            self.assertLess(cropped.height, 100)
        self.assertLessEqual(width, 60)
        self.assertLessEqual(height, 50)

    def test_practice_sheet_embeds_stem_diagram(self):
        from PIL import Image as PILImage
        from pypdf import PdfReader

        img_path = self.root / "diagram.png"
        PILImage.new("RGB", (120, 80), "white").save(img_path)

        error_id = self.create_error()
        notebook.recommend(self.conn, error_id, 3, True, self.root)
        qid = self.conn.execute(
            "SELECT question_id FROM recommendations WHERE error_id=? ORDER BY rank LIMIT 1",
            (error_id,),
        ).fetchone()[0]
        original_stem = self.conn.execute(
            "SELECT stem FROM questions WHERE id=?", (qid,)
        ).fetchone()[0]

        def build_rows():
            return self.conn.execute(
                """SELECT r.rank,r.reason,q.id AS question_id,q.stem,q.options_json,
                          q.answer,q.solution,q.difficulty,q.source_name
                   FROM recommendations r JOIN questions q ON q.id=r.question_id
                   WHERE r.error_id=? AND q.verified=1 ORDER BY r.rank""",
                (error_id,),
            ).fetchall()

        def xobject_count(pdf_path):
            total = 0
            for page in PdfReader(str(pdf_path)).pages:
                xobjs = page["/Resources"].get("/XObject")
                if xobjs:
                    total += len(xobjs)
            return total

        error, _knowledge_codes = notebook.fetch_error(self.conn, error_id)

        plain_pdf = self.root / "practice-plain.pdf"
        practice_sheet.create_pdf(plain_pdf, error_id, error, build_rows(), 1200)
        plain_images = xobject_count(plain_pdf)

        self.conn.execute(
            "UPDATE questions SET stem=? WHERE id=?",
            (original_stem + f"\n![原题图]({img_path})", qid),
        )
        self.conn.commit()
        with_img_pdf = self.root / "practice-with-img.pdf"
        practice_sheet.create_pdf(with_img_pdf, error_id, error, build_rows(), 1200)

        text = "\n".join(
            page.extract_text() for page in PdfReader(str(with_img_pdf)).pages
        )
        self.assertNotIn("题图见原题", text)
        self.assertNotIn("题图缺失", text)
        self.assertGreater(xobject_count(with_img_pdf), plain_images)

    def test_photo_ocr_orientation_score_prefers_horizontal_lines(self):
        horizontal = [{
            "text": "x+1=2",
            "confidence": 0.95,
            "box": [[0, 0], [120, 0], [120, 20], [0, 20]],
        }]
        vertical = [{
            "text": "x+1=2",
            "confidence": 0.95,
            "box": [[0, 0], [20, 0], [20, 120], [0, 120]],
        }]
        self.assertGreater(
            photo_ocr.orientation_score(horizontal),
            photo_ocr.orientation_score(vertical),
        )

    def test_photo_ocr_prepare_image_composites_transparency_on_white(self):
        from PIL import Image as PILImage

        source = self.root / "transparent.png"
        image = PILImage.new("RGBA", (40, 20), (0, 0, 0, 0))
        for x in range(10, 30):
            image.putpixel((x, 10), (0, 0, 0, 255))
        image.save(source)

        prepared, original_size = photo_ocr.prepare_image(source, max_side=100)

        self.assertEqual(original_size, (40, 20))
        self.assertGreaterEqual(min(prepared.getpixel((0, 0))), 250)
        self.assertLessEqual(max(prepared.getpixel((20, 10))), 5)

    def test_photo_ocr_preflight_rotates_crops_and_caches(self):
        from PIL import Image as PILImage

        source = self.root / "portrait.jpg"
        PILImage.new("RGB", (300, 900), "white").save(source)

        class FakeResult:
            def __init__(self, horizontal):
                if horizontal:
                    self.boxes = [[[20, 40], [260, 40], [260, 80], [20, 80]]]
                else:
                    self.boxes = [[[20, 40], [60, 40], [60, 280], [20, 280]]]
                self.txts = ("Q-local123 x+1=2",)
                self.scores = (0.93,)

        class FakeEngine:
            def __call__(self, image):
                height, width = image.shape[:2]
                return FakeResult(width > height)

        out_dir = self.root / "ocr"
        result = photo_ocr.process_photos(
            [source],
            self.root,
            out_dir=out_dir,
            max_side=600,
            preview_side=400,
            engine_factory=FakeEngine,
            preflight_mode="ocr",
        )
        self.assertFalse(result["cache_hit"])
        self.assertEqual(result["pages"], 1)
        self.assertGreaterEqual(result["detail_crops"], 1)
        self.assertEqual(result["question_ids"], ["Q-local123"])
        self.assertIn("x+1=2", result["ocr_pages"][0]["text"])
        self.assertNotIn("lines", result["ocr_pages"][0])
        self.assertNotIn("formula_ocr", result["ocr_pages"][0])
        packet = json.loads(Path(result["packet"]).read_text(encoding="utf-8"))
        self.assertIn(packet["pages"][0]["rotation_degrees_ccw"], (90, 270))
        self.assertIn("x+1=2", packet["pages"][0]["ocr_text"])
        self.assertLess(packet["metrics"]["preview_pixel_ratio"], 1)

        cached = photo_ocr.process_photos(
            [source],
            self.root,
            out_dir=out_dir,
            engine_factory=lambda: self.fail("cache should avoid OCR engine"),
            preflight_mode="ocr",
        )
        self.assertTrue(cached["cache_hit"])

    def test_paddle_formula_runner_returns_only_structured_marker(self):
        image = self.root / "formula.jpg"
        image.write_bytes(b"fake")

        class Completed:
            returncode = 0
            stdout = (
                "Paddle informational output\n"
                'FORMULA_OCR_JSON={"status":"ok","device":"gpu:0",'
                '"engine":"test","formulas":[{"path":"formula.jpg",'
                '"latex":"x^2","requires_visual_confirmation":true}]}\n'
            )
            stderr = "ignored warning"

        captured = {}

        def fake_runner(command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs["env"]
            return Completed()

        result = photo_ocr.run_paddle_formula_ocr(
            [image],
            self.root,
            runner=fake_runner,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["device"], "gpu:0")
        self.assertEqual(result["formulas"][0]["latex"], "x^2")
        self.assertIn("--image", captured["command"])
        self.assertEqual(
            captured["env"]["PADDLE_PDX_CACHE_HOME"],
            str((self.root / ".runtime" / "paddle-home" / "paddlex").resolve()),
        )

    def test_photo_ocr_packet_attaches_formula_to_detail_crop(self):
        from PIL import Image as PILImage

        source = self.root / "formula-source.jpg"
        PILImage.new("RGB", (600, 300), "white").save(source)

        class FakeResult:
            boxes = [[[20, 40], [500, 40], [500, 100], [20, 100]]]
            txts = ("x+1=2",)
            scores = (0.93,)

        class FakeEngine:
            def __call__(self, _image):
                return FakeResult()

        def fake_formula(crop_paths, _project_root):
            return {
                "status": "ok",
                "engine": "PaddleOCR test",
                "device": "gpu:0",
                "predict_seconds": 0.1,
                "formulas": [
                    {
                        "path": str(crop_paths[0].resolve()),
                        "latex": "x+1=2",
                        "requires_visual_confirmation": True,
                    }
                ],
            }

        with (
            patch.object(photo_ocr, "_formula_ocr_mode", return_value="paddle"),
            patch.object(
                photo_ocr,
                "run_paddle_formula_ocr",
                side_effect=fake_formula,
            ),
        ):
            result = photo_ocr.process_photos(
                [source],
                self.root,
                out_dir=self.root / "formula-ocr",
                engine_factory=FakeEngine,
                formula_ocr="auto",
                preflight_mode="ocr",
            )
        packet = json.loads(Path(result["packet"]).read_text(encoding="utf-8"))
        self.assertEqual(result["formula_candidates"], 1)
        self.assertEqual(result["formula_ocr_status"], "ok")
        self.assertEqual(packet["pages"][0]["formula_ocr"][0]["latex"], "x+1=2")
        self.assertTrue(
            packet["pages"][0]["formula_ocr"][0][
                "requires_visual_confirmation"
            ]
        )

    def test_import_context_uses_real_docx_cli_arguments(self):
        context = notebook.agent_context(self.conn, self.db, ROOT, "import")
        commands = "\n".join(context["commands"])
        self.assertIn("--json <paragraphs.json>", commands)
        self.assertIn("--relative-dir", commands)
        self.assertIn("import_recent_docx_batch.py", commands)
        self.assertNotIn("--output-dir", commands)

    def test_daily_review_packet_deduplicates_error_and_loads_for_pdf(self):
        error_id = self.create_error()
        notebook.recommend(self.conn, error_id, 2, True, self.root)
        out = self.root / "daily.json"
        result = notebook.daily_review_packet(
            self.conn, date.today() + timedelta(days=31), 12, out
        )
        self.assertEqual(result["selected_errors"], 1)
        self.assertEqual(result["printable_questions"], 2)
        error, items, knowledge, packet_date = practice_sheet.load_daily_packet(
            self.db, out
        )
        self.assertEqual(len(items), 2)
        self.assertIn("今日到期复习", error["problem_text"])
        self.assertTrue(knowledge)
        self.assertEqual(packet_date, (date.today() + timedelta(days=31)).isoformat())

    def test_daily_review_packet_uses_two_recommendations_for_early_stage(self):
        error_id = self.create_error()
        notebook.recommend(self.conn, error_id, 3, True, self.root)
        out = self.root / "daily-early.json"
        result = notebook.daily_review_packet(
            self.conn, date.today() + timedelta(days=1), 12, out
        )
        packet = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(packet["items"][0]["stage"], 1)
        self.assertEqual(packet["items"][0]["required_recommendations"], 2)
        self.assertEqual(result["printable_questions"], 2)
        error, items, _, _ = practice_sheet.load_daily_packet(self.db, out)
        self.assertEqual(len(items), 2)
        self.assertEqual([item["rank"] for item in items], [1, 1])
        self.assertEqual(
            [item["daily_recommendation_index"] for item in items], [1, 2]
        )
        self.assertTrue(items[0]["daily_group_start"])
        self.assertFalse(items[1]["daily_group_start"])
        self.assertEqual(items[0]["daily_problem_text"], items[1]["daily_problem_text"])
        self.assertTrue(all("错题回顾" not in item["stem"] for item in items))
        self.assertTrue(all("同类练习" not in item["stem"] for item in items))
        self.assertEqual(
            practice_sheet.daily_group_heading(items[0]),
            f"1　错题编号 {error_id}",
        )
        self.assertEqual(
            practice_sheet.daily_recommendation_heading(items[1]),
            f"同类型推荐题 2　题库编号 {items[1]['question_id']}"
            f"（难度 {items[1]['difficulty']}/5）",
        )
        self.assertNotRegex(
            practice_sheet.daily_recommendation_heading(items[1]), r"^\d+"
        )
        self.assertIn("共 1 组，含 2 道练习", error["problem_text"])

    def test_daily_review_packet_keeps_original_when_recommendations_are_missing(self):
        error_id = self.create_error()
        out = self.root / "daily-original-only.json"
        result = notebook.daily_review_packet(
            self.conn, date.today() + timedelta(days=1), 12, out
        )
        self.assertEqual(result["printable_questions"], 0)
        self.assertEqual(result["missing_reviewed_recommendations"], [error_id])
        _, items, _, _ = practice_sheet.load_daily_packet(self.db, out)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["question_id"], error_id)
        self.assertIn("错题回顾", items[0]["stem"])
        self.assertEqual(items[0]["rank"], 1)
        self.assertEqual(items[0]["daily_recommendation_index"], 0)
        self.assertEqual(practice_sheet.identifier_label(error_id), "错题编号")
        self.assertEqual(practice_sheet.identifier_label("Q-example"), "题库编号")

    def test_recoverable_workflow_enforces_order_and_is_idempotent(self):
        started = notebook.workflow_start(self.root, "grade", "photo batch")
        workflow_id = started["workflow_id"]
        with self.assertRaisesRegex(ValueError, "previous workflow steps"):
            notebook.workflow_update(
                self.root, workflow_id, "model_review", "complete", None, None
            )
        first = notebook.workflow_update(
            self.root, workflow_id, "photo_preflight", "complete", "ocr.json", "ok"
        )
        again = notebook.workflow_update(
            self.root, workflow_id, "photo_preflight", "complete", "ocr.json", "ok"
        )
        self.assertEqual(first["next_step"], "model_review")
        status = notebook.workflow_status(self.root, workflow_id)
        self.assertEqual(status["steps"][0]["artifacts"], ["ocr.json"])
        self.assertEqual(again["next_step"], "model_review")

    def test_behavior_cases_are_machine_readable(self):
        listing = notebook.behavior_cases("grade", None)
        self.assertGreaterEqual(listing["count"], 4)
        case = notebook.behavior_cases(None, "grade-careless-evidence")
        self.assertIn("粗心", case["title"])
        self.assertTrue(case["forbidden"])

    def test_fts_index_search_and_trigger_update(self):
        status = notebook.rebuild_search_index(self.conn, self.db, self.root)
        self.assertTrue(status["current"])
        args = Namespace(
            knowledge=None, grade=None, difficulty_min=None, difficulty_max=None,
            text="所有实数", verified=True, limit=20, full=False,
        )
        results = notebook.search_questions(self.conn, args)
        self.assertTrue(results)
        before = notebook.search_index_status(self.conn)["indexed_questions"]
        notebook.import_records(
            self.conn,
            [{"stem": "三角函数恒等式测试题", "answer": "1", "solution": "直接计算。"}],
            "FTS测试", None, "Project-Original", True,
        )
        after = notebook.search_index_status(self.conn)["indexed_questions"]
        self.assertEqual(after, before + 1)


if __name__ == "__main__":
    unittest.main()
