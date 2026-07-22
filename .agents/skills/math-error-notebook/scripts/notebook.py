#!/usr/bin/env python3
"""Deterministic storage and retrieval for the math-error-notebook skill."""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


CAUSE_CODES = {
    "knowledge_gap": "知识点未掌握",
    "concept_confusion": "概念理解不准确",
    "formula_condition": "公式或定理使用条件遗漏",
    "method_choice": "解题思路选择错误",
    "reasoning_gap": "推理或步骤跳跃",
    "algebra_transform": "代数变形错误",
    "calculation": "计算错误",
    "misreading": "审题错误",
    "incomplete_cases": "漏解或分类不完整",
    "expression": "表达或书写不规范",
    "careless": "有证据支持的粗心错误",
    "unclear": "信息不足，暂不能判断",
}
FEATURE_CODES = {
    "single-choice": "单项选择",
    "multiple-choice": "多项选择",
    "fill-blank": "填空",
    "solution": "解答",
    "proof": "证明",
    "multi-part": "多问综合",
    "parameter-range": "参数范围",
    "existence": "存在性",
    "uniqueness": "唯一性",
    "maximum-minimum": "最值",
    "tangent": "相切/切线",
    "symmetry": "对称",
    "locus": "轨迹",
    "intersection": "交点/交集",
    "coordinate-method": "坐标法",
    "vector-method": "向量法",
    "discriminant": "判别式",
    "case-analysis": "分类讨论",
    "derivative-analysis": "导数分析",
    "function-analysis": "函数性质分析",
    "formula-substitution": "公式代入",
    "recurrence": "递推",
    "summation": "求和",
    "counting": "计数",
    "diagram-dependent": "依赖图形",
}
REVIEW_INTERVALS = (1, 2, 4, 7, 15, 30)
SCHEMA_VERSION = 2
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent


def default_database_path() -> Path:
    """Bind a project skill to its project's canonical DB, even from a subdirectory."""
    if SKILL_DIR.parent.name == "skills" and SKILL_DIR.parent.parent.name == ".agents":
        return SKILL_DIR.parents[2] / "data" / "math_notebook.db"
    return Path("data/math_notebook.db")


DEFAULT_DB = default_database_path()
DEFAULT_KNOWLEDGE = SKILL_DIR / "assets/knowledge-points.json"
DEFAULT_SEED = SKILL_DIR / "assets/seed-questions.jsonl"
DEFAULT_PROJECT_ROOT = DEFAULT_DB.parent.parent
RELIABLE_BATCH = "2026-07-19-g11-beijing-20"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT,
    license TEXT NOT NULL,
    rights_confirmed INTEGER NOT NULL DEFAULT 0,
    retrieved_at TEXT,
    notes TEXT,
    UNIQUE(name, url)
);
CREATE TABLE IF NOT EXISTS knowledge_points (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    grade INTEGER NOT NULL CHECK (grade BETWEEN 10 AND 12),
    module TEXT NOT NULL,
    parent_code TEXT,
    description TEXT
);
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    stem TEXT NOT NULL,
    options_json TEXT,
    answer TEXT NOT NULL,
    solution TEXT,
    grade INTEGER NOT NULL CHECK (grade BETWEEN 10 AND 12),
    semester INTEGER CHECK (semester IN (1, 2) OR semester IS NULL),
    curriculum TEXT,
    question_type TEXT NOT NULL,
    difficulty REAL NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
    source_name TEXT NOT NULL,
    source_url TEXT,
    source_year TEXT,
    license TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    raw_json TEXT
);
CREATE TABLE IF NOT EXISTS question_knowledge (
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    knowledge_code TEXT NOT NULL REFERENCES knowledge_points(code),
    weight REAL NOT NULL DEFAULT 1,
    PRIMARY KEY(question_id, knowledge_code)
);
CREATE TABLE IF NOT EXISTS question_targets (
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    cause_code TEXT NOT NULL,
    PRIMARY KEY(question_id, cause_code)
);
CREATE TABLE IF NOT EXISTS question_features (
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    feature_code TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'inferred' CHECK(source IN ('inferred','imported','audited')),
    confidence REAL NOT NULL DEFAULT 1 CHECK(confidence BETWEEN 0 AND 1),
    PRIMARY KEY(question_id, feature_code)
);
CREATE TABLE IF NOT EXISTS verification_reviews (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    review_sha256 TEXT NOT NULL,
    review_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS errors (
    id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    problem_text TEXT NOT NULL,
    student_answer TEXT,
    correct_answer TEXT,
    correct_solution TEXT,
    first_wrong_step TEXT,
    cause_code TEXT NOT NULL,
    cause_detail TEXT NOT NULL,
    evidence_json TEXT,
    difficulty REAL NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    image_path TEXT,
    question_id TEXT REFERENCES questions(id),
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    raw_analysis_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS error_knowledge (
    error_id TEXT NOT NULL REFERENCES errors(id) ON DELETE CASCADE,
    knowledge_code TEXT NOT NULL REFERENCES knowledge_points(code),
    PRIMARY KEY(error_id, knowledge_code)
);
CREATE TABLE IF NOT EXISTS review_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    error_id TEXT NOT NULL REFERENCES errors(id) ON DELETE CASCADE,
    cycle INTEGER NOT NULL DEFAULT 1,
    stage INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    completed_at TEXT,
    result TEXT,
    note TEXT,
    UNIQUE(error_id, cycle, stage)
);
CREATE TABLE IF NOT EXISTS recommendations (
    id TEXT PRIMARY KEY,
    error_id TEXT NOT NULL REFERENCES errors(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL REFERENCES questions(id),
    rank INTEGER NOT NULL,
    score REAL NOT NULL,
    reason TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'assigned'
);
CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id),
    error_id TEXT REFERENCES errors(id),
    submitted_answer TEXT,
    is_correct INTEGER NOT NULL,
    cause_code TEXT,
    attempted_at TEXT NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_questions_grade_difficulty ON questions(grade, difficulty);
CREATE INDEX IF NOT EXISTS idx_qk_knowledge ON question_knowledge(knowledge_code);
CREATE INDEX IF NOT EXISTS idx_qf_feature ON question_features(feature_code);
CREATE INDEX IF NOT EXISTS idx_verification_question ON verification_reviews(question_id, created_at);
CREATE INDEX IF NOT EXISTS idx_errors_cause ON errors(cause_code, occurred_at);
CREATE INDEX IF NOT EXISTS idx_reviews_due ON review_schedule(due_date, completed_at);
"""


def init_database(conn: sqlite3.Connection, knowledge_file: Path = DEFAULT_KNOWLEDGE) -> dict[str, int]:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    points = json.loads(knowledge_file.read_text(encoding="utf-8"))
    for point in points:
        conn.execute(
            """INSERT INTO knowledge_points(code, name, grade, module, parent_code, description)
               VALUES(:code, :name, :grade, :module, :parent_code, :description)
               ON CONFLICT(code) DO UPDATE SET name=excluded.name, grade=excluded.grade,
               module=excluded.module, parent_code=excluded.parent_code,
               description=excluded.description""",
            {
                "parent_code": None,
                "description": "",
                **point,
            },
        )
    conn.commit()
    return {"knowledge_points": len(points)}


def fingerprint(text: str) -> str:
    normalized = re.sub(r"[\s，。！？；：、,.!?;:()（）\\$]", "", text).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def slug_id(prefix: str, seed: str | None = None) -> str:
    if seed:
        suffix = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    else:
        suffix = uuid.uuid4().hex[:12]
    return f"{prefix}-{suffix}"


KEYWORD_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("sets", ("集合", "set ", "intersection", "union", "∩", "∪")),
    ("logic", ("命题", "充分", "必要", "quantifier", "proposition")),
    ("inequalities", ("不等式", "inequality", "cauchy", "柯西")),
    ("function-properties", ("函数", "function", "定义域", "单调", "奇函数", "偶函数")),
    ("exponential-log", ("指数", "对数", "log", "exponential")),
    ("trig-definition", ("sin", "cos", "tan", "三角函数", "弧度")),
    ("trig-identities", ("正弦定理", "余弦定理", "trigonometric", "恒等变换")),
    ("plane-vectors", ("向量", "vector", "vec", "dot product", "平行")),
    ("complex-numbers", ("复数", "complex", "imaginary", "虚数")),
    ("solid-geometry", ("棱锥", "棱柱", "球", "二面角", "solid", "plane in space")),
    ("statistics", ("平均数", "方差", "标准差", "样本", "statistics", "variance")),
    ("probability", ("概率", "probability", "随机")),
    ("line-circle", ("直线", "圆", "circle", "line passes", "slope")),
    ("conic-ellipse", ("椭圆", "ellipse")),
    ("conic-hyperbola", ("双曲线", "hyperbola")),
    ("conic-parabola", ("抛物线", "parabola")),
    ("sequences", ("数列", "sequence", "an}", "a_n", "通项")),
    ("derivatives", ("导数", "derivative", "f'(x)", "切线")),
    ("counting-binomial", ("排列", "组合", "二项式", "binomial", "combination")),
    ("conditional-probability", ("条件概率", "conditional probability", "bayes")),
    ("random-variables", ("随机变量", "分布列", "期望", "distribution", "expectation")),
]


def infer_knowledge(stem: str) -> list[str]:
    lower = stem.lower()
    codes = [code for code, terms in KEYWORD_RULES if any(term.lower() in lower for term in terms)]
    return codes[:3]


FEATURE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("proof", ("证明", "求证")),
    ("multi-part", ("（1）", "(1)", "①")),
    ("parameter-range", ("取值范围", "参数范围", "实数 m", "实数 a", "若存在")),
    ("existence", ("存在", "是否存在")),
    ("uniqueness", ("唯一", "有且只有")),
    ("maximum-minimum", ("最大值", "最小值", "最值")),
    ("tangent", ("相切", "切线", "切点")),
    ("symmetry", ("对称", "对称轴", "对称中心")),
    ("locus", ("轨迹", "动点")),
    ("intersection", ("交点", "相交", "公共点")),
    ("vector-method", ("向量", "内积", "数量积")),
    ("discriminant", ("判别式", "根的个数", "两个不同实根")),
    ("case-analysis", ("分类讨论", "分情况")),
    ("derivative-analysis", ("导数", "单调区间", "极值点")),
    ("function-analysis", ("单调性", "奇偶性", "函数图象", "函数性质")),
    ("recurrence", ("递推", "a_{n+1}", "aₙ₊₁")),
    ("summation", ("前 n 项和", "求和", "s_n", "S_n")),
    ("counting", ("排列", "组合", "计数", "不同方法")),
    ("diagram-dependent", ("如图", "图中", "下图", "示意图")),
]


def infer_question_features(stem: str, question_type: str | None = None) -> list[str]:
    """Infer a small, auditable structural signature without mathematical guessing."""
    text = f"{question_type or ''}\n{stem or ''}".casefold()
    features: set[str] = set()
    qtype = (question_type or "").casefold()
    if "多选" in qtype or "多项选择" in qtype:
        features.add("multiple-choice")
    elif "选择" in qtype:
        features.add("single-choice")
    elif "填空" in qtype:
        features.add("fill-blank")
    elif "证明" in qtype:
        features.update(("solution", "proof"))
    elif qtype:
        features.add("solution")
    for code, terms in FEATURE_RULES:
        if any(term.casefold() in text for term in terms):
            features.add(code)
    return sorted(features)


def validate_feature_codes(codes: Iterable[str]) -> list[str]:
    normalized = sorted({str(code).strip() for code in codes if str(code).strip()})
    unknown = [code for code in normalized if code not in FEATURE_CODES]
    if unknown:
        raise ValueError(f"unknown feature code(s): {', '.join(unknown)}")
    return normalized


def normalize_difficulty(record: dict[str, Any]) -> float:
    raw = record.get("difficulty")
    if raw is not None:
        try:
            value = float(raw)
            if 1 <= value <= 5:
                return round(value, 2)
        except (TypeError, ValueError):
            pass
    score = record.get("score")
    try:
        score_value = float(score)
        if score_value <= 4:
            return 2.5
        if score_value <= 6:
            return 3.0
        if score_value <= 10:
            return 4.0
        return 4.5
    except (TypeError, ValueError):
        return 3.0


def normalize_question(
    record: dict[str, Any],
    source_name: str,
    source_url: str | None,
    license_name: str,
    verified: bool,
) -> dict[str, Any]:
    stem = str(record.get("stem") or record.get("problem") or record.get("question") or "").strip()
    answer = str(record.get("answer") or record.get("label") or "").strip()
    if not stem or not answer:
        raise ValueError("question requires non-empty stem/problem/question and answer/label")
    options = record.get("options") or record.get("choices")
    knowledge = record.get("knowledge_codes") or record.get("knowledge") or infer_knowledge(stem)
    if isinstance(knowledge, str):
        knowledge = [part.strip() for part in re.split(r"[,;，；]", knowledge) if part.strip()]
    targets = record.get("target_causes") or record.get("cause_codes") or []
    if isinstance(targets, str):
        targets = [part.strip() for part in re.split(r"[,;，；]", targets) if part.strip()]
    grade = int(record.get("grade") or 12)
    grade = min(12, max(10, grade))
    qtype = str(record.get("question_type") or record.get("type") or "解答题")
    supplied_features = record.get("feature_codes") or record.get("features")
    features = supplied_features or infer_question_features(stem, qtype)
    if isinstance(features, str):
        features = [part.strip() for part in re.split(r"[,;，；]", features) if part.strip()]
    features = validate_feature_codes(features)
    raw_id = record.get("id")
    qid_seed = f"{source_name}|{raw_id or ''}|{stem}"
    question_id = str(raw_id) if source_name == "内置原创示例题" and raw_id else slug_id("Q", qid_seed)
    return {
        "id": question_id,
        "stem": stem,
        "options_json": json.dumps(options, ensure_ascii=False) if options else None,
        "answer": answer,
        "solution": str(record.get("solution") or record.get("analysis") or "").strip() or None,
        "grade": grade,
        "semester": record.get("semester"),
        "curriculum": str(record.get("curriculum") or "普通高中数学课程标准（2017年版2020修订）"),
        "question_type": qtype,
        "difficulty": normalize_difficulty(record),
        "source_name": source_name,
        "source_url": source_url or record.get("source_url"),
        "source_year": str(record.get("source_year") or record.get("year") or "") or None,
        "license": license_name,
        # Verification is a caller-controlled trust decision. Imported payloads
        # must not be able to self-promote by setting {"verified": true}.
        "verified": int(verified),
        "fingerprint": fingerprint(stem),
        "created_at": now_iso(),
        "raw_json": json.dumps(record, ensure_ascii=False),
        "knowledge_codes": list(knowledge or []),
        "target_causes": list(targets or []),
        "feature_codes": features,
        "feature_source": "imported" if supplied_features else "inferred",
    }


def insert_question(conn: sqlite3.Connection, question: dict[str, Any]) -> tuple[bool, list[str]]:
    columns = (
        "id", "stem", "options_json", "answer", "solution", "grade", "semester", "curriculum",
        "question_type", "difficulty", "source_name", "source_url", "source_year", "license",
        "verified", "fingerprint", "created_at", "raw_json",
    )
    values = tuple(question.get(column) for column in columns)
    existing = conn.execute("SELECT id FROM questions WHERE id=?", (question["id"],)).fetchone()
    if existing and question["source_name"] == "内置原创示例题":
        assignments = ",".join(f"{column}=?" for column in columns if column != "id")
        conn.execute(
            f"UPDATE questions SET {assignments} WHERE id=?",
            tuple(question.get(column) for column in columns if column != "id") + (question["id"],),
        )
        conn.execute("DELETE FROM question_knowledge WHERE question_id=?", (question["id"],))
        conn.execute("DELETE FROM question_targets WHERE question_id=?", (question["id"],))
        conn.execute("DELETE FROM question_features WHERE question_id=?", (question["id"],))
        added = False
    else:
        added = True
    try:
        if added:
            conn.execute(
                f"INSERT INTO questions({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                values,
            )
    except sqlite3.IntegrityError as exc:
        if "fingerprint" in str(exc) or "questions.id" in str(exc):
            return False, []
        raise
    valid_codes = {row[0] for row in conn.execute("SELECT code FROM knowledge_points")}
    attached = []
    for code in question.get("knowledge_codes", []):
        if code in valid_codes:
            conn.execute(
                "INSERT OR IGNORE INTO question_knowledge(question_id, knowledge_code) VALUES(?, ?)",
                (question["id"], code),
            )
            attached.append(code)
    for cause in question.get("target_causes", []):
        if cause in CAUSE_CODES:
            conn.execute(
                "INSERT OR IGNORE INTO question_targets(question_id, cause_code) VALUES(?, ?)",
                (question["id"], cause),
            )
    for code in question.get("feature_codes", []):
        conn.execute(
            """INSERT OR IGNORE INTO question_features(question_id,feature_code,source,confidence)
               VALUES(?,?,?,?)""",
            (
                question["id"], code, question.get("feature_source", "imported"),
                1.0 if question.get("feature_source") == "imported" else 0.8,
            ),
        )
    return added, attached


def import_records(
    conn: sqlite3.Connection,
    records: Iterable[dict[str, Any]],
    source_name: str,
    source_url: str | None,
    license_name: str,
    verified: bool,
) -> dict[str, int]:
    inserted = duplicate = untagged = invalid = 0
    for record in records:
        try:
            question = normalize_question(record, source_name, source_url, license_name, verified)
            added, codes = insert_question(conn, question)
            if added:
                inserted += 1
                if not codes:
                    untagged += 1
            else:
                duplicate += 1
        except (ValueError, TypeError, json.JSONDecodeError):
            invalid += 1
    conn.commit()
    return {"inserted": inserted, "duplicates": duplicate, "untagged": untagged, "invalid": invalid}


def read_json_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return [item.get("row", item) for item in payload["rows"]]
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    raise ValueError("JSON must be an array, {data:[...]}, or Hugging Face {rows:[{row:...}]} payload")


def fetch_json(url: str, raw_dir: Path) -> tuple[list[dict[str, Any]], Path]:
    request = urllib.request.Request(url, headers={"User-Agent": "math-error-notebook/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content_length = int(response.headers.get("Content-Length", "0") or 0)
        if content_length > 50 * 1024 * 1024:
            raise ValueError("remote payload exceeds 50 MB limit")
        payload = response.read(50 * 1024 * 1024 + 1)
    if len(payload) > 50 * 1024 * 1024:
        raise ValueError("remote payload exceeds 50 MB limit")
    raw_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jsonl" if ".jsonl" in url.lower() else ".json"
    raw_path = raw_dir / f"import-{datetime.now().strftime('%Y%m%d-%H%M%S')}{suffix}"
    raw_path.write_bytes(payload)
    return read_json_records(raw_path), raw_path


def register_exam_directory(
    conn: sqlite3.Connection,
    directory: Path,
    license_name: str,
    manifest_out: Path | None,
) -> dict[str, Any]:
    """Catalog local PDF sources without claiming that their questions were verified."""
    if not directory.is_dir():
        raise ValueError(f"not a directory: {directory}")
    records: list[dict[str, Any]] = []
    inserted = updated = 0
    for path in sorted(directory.glob("*.pdf"), key=lambda item: item.name.casefold()):
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        lower_name = path.name.casefold()
        if "yangziyu2006" in lower_name:
            document_type = "study_reference"
            extraction_status = "excluded_non_exam_reference"
        elif lower_name.endswith("20240608-1.pdf") or lower_name.endswith("20240608-2.pdf"):
            document_type = "fragment_or_cover"
            extraction_status = "excluded_fragment"
        else:
            document_type = "exam_paper"
            extraction_status = "cataloged_pending_question_extraction"
        page_count = len(re.findall(rb"/Type\s*/Page\b", payload))
        try:
            pdf_info = subprocess.run(
                ["pdfinfo", str(path.resolve())],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            ).stdout
            match = re.search(r"^Pages:\s*(\d+)\s*$", pdf_info, re.MULTILINE)
            if match:
                page_count = int(match.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
        record = {
            "filename": path.name,
            "local_path": str(path.resolve()),
            "sha256": digest,
            "bytes": len(payload),
            "pages": page_count,
            "document_type": document_type,
            "extraction_status": extraction_status,
            "verified": False,
            "rights_basis": "user-provided local files; user requested import",
        }
        source_name = f"本地试卷：{path.stem}"
        source_url = str(path.resolve())
        notes = json.dumps(record, ensure_ascii=False, sort_keys=True)
        existing = conn.execute(
            "SELECT id FROM sources WHERE name=? AND url=?", (source_name, source_url)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE sources SET license=?,rights_confirmed=1,retrieved_at=?,notes=?
                   WHERE id=?""",
                (license_name, now_iso(), notes, existing["id"]),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO sources(name,url,license,rights_confirmed,retrieved_at,notes)
                   VALUES(?,?,?,?,?,?)""",
                (source_name, source_url, license_name, 1, now_iso(), notes),
            )
            inserted += 1
        records.append(record)
    if manifest_out:
        manifest_out.parent.mkdir(parents=True, exist_ok=True)
        manifest_out.write_text(
            json.dumps(
                {
                    "created_at": now_iso(),
                    "source_directory": str(directory.resolve()),
                    "license": license_name,
                    "rights_confirmed": True,
                    "documents": records,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    conn.commit()
    return {
        "pdf_count": len(records),
        "sources_inserted": inserted,
        "sources_updated": updated,
        "manifest_path": str(manifest_out) if manifest_out else None,
        "pending_exam_papers": sum(
            item["document_type"] == "exam_paper" for item in records
        ),
        "excluded_non_exam_or_fragments": sum(
            item["document_type"] != "exam_paper" for item in records
        ),
    }


def sync_source_manifest(conn: sqlite3.Connection, manifest_path: Path) -> dict[str, int]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents = payload.get("documents", []) if isinstance(payload, dict) else []
    if not isinstance(documents, list):
        raise ValueError("manifest documents must be a list")
    updated = missing = 0
    for record in documents:
        if not isinstance(record, dict) or not record.get("local_path"):
            continue
        cursor = conn.execute(
            "UPDATE sources SET notes=? WHERE url=?",
            (json.dumps(record, ensure_ascii=False, sort_keys=True), record["local_path"]),
        )
        if cursor.rowcount:
            updated += cursor.rowcount
        else:
            missing += 1
    conn.commit()
    return {"sources_updated": updated, "sources_missing": missing}


def update_source_metadata(
    conn: sqlite3.Connection,
    source_name: str,
    source_url: str,
    license_name: str,
    rights_confirmed: bool,
    notes: str | None,
) -> dict[str, Any]:
    """Correct provenance for one explicitly named source and its questions."""
    if not rights_confirmed:
        raise ValueError("source metadata update requires --rights-confirmed")
    question_count = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE source_name=?", (source_name,)
    ).fetchone()[0]
    if not question_count:
        raise ValueError(f"source has no questions: {source_name}")
    conn.execute(
        "UPDATE questions SET source_url=?,license=? WHERE source_name=?",
        (source_url, license_name, source_name),
    )
    existing = conn.execute(
        "SELECT id FROM sources WHERE name=? ORDER BY id LIMIT 1", (source_name,)
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE sources SET url=?,license=?,rights_confirmed=1,
                      retrieved_at=?,notes=? WHERE id=?""",
            (source_url, license_name, now_iso(), notes, existing["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO sources(name,url,license,rights_confirmed,retrieved_at,notes)
               VALUES(?,?,?,?,?,?)""",
            (source_name, source_url, license_name, 1, now_iso(), notes),
        )
    conn.commit()
    return {
        "source_name": source_name,
        "source_url": source_url,
        "license": license_name,
        "rights_confirmed": True,
        "questions_updated": question_count,
    }


def create_review_cycle(conn: sqlite3.Connection, error_id: str, base: date, cycle: int) -> None:
    for stage, days in enumerate(REVIEW_INTERVALS, start=1):
        conn.execute(
            "INSERT INTO review_schedule(error_id, cycle, stage, due_date) VALUES(?, ?, ?, ?)",
            (error_id, cycle, stage, (base + timedelta(days=days)).isoformat()),
        )


def render_error_markdown(error_id: str, analysis: dict[str, Any], schedule: list[sqlite3.Row]) -> str:
    codes = analysis.get("knowledge_codes") or []
    evidence = analysis.get("evidence") or []
    lines = [
        f"# 错题记录 {error_id}",
        "",
        f"- 记录时间：{analysis.get('occurred_at') or date.today().isoformat()}",
        f"- 知识点：{', '.join(codes) if codes else '待标注'}",
        f"- 难度：{analysis.get('difficulty', 3)}/5",
        f"- 错误原因：{CAUSE_CODES.get(analysis.get('cause_code'), analysis.get('cause_code'))}",
        f"- 分析置信度：{analysis.get('confidence', 0):.0%}",
        "",
        "## 题目",
        "",
        str(analysis.get("problem_text", "")),
        "",
        "## 学生作答",
        "",
        str(analysis.get("student_answer") or "未提供"),
        "",
        "## 第一处实质性错误",
        "",
        str(analysis.get("first_wrong_step") or "未能从现有信息中定位"),
        "",
        "## 错因分析",
        "",
        str(analysis.get("cause_detail", "")),
    ]
    if evidence:
        lines.extend(["", "### 判断依据", ""] + [f"- {item}" for item in evidence])
    lines.extend([
        "",
        "## 正确答案",
        "",
        str(analysis.get("correct_answer") or ""),
        "",
        "## 正确解法",
        "",
        str(analysis.get("correct_solution") or ""),
        "",
        "## 复习计划",
        "",
        "| 阶段 | 计划日期 | 状态 |",
        "|---|---|---|",
    ])
    lines.extend(f"| {row['stage']} | {row['due_date']} | 待复习 |" for row in schedule)
    if analysis.get("image_path"):
        lines.extend(["", f"原图：`{analysis['image_path']}`"])
    return "\n".join(lines) + "\n"


def validate_error_analysis(
    conn: sqlite3.Connection, analysis: dict[str, Any]
) -> dict[str, Any]:
    """Run deterministic validation before an error analysis is persisted."""
    if not isinstance(analysis, dict):
        raise ValueError("analysis must be a JSON object")
    required = ("problem_text", "cause_code", "cause_detail", "difficulty", "confidence")
    missing = [key for key in required if analysis.get(key) in (None, "")]
    if missing:
        raise ValueError(f"analysis missing required fields: {', '.join(missing)}")
    cause_code = str(analysis["cause_code"])
    if cause_code not in CAUSE_CODES:
        raise ValueError(f"unsupported cause_code: {cause_code}")
    difficulty = float(analysis["difficulty"])
    confidence = float(analysis["confidence"])
    if not 1 <= difficulty <= 5 or not 0 <= confidence <= 1:
        raise ValueError("difficulty must be 1..5 and confidence must be 0..1")
    evidence = analysis.get("evidence") or []
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    if cause_code == "careless" and not any(str(item).strip() for item in evidence):
        raise ValueError("careless requires direct evidence from the student's work")
    codes = analysis.get("knowledge_codes") or []
    if not isinstance(codes, list):
        raise ValueError("knowledge_codes must be a list")
    valid_codes = {row[0] for row in conn.execute("SELECT code FROM knowledge_points")}
    unknown = [str(code) for code in codes if code not in valid_codes]
    if unknown:
        raise ValueError("unknown knowledge code: " + ", ".join(unknown))
    features = validate_feature_codes(analysis.get("feature_codes") or [])
    warnings: list[str] = []
    if cause_code != "unclear" and not str(analysis.get("first_wrong_step") or "").strip():
        warnings.append("first_wrong_step_missing")
    if not str(analysis.get("correct_solution") or "").strip():
        warnings.append("correct_solution_missing")
    if cause_code == "unclear" and confidence > 0.7:
        warnings.append("unclear_with_high_confidence")
    return {
        "valid": True,
        "cause_code": cause_code,
        "difficulty": difficulty,
        "confidence": confidence,
        "knowledge_codes": codes,
        "feature_codes": features,
        "warnings": warnings,
    }


def record_error(conn: sqlite3.Connection, analysis: dict[str, Any], project_root: Path, copy_image: bool) -> str:
    validation = validate_error_analysis(conn, analysis)
    difficulty = validation["difficulty"]
    confidence = validation["confidence"]
    occurred = analysis.get("occurred_at") or now_iso()
    error_id = analysis.get("id") or f"ERR-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    image_path = analysis.get("image_path")
    if copy_image and image_path and Path(image_path).is_file():
        destination = project_root / "data" / "images" / f"{error_id}{Path(image_path).suffix.lower()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image_path, destination)
        image_path = str(destination.resolve())
        analysis["image_path"] = image_path
    conn.execute(
        """INSERT INTO errors(
           id, occurred_at, problem_text, student_answer, correct_answer, correct_solution,
           first_wrong_step, cause_code, cause_detail, evidence_json, difficulty, confidence,
           image_path, question_id, created_at, raw_analysis_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            error_id, occurred, analysis["problem_text"], analysis.get("student_answer"),
            analysis.get("correct_answer"), analysis.get("correct_solution"),
            analysis.get("first_wrong_step"), analysis["cause_code"], analysis["cause_detail"],
            json.dumps(analysis.get("evidence") or [], ensure_ascii=False), difficulty, confidence,
            image_path, analysis.get("question_id"), now_iso(), json.dumps(analysis, ensure_ascii=False),
        ),
    )
    for code in validation["knowledge_codes"]:
        conn.execute("INSERT INTO error_knowledge(error_id, knowledge_code) VALUES(?, ?)", (error_id, code))
    base = datetime.fromisoformat(occurred).date() if "T" in occurred else date.fromisoformat(occurred)
    create_review_cycle(conn, error_id, base, 1)
    conn.commit()
    schedule = list(conn.execute(
        "SELECT stage, due_date FROM review_schedule WHERE error_id=? ORDER BY cycle, stage", (error_id,)
    ))
    month_dir = project_root / "errors" / base.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    report = month_dir / f"{error_id}.md"
    report.write_text(render_error_markdown(error_id, analysis, schedule), encoding="utf-8")
    return error_id


def fetch_error(conn: sqlite3.Connection, error_id: str) -> tuple[sqlite3.Row, list[str]]:
    row = conn.execute("SELECT * FROM errors WHERE id=?", (error_id,)).fetchone()
    if not row:
        raise ValueError(f"error not found: {error_id}")
    codes = [item[0] for item in conn.execute(
        "SELECT knowledge_code FROM error_knowledge WHERE error_id=?", (error_id,)
    )]
    return row, codes


def delete_error(conn: sqlite3.Connection, error_id: str, project_root: Path) -> dict[str, Any]:
    """Remove a mistakenly recorded error and its generated local artifacts."""
    row = conn.execute("SELECT image_path FROM errors WHERE id=?", (error_id,)).fetchone()
    if not row:
        raise ValueError(f"error not found: {error_id}")
    attempt_count = conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE error_id=?", (error_id,)
    ).fetchone()[0]
    if attempt_count:
        raise ValueError("cannot delete an error that has recorded practice attempts")

    conn.execute("DELETE FROM errors WHERE id=?", (error_id,))
    conn.commit()

    removed_files: list[str] = []
    root = project_root.resolve()
    report_root = (root / "errors").resolve()
    if report_root.is_dir():
        for report in report_root.glob(f"*/{error_id}.md"):
            resolved = report.resolve()
            if report_root in resolved.parents and resolved.is_file():
                resolved.unlink()
                removed_files.append(str(resolved))

    image_path = row["image_path"]
    image_root = (root / "data" / "images").resolve()
    if image_path:
        candidate = Path(image_path).resolve()
        if (
            image_root in candidate.parents
            and candidate.name.startswith(error_id)
            and candidate.is_file()
        ):
            candidate.unlink()
            removed_files.append(str(candidate))

    return {"error_id": error_id, "deleted": True, "removed_files": removed_files}


def compact_recommendations(items: list[dict[str, Any]], full: bool) -> list[dict[str, Any]]:
    if full:
        return items
    fields = (
        "rank", "question_id", "stem", "options", "difficulty",
        "source_name", "score", "reason",
    )
    return [{key: item[key] for key in fields} for item in items]


def question_feature_codes(
    conn: sqlite3.Connection,
    question_id: str,
    stem: str | None = None,
    question_type: str | None = None,
) -> list[str]:
    stored = {
        row[0] for row in conn.execute(
            "SELECT feature_code FROM question_features WHERE question_id=?",
            (question_id,),
        )
    }
    if stem is not None:
        stored.update(infer_question_features(stem, question_type))
    return sorted(stored)


def backfill_question_features(
    conn: sqlite3.Connection, verified_only: bool = False
) -> dict[str, Any]:
    clauses = "WHERE verified=1" if verified_only else ""
    rows = conn.execute(
        f"SELECT id,stem,question_type FROM questions {clauses} ORDER BY id"
    ).fetchall()
    inserted = 0
    removed = 0
    affected: list[str] = []
    for row in rows:
        deleted = conn.execute(
            "DELETE FROM question_features WHERE question_id=? AND source='inferred'",
            (row["id"],),
        ).rowcount
        removed += max(0, deleted)
        row_inserted = 0
        for code in infer_question_features(row["stem"], row["question_type"]):
            cursor = conn.execute(
                """INSERT OR IGNORE INTO question_features(question_id,feature_code,source,confidence)
                   VALUES(?,?,?,?)""",
                (row["id"], code, "inferred", 0.8),
            )
            row_inserted += max(0, cursor.rowcount)
        inserted += row_inserted
        if deleted or row_inserted:
            affected.append(row["id"])
    conn.commit()
    return {
        "questions_scanned": len(rows),
        "questions_updated": len(affected),
        "features_inserted": inserted,
        "stale_inferred_features_removed": removed,
    }


def error_feature_codes(error: sqlite3.Row, explicit: list[str] | None = None) -> list[str]:
    features = set(validate_feature_codes(explicit or []))
    try:
        analysis = json.loads(error["raw_analysis_json"] or "{}")
    except json.JSONDecodeError:
        analysis = {}
    stored = analysis.get("feature_codes") or []
    if isinstance(stored, str):
        stored = [part.strip() for part in re.split(r"[,;，；]", stored) if part.strip()]
    features.update(validate_feature_codes(stored))
    features.update(infer_question_features(error["problem_text"], analysis.get("question_type")))
    return sorted(features)


def recommend(
    conn: sqlite3.Connection,
    error_id: str,
    limit: int,
    save: bool,
    project_root: Path,
    keywords: list[str] | None = None,
    replace: bool = False,
    full: bool = False,
    features: list[str] | None = None,
) -> list[dict[str, Any]]:
    error, codes = fetch_error(conn, error_id)
    if not codes:
        raise ValueError("error has no knowledge codes; tag it before requesting recommendations")
    keywords = [item.strip().casefold() for item in (keywords or []) if item.strip()]
    target_features = error_feature_codes(error, features)
    if save and replace:
        conn.execute("DELETE FROM recommendations WHERE error_id=?", (error_id,))
    placeholders = ",".join("?" for _ in codes)
    rows = list(conn.execute(
        f"""SELECT q.*,
               COUNT(DISTINCT qk.knowledge_code) AS overlap,
               MAX(CASE WHEN qt.cause_code=? THEN 1 ELSE 0 END) AS cause_match,
               COALESCE((SELECT AVG(a.is_correct) FROM attempts a WHERE a.question_id=q.id), -1) AS accuracy
            FROM questions q
            JOIN question_knowledge qk ON qk.question_id=q.id
            LEFT JOIN question_targets qt ON qt.question_id=q.id
            WHERE qk.knowledge_code IN ({placeholders})
              AND q.verified=1
              AND (q.id <> COALESCE(?, ''))
              AND q.id NOT IN (SELECT question_id FROM recommendations WHERE error_id=?)
            GROUP BY q.id""",
        (error["cause_code"], *codes, error["question_id"], error_id),
    ))
    if not rows:
        return []
    desired_offsets = (-0.6, -0.2, 0.0, 0.35, 0.8)
    selected: list[dict[str, Any]] = []
    remaining = rows[:]
    for rank in range(1, min(limit, len(rows)) + 1):
        target = max(1.0, min(5.0, float(error["difficulty"]) + desired_offsets[(rank - 1) % len(desired_offsets)]))
        def score(row: sqlite3.Row) -> float:
            difficulty_score = max(0.0, 3.0 - abs(float(row["difficulty"]) - target))
            repeat_penalty = 0.5 if row["accuracy"] == 1 else 0.0
            stem = (row["stem"] or "").casefold()
            keyword_score = 2.0 * sum(keyword in stem for keyword in keywords)
            candidate_features = set(question_feature_codes(
                conn, row["id"], row["stem"], row["question_type"]
            ))
            feature_score = 3.0 * len(candidate_features.intersection(target_features))
            return (
                5.0 * row["overlap"] + 2.5 * row["cause_match"]
                + difficulty_score + row["verified"] + keyword_score
                + feature_score - repeat_penalty
            )
        best = max(remaining, key=score)
        remaining.remove(best)
        matched_codes = [item[0] for item in conn.execute(
            "SELECT knowledge_code FROM question_knowledge WHERE question_id=? AND knowledge_code IN ("
            + placeholders + ")", (best["id"], *codes)
        )]
        reason_parts = [f"匹配知识点：{', '.join(matched_codes)}", f"目标难度 {target:.1f}，题目难度 {best['difficulty']:.1f}"]
        if best["cause_match"]:
            reason_parts.append(f"针对错因：{CAUSE_CODES[error['cause_code']]}")
        matched_keywords = [keyword for keyword in keywords if keyword in (best["stem"] or "").casefold()]
        if matched_keywords:
            reason_parts.append(f"题型关键词：{', '.join(matched_keywords)}")
        matched_features = sorted(set(question_feature_codes(
            conn, best["id"], best["stem"], best["question_type"]
        )).intersection(target_features))
        if matched_features:
            labels = [FEATURE_CODES[code] for code in matched_features]
            reason_parts.append(f"结构特征：{', '.join(labels)}")
        reason = "；".join(reason_parts)
        item = {
            "rank": rank,
            "question_id": best["id"],
            "stem": best["stem"],
            "options": json.loads(best["options_json"]) if best["options_json"] else None,
            "difficulty": best["difficulty"],
            "answer": best["answer"],
            "solution": best["solution"],
            "source_name": best["source_name"],
            "source_url": best["source_url"],
            "score": round(score(best), 3),
            "reason": reason,
        }
        selected.append(item)
        if save:
            conn.execute(
                """INSERT INTO recommendations(id,error_id,question_id,rank,score,reason,assigned_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (slug_id("REC"), error_id, best["id"], rank, item["score"], reason, now_iso()),
            )
    if save:
        conn.commit()
    if save and selected:
        out_dir = project_root / "practice"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{error_id}-{date.today().isoformat()}.md"
        lines = [f"# 针对性练习：{error_id}", ""]
        for item in selected:
            lines.extend([
                f"## {item['rank']}. {item['question_id']}（难度 {item['difficulty']}/5）",
                "", item["stem"], "", f"> 推荐理由：{item['reason']}", "",
                "<details><summary>查看答案与解析</summary>", "",
                f"**答案：** {item['answer']}", "", item["solution"] or "暂无解析，建议先独立作答后请求讲解。", "",
                "</details>", "",
            ])
        out_path.write_text("\n".join(lines), encoding="utf-8")
    return compact_recommendations(selected, full)


def assign_recommendations(
    conn: sqlite3.Connection,
    error_id: str,
    items: list[dict[str, Any]],
    project_root: Path,
    save: bool,
    full: bool = False,
) -> list[dict[str, Any]]:
    """Replace automatic recommendations with a manually reviewed verified set."""
    fetch_error(conn, error_id)
    if not items:
        raise ValueError("recommendation plan must contain at least one item")
    question_ids = [item.get("question_id") for item in items]
    if any(not question_id for question_id in question_ids):
        raise ValueError("each recommendation requires question_id")
    if len(set(question_ids)) != len(question_ids):
        raise ValueError("duplicate question_id in recommendation plan")

    selected: list[dict[str, Any]] = []
    for rank, item in enumerate(items, 1):
        row = conn.execute("SELECT * FROM questions WHERE id=?", (item["question_id"],)).fetchone()
        if not row:
            raise ValueError(f"question not found: {item['question_id']}")
        if not row["verified"]:
            raise ValueError(f"question is not verified: {item['question_id']}")
        selected.append({
            "rank": rank,
            "question_id": row["id"],
            "stem": row["stem"],
            "options": json.loads(row["options_json"]) if row["options_json"] else None,
            "difficulty": row["difficulty"],
            "answer": row["answer"],
            "solution": row["solution"],
            "source_name": row["source_name"],
            "source_url": row["source_url"],
            "score": float(item.get("score", 10.0 - 0.1 * (rank - 1))),
            "reason": item.get("reason") or "人工复核后匹配当前错因与题型",
        })

    conn.execute("DELETE FROM recommendations WHERE error_id=?", (error_id,))
    for item in selected:
        conn.execute(
            """INSERT INTO recommendations(id,error_id,question_id,rank,score,reason,assigned_at)
               VALUES(?,?,?,?,?,?,?)""",
            (
                slug_id("REC"), error_id, item["question_id"], item["rank"],
                item["score"], item["reason"], now_iso(),
            ),
        )
    conn.commit()

    if save:
        out_dir = project_root / "practice"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{error_id}-{date.today().isoformat()}.md"
        lines = [f"# 针对性练习：{error_id}", ""]
        for item in selected:
            lines.extend([
                f"## {item['rank']}. {item['question_id']}（难度 {item['difficulty']}/5）",
                "", item["stem"], "", f"> 推荐理由：{item['reason']}",
                "", f"> 来源：{item['source_name']}", "",
                "<details><summary>查看答案与解析</summary>", "",
                f"**答案：** {item['answer']}", "", item["solution"] or "暂无解析。", "",
                "</details>", "",
            ])
        out_path.write_text("\n".join(lines), encoding="utf-8")
    return compact_recommendations(selected, full)


def review_due(conn: sqlite3.Connection, target: date) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        """SELECT rs.id AS review_id, rs.error_id, rs.cycle, rs.stage, rs.due_date,
                  e.problem_text, e.cause_code, e.difficulty
           FROM review_schedule rs JOIN errors e ON e.id=rs.error_id
           WHERE rs.completed_at IS NULL AND rs.due_date<=? AND e.status='active'
           ORDER BY rs.due_date, e.id""",
        (target.isoformat(),),
    )]


def mark_review(conn: sqlite3.Connection, error_id: str, result: str, note: str | None, on_date: date) -> dict[str, Any]:
    current = conn.execute(
        """SELECT * FROM review_schedule WHERE error_id=? AND completed_at IS NULL
           ORDER BY cycle, stage LIMIT 1""",
        (error_id,),
    ).fetchone()
    if not current:
        raise ValueError("no pending review stage")
    conn.execute(
        "UPDATE review_schedule SET completed_at=?, result=?, note=? WHERE id=?",
        (now_iso(), result, note, current["id"]),
    )
    if result in {"wrong", "partial"}:
        conn.execute(
            "DELETE FROM review_schedule WHERE error_id=? AND completed_at IS NULL",
            (error_id,),
        )
        cycle = int(current["cycle"]) + 1
        intervals = REVIEW_INTERVALS if result == "wrong" else (1, 3, 7, 15, 30, 45)
        for stage, days in enumerate(intervals, start=1):
            conn.execute(
                "INSERT INTO review_schedule(error_id, cycle, stage, due_date) VALUES(?,?,?,?)",
                (error_id, cycle, stage, (on_date + timedelta(days=days)).isoformat()),
            )
    pending = conn.execute(
        "SELECT due_date FROM review_schedule WHERE error_id=? AND completed_at IS NULL ORDER BY cycle,stage LIMIT 1",
        (error_id,),
    ).fetchone()
    if not pending:
        conn.execute("UPDATE errors SET status='mastered' WHERE id=?", (error_id,))
    conn.commit()
    return {"error_id": error_id, "result": result, "next_due": pending[0] if pending else None}


def record_attempt(conn: sqlite3.Connection, args: argparse.Namespace) -> str:
    if args.cause_code and args.cause_code not in CAUSE_CODES:
        raise ValueError(f"unsupported cause_code: {args.cause_code}")
    attempt_id = slug_id("ATT")
    conn.execute(
        """INSERT INTO attempts(id,question_id,error_id,submitted_answer,is_correct,cause_code,attempted_at,note)
           VALUES(?,?,?,?,?,?,?,?)""",
        (attempt_id, args.question_id, args.error_id, args.answer, int(args.correct), args.cause_code, now_iso(), args.note),
    )
    conn.execute(
        "UPDATE recommendations SET status=? WHERE error_id=? AND question_id=?",
        ("correct" if args.correct else "wrong", args.error_id, args.question_id),
    )
    conn.commit()
    return attempt_id


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    result: dict[str, Any] = {}
    result["questions"] = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    result["verified_questions"] = conn.execute("SELECT COUNT(*) FROM questions WHERE verified=1").fetchone()[0]
    result["errors"] = conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
    result["active_errors"] = conn.execute("SELECT COUNT(*) FROM errors WHERE status='active'").fetchone()[0]
    result["due_reviews"] = len(review_due(conn, date.today()))
    result["attempt_accuracy"] = conn.execute("SELECT ROUND(AVG(is_correct),3) FROM attempts").fetchone()[0]
    result["top_causes"] = [dict(row) for row in conn.execute(
        "SELECT cause_code, COUNT(*) AS count FROM errors GROUP BY cause_code ORDER BY count DESC LIMIT 5"
    )]
    result["weak_points"] = [dict(row) for row in conn.execute(
        """SELECT ek.knowledge_code, kp.name, COUNT(*) AS error_count
           FROM error_knowledge ek JOIN knowledge_points kp ON kp.code=ek.knowledge_code
           GROUP BY ek.knowledge_code ORDER BY error_count DESC LIMIT 10"""
    )]
    return result


def list_sources(conn: sqlite3.Connection, text: str | None = None) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if text:
        clauses.append("s.name LIKE ?")
        params.append(f"%{text}%")
    return [dict(row) for row in conn.execute(
        f"""SELECT s.name,s.url,s.license,s.rights_confirmed,
                   COUNT(q.id) AS questions,
                   SUM(CASE WHEN q.verified=1 THEN 1 ELSE 0 END) AS verified
            FROM sources s LEFT JOIN questions q ON q.source_name=s.name
            WHERE {' AND '.join(clauses)}
            GROUP BY s.id ORDER BY s.name""",
        params,
    )]


def bank_info(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    """Return the identity and health of the selected question bank."""
    resolved = db_path.resolve()
    # Hash the logical SQLite dump rather than the main file bytes. WAL mode can
    # change file-header/checkpoint bytes even when the logical data is unchanged.
    logical_dump = "\n".join(conn.iterdump())
    logical_hash = hashlib.sha256(logical_dump.encode("utf-8")).hexdigest()
    return {
        "canonical_path": str(resolved),
        "sha256": logical_hash,
        "integrity_check": conn.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_violations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "schema_version": conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0],
        "questions": conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
        "verified_questions": conn.execute(
            "SELECT COUNT(*) FROM questions WHERE verified=1"
        ).fetchone()[0],
        "sources": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        "errors": conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0],
    }


def coverage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        """SELECT kp.grade, kp.module, kp.code, kp.name,
                  COUNT(qk.question_id) AS question_count,
                  SUM(CASE WHEN q.verified=1 THEN 1 ELSE 0 END) AS verified_count
           FROM knowledge_points kp
           LEFT JOIN question_knowledge qk ON qk.knowledge_code=kp.code
           LEFT JOIN questions q ON q.id=qk.question_id
           GROUP BY kp.code ORDER BY kp.grade, kp.module, kp.code"""
    )]


def list_knowledge_points(
    conn: sqlite3.Connection, text: str | None = None, grade: int | None = None
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if text:
        clauses.append("(code LIKE ? OR name LIKE ? OR module LIKE ? OR description LIKE ?)")
        needle = f"%{text}%"
        params.extend((needle, needle, needle, needle))
    if grade:
        clauses.append("grade=?")
        params.append(grade)
    return [dict(row) for row in conn.execute(
        f"""SELECT code,name,grade,module FROM knowledge_points
             WHERE {' AND '.join(clauses)} ORDER BY grade,module,code""",
        params,
    )]


def list_cause_codes(text: str | None = None) -> list[dict[str, str]]:
    needle = (text or "").casefold()
    return [
        {"code": code, "name": name}
        for code, name in CAUSE_CODES.items()
        if not needle or needle in code.casefold() or needle in name.casefold()
    ]


def list_feature_codes(text: str | None = None) -> list[dict[str, str]]:
    needle = (text or "").casefold()
    return [
        {"code": code, "name": name}
        for code, name in FEATURE_CODES.items()
        if not needle or needle in code.casefold() or needle in name.casefold()
    ]


def question_detail(conn: sqlite3.Connection, question_id: str, raw: bool = False) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
    if not row:
        raise ValueError(f"question not found: {question_id}")
    result = dict(row)
    options_json = result.pop("options_json", None)
    result["options"] = json.loads(options_json) if options_json else None
    result["knowledge_codes"] = [item[0] for item in conn.execute(
        "SELECT knowledge_code FROM question_knowledge WHERE question_id=? ORDER BY knowledge_code",
        (question_id,),
    )]
    result["target_causes"] = [item[0] for item in conn.execute(
        "SELECT cause_code FROM question_targets WHERE question_id=? ORDER BY cause_code",
        (question_id,),
    )]
    result["feature_codes"] = question_feature_codes(
        conn, question_id, result["stem"], result["question_type"]
    )
    if not raw:
        result.pop("raw_json", None)
    return result


def search_questions(conn: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    join = ""
    if args.knowledge:
        join = "JOIN question_knowledge qk ON qk.question_id=q.id"
        clauses.append("qk.knowledge_code=?")
        params.append(args.knowledge)
    if args.grade:
        clauses.append("q.grade=?")
        params.append(args.grade)
    if args.difficulty_min is not None:
        clauses.append("q.difficulty>=?")
        params.append(args.difficulty_min)
    if args.difficulty_max is not None:
        clauses.append("q.difficulty<=?")
        params.append(args.difficulty_max)
    if args.text:
        clauses.append("q.stem LIKE ?")
        params.append(f"%{args.text}%")
    if args.verified:
        clauses.append("q.verified=1")
    params.append(args.limit)
    fields = "q.*" if args.full else (
        "q.id,q.stem,q.options_json,q.grade,q.question_type,q.difficulty,"
        "q.source_name,q.source_year,q.verified"
    )
    rows = conn.execute(
        f"SELECT DISTINCT {fields} FROM questions q {join} WHERE {' AND '.join(clauses)} ORDER BY q.difficulty,q.id LIMIT ?",
        params,
    )
    result = []
    for row in rows:
        item = dict(row)
        options_json = item.pop("options_json", None)
        item["options"] = json.loads(options_json) if options_json else None
        result.append(item)
    return result


def annotate_question(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    correction_override: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    question = conn.execute("SELECT * FROM questions WHERE id=?", (args.question_id,)).fetchone()
    if not question:
        raise ValueError(f"question not found: {args.question_id}")
    valid_codes = {row[0] for row in conn.execute("SELECT code FROM knowledge_points")}
    for code in args.knowledge or []:
        if code not in valid_codes:
            raise ValueError(f"unknown knowledge code: {code}")
    for cause in args.target_cause or []:
        if cause not in CAUSE_CODES:
            raise ValueError(f"unsupported cause_code: {cause}")
    feature_codes = validate_feature_codes(getattr(args, "feature", None) or [])
    updates: dict[str, Any] = {}
    correction_file = getattr(args, "correction_file", None)
    correction: dict[str, Any] = correction_override or {}
    if correction_file:
        payload = json.loads(correction_file.read_text(encoding="utf-8"))
        records = payload.get("questions", []) if isinstance(payload, dict) else payload
        correction = next(
            (record for record in records if str(record.get("id")) == args.question_id),
            {},
        )
        if not correction:
            raise ValueError(f"question not present in correction file: {args.question_id}")
    stem_file = getattr(args, "stem_file", None)
    answer_file = getattr(args, "answer_file", None)
    audited_stem = (
        stem_file.read_text(encoding="utf-8").strip()
        if stem_file else str(correction.get("stem") or "").strip()
    )
    if audited_stem:
        audited_fingerprint = fingerprint(audited_stem)
        duplicate = conn.execute(
            "SELECT id FROM questions WHERE fingerprint=? AND id<>?",
            (audited_fingerprint, args.question_id),
        ).fetchone()
        if duplicate:
            raise ValueError(f"corrected stem duplicates question: {duplicate['id']}")
        updates["stem"] = audited_stem
        updates["fingerprint"] = audited_fingerprint
    audited_answer = (
        answer_file.read_text(encoding="utf-8").strip()
        if answer_file else str(correction.get("answer") or "").strip()
    )
    if audited_answer:
        updates["answer"] = audited_answer
    if "options" in correction:
        audited_options = correction["options"]
        if not isinstance(audited_options, list) or not all(
            isinstance(option, str) and option.strip() for option in audited_options
        ):
            raise ValueError("correction options must be a non-empty string list")
        updates["options_json"] = json.dumps(
            [option.strip() for option in audited_options], ensure_ascii=False
        )
    if args.difficulty is not None:
        if not 1 <= args.difficulty <= 5:
            raise ValueError("difficulty must be 1..5")
        updates["difficulty"] = args.difficulty
    if args.grade is not None:
        updates["grade"] = args.grade
    if args.question_type:
        updates["question_type"] = args.question_type
    if args.solution_file:
        updates["solution"] = args.solution_file.read_text(encoding="utf-8").strip()
    elif correction.get("solution"):
        updates["solution"] = str(correction["solution"]).strip()
    if updates:
        conn.execute(
            f"UPDATE questions SET {','.join(f'{key}=?' for key in updates)} WHERE id=?",
            (*updates.values(), args.question_id),
        )
    if args.replace_tags:
        conn.execute("DELETE FROM question_knowledge WHERE question_id=?", (args.question_id,))
        conn.execute("DELETE FROM question_targets WHERE question_id=?", (args.question_id,))
    if getattr(args, "replace_features", False):
        conn.execute("DELETE FROM question_features WHERE question_id=?", (args.question_id,))
    for code in args.knowledge or []:
        conn.execute(
            "INSERT OR IGNORE INTO question_knowledge(question_id,knowledge_code) VALUES(?,?)",
            (args.question_id, code),
        )
    for cause in args.target_cause or []:
        conn.execute(
            "INSERT OR IGNORE INTO question_targets(question_id,cause_code) VALUES(?,?)",
            (args.question_id, cause),
        )
    for code in feature_codes:
        conn.execute(
            """INSERT INTO question_features(question_id,feature_code,source,confidence)
               VALUES(?,?,?,?)
               ON CONFLICT(question_id,feature_code) DO UPDATE SET
                   source=excluded.source, confidence=excluded.confidence""",
            (args.question_id, code, "audited", 1.0),
        )
    if args.verify:
        refreshed = conn.execute("SELECT * FROM questions WHERE id=?", (args.question_id,)).fetchone()
        tag_count = conn.execute(
            "SELECT COUNT(*) FROM question_knowledge WHERE question_id=?", (args.question_id,)
        ).fetchone()[0]
        missing = []
        if not refreshed["answer"]:
            missing.append("answer")
        if not refreshed["solution"]:
            missing.append("solution")
        if not refreshed["source_name"] or not refreshed["license"]:
            missing.append("source/license")
        if not tag_count:
            missing.append("knowledge tag")
        audited_text = "\n".join(
            str(refreshed[key] or "") for key in ("stem", "answer", "solution")
        )
        if re.search(r"[\ue000-\uf8ff]", audited_text):
            missing.append("unresolved PDF private-use glyph")
        if "##" in audited_text:
            missing.append("broken OCR marker")
        if missing:
            raise ValueError("cannot verify; missing " + ", ".join(missing))
        conn.execute("UPDATE questions SET verified=1 WHERE id=?", (args.question_id,))
    if commit:
        conn.commit()
    final = conn.execute("SELECT id,grade,difficulty,question_type,verified FROM questions WHERE id=?", (args.question_id,)).fetchone()
    tags = [row[0] for row in conn.execute(
        "SELECT knowledge_code FROM question_knowledge WHERE question_id=? ORDER BY knowledge_code", (args.question_id,)
    )]
    return {
        **dict(final),
        "knowledge_codes": tags,
        "feature_codes": question_feature_codes(conn, args.question_id),
    }


def question_issue_codes(
    row: sqlite3.Row | dict[str, Any], tag_count: int, target_count: int
) -> list[str]:
    text = "\n".join(str(row[key] or "") for key in ("stem", "answer", "solution"))
    issues: list[str] = []
    if not bool((row["solution"] or "").strip()):
        issues.append("missing_solution")
    if tag_count == 0:
        issues.append("missing_tags")
    if target_count == 0:
        issues.append("missing_targets")
    if "选择" in (row["question_type"] or "") and not row["options_json"]:
        issues.append("missing_choice_options")
    if re.search(r"[\ue000-\uf8ff]", text):
        issues.append("private_use_glyph")
    if "##" in text:
        issues.append("broken_marker")
    if re.search(r"证明见解析|答案见解析|需根据|轨迹方程见解析", row["answer"] or ""):
        issues.append("placeholder_answer")
    if re.search(r"如图|图中|下图|直方图", row["stem"] or ""):
        issues.append("diagram_reference")
    return issues


def near_duplicate_candidates(
    conn: sqlite3.Connection, question_id: str, stem: str, limit: int = 3
) -> list[dict[str, Any]]:
    normalized = re.sub(r"\s+", "", stem).casefold()
    if not normalized:
        return []
    candidates: list[tuple[float, sqlite3.Row]] = []
    for row in conn.execute(
        "SELECT id,stem,source_name,verified FROM questions WHERE id<>?", (question_id,)
    ):
        other = re.sub(r"\s+", "", row["stem"] or "").casefold()
        if not other:
            continue
        length_ratio = min(len(normalized), len(other)) / max(len(normalized), len(other))
        if length_ratio < 0.65:
            continue
        ratio = difflib.SequenceMatcher(None, normalized, other, autojunk=False).ratio()
        if ratio >= 0.82:
            candidates.append((ratio, row))
    candidates.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [
        {
            "question_id": row["id"],
            "similarity": round(ratio, 3),
            "source_name": row["source_name"],
            "verified": row["verified"],
            "stem_preview": row["stem"][:160],
        }
        for ratio, row in candidates[:limit]
    ]


def audit_item(
    conn: sqlite3.Connection,
    question_id: str,
    out_path: Path | None = None,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
    if not row:
        raise ValueError(f"question not found: {question_id}")
    tags = [item[0] for item in conn.execute(
        "SELECT knowledge_code FROM question_knowledge WHERE question_id=? ORDER BY knowledge_code",
        (question_id,),
    )]
    targets = [item[0] for item in conn.execute(
        "SELECT cause_code FROM question_targets WHERE question_id=? ORDER BY cause_code",
        (question_id,),
    )]
    source = conn.execute(
        """SELECT name,url,license,rights_confirmed,retrieved_at,notes
           FROM sources WHERE name=? ORDER BY rights_confirmed DESC,id LIMIT 1""",
        (row["source_name"],),
    ).fetchone()
    packet = {
        "schema": "math-question-audit-packet/v1",
        "question": {
            "id": row["id"],
            "stem": row["stem"],
            "options": json.loads(row["options_json"]) if row["options_json"] else None,
            "stored_answer": row["answer"],
            "stored_solution": row["solution"],
            "grade": row["grade"],
            "question_type": row["question_type"],
            "difficulty": row["difficulty"],
            "verified": row["verified"],
            "source_name": row["source_name"],
            "source_url": row["source_url"],
            "license": row["license"],
            "knowledge_codes": tags,
            "target_causes": targets,
            "feature_codes": question_feature_codes(
                conn, question_id, row["stem"], row["question_type"]
            ),
        },
        "source_record": dict(source) if source else None,
        "automatic_checks": {
            "issues": question_issue_codes(row, len(tags), len(targets)),
            "near_duplicates": near_duplicate_candidates(conn, question_id, row["stem"]),
        },
        "required_review": [
            "核对题干、选项和图形条件是否完整",
            "独立推导答案，不采信外部 verified 字段",
            "核对或补全逐步解析",
            "核对知识点、题型、难度和结构特征",
            "核对来源授权并处理重复项",
        ],
    }
    canonical = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    packet["packet_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "question_id": question_id,
            "packet_path": str(out_path.resolve()),
            "packet_sha256": packet["packet_sha256"],
            "issues": packet["automatic_checks"]["issues"],
            "near_duplicate_count": len(packet["automatic_checks"]["near_duplicates"]),
        }
    return packet


def apply_verification_review(
    conn: sqlite3.Connection,
    question_id: str,
    review_path: Path,
) -> dict[str, Any]:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if not isinstance(review, dict):
        raise ValueError("review must be a JSON object")
    if str(review.get("question_id") or "") != question_id:
        raise ValueError("review question_id does not match command question_id")
    verdict = str(review.get("verdict") or "").strip()
    if verdict not in {"pass", "corrected", "needs_revision", "reject"}:
        raise ValueError("verdict must be pass, corrected, needs_revision, or reject")
    reviewer = str(review.get("reviewer") or "").strip()
    if not reviewer:
        raise ValueError("reviewer is required")
    canonical = json.dumps(review, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    review_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    review_id = slug_id("VR")

    if verdict in {"needs_revision", "reject"}:
        conn.execute(
            """INSERT INTO verification_reviews
               (id,question_id,verdict,reviewer,review_sha256,review_json,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (review_id, question_id, verdict, reviewer, review_sha256, canonical, now_iso()),
        )
        conn.commit()
        return {"question_id": question_id, "verified": 0, "verdict": verdict, "review_id": review_id}

    checklist = review.get("checklist") or {}
    required_checks = (
        "stem_complete", "source_checked", "duplicate_checked",
        "answer_derived", "solution_checked",
    )
    missing_checks = [name for name in required_checks if checklist.get(name) is not True]
    if missing_checks:
        raise ValueError("cannot verify; checklist not confirmed: " + ", ".join(missing_checks))
    independent_answer = str(review.get("independent_answer") or "").strip()
    independent_solution = str(review.get("independent_solution") or "").strip()
    if not independent_answer or not independent_solution:
        raise ValueError("independent_answer and independent_solution are required")
    answer_check = review.get("answer_check")
    solution_check = review.get("solution_check")
    if answer_check not in {"match", "corrected"}:
        raise ValueError("answer_check must be match or corrected")
    if solution_check not in {"match", "corrected", "completed"}:
        raise ValueError("solution_check must be match, corrected, or completed")
    knowledge = review.get("knowledge_codes") or []
    if not isinstance(knowledge, list) or not knowledge:
        raise ValueError("knowledge_codes must be a non-empty list")
    targets = review.get("target_causes") or []
    features = validate_feature_codes(review.get("feature_codes") or [])
    difficulty = review.get("difficulty")
    grade = review.get("grade")
    question_type = str(review.get("question_type") or "").strip()
    if difficulty is None or grade is None or not question_type:
        raise ValueError("grade, difficulty, and question_type are required")
    correction = dict(review.get("correction") or {})
    correction["id"] = question_id
    if answer_check == "corrected":
        correction["answer"] = str(correction.get("answer") or independent_answer).strip()
    current = conn.execute("SELECT solution FROM questions WHERE id=?", (question_id,)).fetchone()
    if not current:
        raise ValueError(f"question not found: {question_id}")
    if solution_check in {"corrected", "completed"} or not (current["solution"] or "").strip():
        correction["solution"] = str(correction.get("solution") or independent_solution).strip()

    args = argparse.Namespace(
        question_id=question_id,
        knowledge=knowledge,
        target_cause=targets,
        feature=features,
        difficulty=float(difficulty),
        grade=int(grade),
        question_type=question_type,
        correction_file=None,
        stem_file=None,
        answer_file=None,
        solution_file=None,
        replace_tags=True,
        replace_features=True,
        verify=True,
    )
    result = annotate_question(conn, args, correction_override=correction, commit=False)
    conn.execute(
        """INSERT INTO verification_reviews
           (id,question_id,verdict,reviewer,review_sha256,review_json,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (review_id, question_id, verdict, reviewer, review_sha256, canonical, now_iso()),
    )
    conn.commit()
    return {
        **result,
        "verdict": verdict,
        "review_id": review_id,
        "review_sha256": review_sha256,
    }


def apply_verification_review_batch(
    conn: sqlite3.Connection,
    manifest_path: Path,
) -> dict[str, Any]:
    """Apply item-level review files through the canonical verifier with compact output."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("items") if isinstance(manifest, dict) else None
    if not isinstance(items, list):
        raise ValueError("review manifest must contain an items array")
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for item in items:
        question_id = str((item or {}).get("question_id") or "").strip()
        review_value = str((item or {}).get("review") or "").strip()
        if not question_id or not review_value:
            failures.append({
                "question_id": question_id,
                "error": "question_id and review are required",
            })
            continue
        review_path = Path(review_value)
        if not review_path.is_absolute():
            review_path = manifest_path.parent / review_path
        try:
            result = apply_verification_review(conn, question_id, review_path)
            results.append({
                "question_id": question_id,
                "verdict": result["verdict"],
                "verified": int(result["verified"]),
                "review_id": result["review_id"],
            })
        except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
            failures.append({"question_id": question_id, "error": str(exc)})
    return {
        "processed": len(results),
        "verified": sum(item["verified"] for item in results),
        "needs_revision": sum(
            item["verdict"] == "needs_revision" for item in results
        ),
        "rejected": sum(item["verdict"] == "reject" for item in results),
        "failed": len(failures),
        "failures": failures,
    }


def repair_embedded_options(
    conn: sqlite3.Connection,
    verified_only: bool,
    source_name: str | None = None,
) -> dict[str, Any]:
    clauses = ["options_json IS NULL"]
    if verified_only:
        clauses.append("verified=1")
    params: list[Any] = []
    if source_name:
        clauses.append("source_name=?")
        params.append(source_name)
    rows = conn.execute(
        f"SELECT id,stem,raw_json FROM questions WHERE {' AND '.join(clauses)} ORDER BY id",
        params,
    )
    pattern = re.compile(
        r"\n\nA\.\s*(.*?)\n\nB\.\s*(.*?)\n\nC\.\s*(.*?)\n\nD\.\s*(.*?)\s*$",
        re.DOTALL,
    )
    updated: list[str] = []
    for row in rows:
        options: list[str] = []
        try:
            raw = json.loads(row["raw_json"] or "{}")
            raw_options = raw.get("options") or raw.get("choices") or raw.get("options_json")
            if isinstance(raw_options, str):
                raw_options = json.loads(raw_options)
            if isinstance(raw_options, list):
                options = [str(part).strip() for part in raw_options]
        except (TypeError, ValueError, json.JSONDecodeError):
            options = []
        if not options:
            match = pattern.search(row["stem"])
            if match:
                options = [part.strip() for part in match.groups()]
        if not options or not all(options):
            continue
        conn.execute(
            "UPDATE questions SET options_json=? WHERE id=?",
            (json.dumps(options, ensure_ascii=False), row["id"]),
        )
        updated.append(row["id"])
    conn.commit()
    return {"updated": len(updated), "question_ids": updated}


def audit_queue(
    conn: sqlite3.Connection, limit: int, source_name: str | None = None, full: bool = False
) -> list[dict[str, Any]]:
    source_clause = " AND q.source_name=?" if source_name else ""
    params: tuple[Any, ...] = (source_name, limit) if source_name else (limit,)
    fields = (
        "q.id,q.stem,q.options_json,q.answer,q.solution,q.grade,q.question_type,"
        "q.difficulty,q.source_name,q.source_url"
        if full else
        "q.id,q.stem,q.answer,q.grade,q.question_type,q.difficulty,q.source_name"
    )
    return [dict(row) for row in conn.execute(
        f"""SELECT {fields},
                  COUNT(qk.knowledge_code) AS tag_count
           FROM questions q LEFT JOIN question_knowledge qk ON qk.question_id=q.id
           WHERE q.verified=0{source_clause} GROUP BY q.id
           ORDER BY tag_count ASC,q.source_name,q.id LIMIT ?""",
        params,
    )]


def audit_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT q.*,
                  COUNT(DISTINCT qk.knowledge_code) AS tag_count,
                  COUNT(DISTINCT qt.cause_code) AS target_count
           FROM questions q
           LEFT JOIN question_knowledge qk ON qk.question_id=q.id
           LEFT JOIN question_targets qt ON qt.question_id=q.id
           WHERE q.verified=0
           GROUP BY q.id
           ORDER BY q.source_name,q.id"""
    ).fetchall()
    issue_keys = (
        "missing_solution", "missing_tags", "missing_targets", "missing_choice_options",
        "private_use_glyph", "broken_marker", "placeholder_answer", "diagram_reference",
    )
    totals = {key: 0 for key in issue_keys}
    sources: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = row["source_name"]
        summary = sources.setdefault(source, {"questions": 0, **{key: 0 for key in issue_keys}})
        summary["questions"] += 1
        present_issues = set(question_issue_codes(row, row["tag_count"], row["target_count"]))
        for key in issue_keys:
            if key in present_issues:
                totals[key] += 1
                summary[key] += 1
    return {
        "unverified_questions": len(rows),
        "issues": totals,
        "sources": [
            {"source_name": source, **summary}
            for source, summary in sorted(
                sources.items(), key=lambda item: (-item[1]["questions"], item[0])
            )
        ],
    }


def _read_project_config(project_root: Path) -> dict[str, Any]:
    path = project_root / "config" / "math-error-notebook.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _find_soffice() -> Path | None:
    candidates = (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    )
    return next((path for path in candidates if path.is_file()), None)


def _git_summary(project_root: Path) -> dict[str, Any]:
    command = [
        "git", "-c", f"safe.directory={project_root.resolve()}",
        "-C", str(project_root.resolve()),
    ]
    try:
        head = subprocess.run(
            [*command, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", timeout=5,
        )
        status = subprocess.run(
            [*command, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, encoding="utf-8", timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "head": None, "tracked_dirty": None}
    return {
        "available": head.returncode == 0,
        "head": head.stdout.strip() or None,
        "tracked_dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
    }


def doctor(
    conn: sqlite3.Connection, db_path: Path, project_root: Path
) -> dict[str, Any]:
    """Run one deterministic, read-only project startup check."""
    info = bank_info(conn, db_path)
    required = (
        project_root / "AGENTS.md",
        project_root / "PROJECT_ARCHITECTURE.md",
        project_root / ".agents" / "skills" / "math-error-notebook" / "SKILL.md",
        project_root / ".agents" / "skills" / "math-error-notebook" / "assets" / "error-analysis-template.json",
        project_root / ".agents" / "skills" / "math-error-notebook" / "assets" / "question-review-template.json",
    )
    runtime = project_root / "runtime" / "pdf"
    added_runtime = False
    if runtime.is_dir() and str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
        added_runtime = True
    try:
        pdf_dependencies = {
            module: importlib.util.find_spec(module) is not None
            for module in ("reportlab", "PIL", "matplotlib", "pypdf")
        }
    finally:
        if added_runtime:
            sys.path.remove(str(runtime))
    config = _read_project_config(project_root)
    checks = {
        "canonical_database": Path(info["canonical_path"]) == DEFAULT_DB.resolve(),
        "integrity": info["integrity_check"] == "ok",
        "foreign_keys": info["foreign_key_violations"] == 0,
        "schema": str(info["schema_version"]) == str(SCHEMA_VERSION),
        "required_project_files": all(path.is_file() for path in required),
    }
    warnings: list[str] = []
    missing_pdf = [name for name, available in pdf_dependencies.items() if not available]
    if missing_pdf:
        warnings.append("missing_pdf_dependencies:" + ",".join(missing_pdf))
    if not _find_soffice():
        warnings.append("libreoffice_not_found")
    if not config.get("printer_name"):
        warnings.append("printer_not_configured")
    return {
        "status": "ok" if all(checks.values()) else "error",
        "checks": checks,
        "warnings": warnings,
        "bank": {
            "canonical_path": info["canonical_path"],
            "sha256": info["sha256"],
            "schema_version": info["schema_version"],
            "questions": info["questions"],
            "verified": info["verified_questions"],
            "unverified": info["questions"] - info["verified_questions"],
            "errors": info["errors"],
        },
        "pdf_dependencies": pdf_dependencies,
        "printer": config.get("printer_name"),
        "libreoffice": str(_find_soffice()) if _find_soffice() else None,
    }


def handoff_snapshot(
    conn: sqlite3.Connection, db_path: Path, project_root: Path
) -> dict[str, Any]:
    """Return a compact operational snapshot suitable for another agent."""
    health = doctor(conn, db_path, project_root)
    audit = audit_summary(conn)
    nonzero_issues = {key: value for key, value in audit["issues"].items() if value}
    top_sources = [
        {
            "source_name": row["source_name"],
            "questions": row["questions"],
            "issues": {
                key: value
                for key, value in row.items()
                if key not in {"source_name", "questions"} and value
            },
        }
        for row in audit["sources"][:5]
    ]
    return {
        "generated_at": now_iso(),
        "status": health["status"],
        "bank": health["bank"],
        "unverified_issues": nonzero_issues,
        "top_pending_sources": top_sources,
        "due_reviews": len(review_due(conn, date.today())),
        "git": _git_summary(project_root),
        "warnings": health["warnings"],
        "defaults": {"answers": False, "print": False},
        "reliable_batch_exception": RELIABLE_BATCH,
    }


AGENT_TASK_CONTEXT: dict[str, dict[str, Any]] = {
    "grade": {
        "commands": [
            "causes --text <topic> --json",
            "knowledge --text <topic> --json",
            "features --text <structure> --json",
            "grade-preview <analysis.json> --json",
            "grade-commit <analysis.json> --copy-image --json",
            "recommend-packet <error-id> --out <packet.json> --json",
        ],
        "optional_reference": "references/error-taxonomy.md only when cause selection is ambiguous",
    },
    "recommend": {
        "commands": [
            "recommend-packet <error-id> --keyword <type> --feature <code> --out <packet.json> --json",
            "assign-recommendations <error-id> <reviewed-plan.json> --save --json",
            "practice_sheet.py <error-id>",
        ],
    },
    "verify": {
        "commands": [
            "prepare-audit-batch --source-name <source> --limit <n> --out-dir <dir> --json",
            "verify-item <question-id> <review.json> --json",
            "verify-review-batch <manifest.json> --json",
        ],
        "required_reference": "references/import-and-verification.md",
    },
    "import": {
        "commands": [
            "scripts/extract_docx_omml.py <docx> --output-dir <dir>",
            "scripts/build_omml_exam_import.py <extracted.json> --output <jsonl>",
            "import-file <jsonl> --source-name <name> --license <license> --rights-confirmed --json",
            "prepare-audit-batch --source-name <source> --limit <n> --out-dir <dir> --json",
        ],
        "required_reference": "references/import-and-verification.md",
    },
    "review": {
        "commands": [
            "due --json", "attempt <question-id> --error-id <error-id> --correct|--wrong --json",
            "review <error-id> --result correct|partial|wrong --json", "stats --json",
        ],
    },
    "pdf": {
        "commands": [
            "practice_sheet.py <error-id>",
            "practice_sheet.py <error-id> --with-answers",
            "practice_sheet.py <error-id> --print  # only after explicit user request",
        ],
    },
    "maintenance": {
        "commands": ["doctor --json", "handoff --json", "audit-summary --json", "coverage --json"],
    },
}


def agent_context(
    conn: sqlite3.Connection, db_path: Path, project_root: Path, task: str
) -> dict[str, Any]:
    health = doctor(conn, db_path, project_root)
    task_context = AGENT_TASK_CONTEXT[task]
    return {
        "task": task,
        "health": {
            "status": health["status"],
            "canonical_path": health["bank"]["canonical_path"],
            "integrity": health["checks"]["integrity"],
            "foreign_keys": health["checks"]["foreign_keys"],
            "questions": health["bank"]["questions"],
            "verified": health["bank"]["verified"],
            "unverified": health["bank"]["unverified"],
        },
        "critical_rules": [
            "use only data/math_notebook.db through notebook.py",
            "never verify from source reputation, external verified, sampling, or bulk SQL",
            "recommend only verified questions and review relevance before save",
            "PDF defaults to no answers and no print",
            "mathematical judgment remains model-reviewed; deterministic scripts do not replace it",
        ],
        "reliable_batch_exception": RELIABLE_BATCH if task == "verify" else None,
        **task_context,
    }


def prepare_audit_batch(
    conn: sqlite3.Connection,
    source_name: str | None,
    limit: int,
    out_dir: Path,
    reviewer: str,
    force: bool,
) -> dict[str, Any]:
    """Create packets and safe, unsubmitted review skeletons without changing the DB."""
    rows = audit_queue(conn, limit, source_name, full=False)
    packets_dir = out_dir / "packets"
    reviews_dir = out_dir / "reviews"
    items: list[dict[str, Any]] = []
    skipped: list[str] = []
    for row in rows:
        question_id = row["id"]
        safe_id = re.sub(r"[^0-9A-Za-z._-]+", "_", question_id)
        packet_path = packets_dir / f"{safe_id}.json"
        review_path = reviews_dir / f"{safe_id}.review.json"
        if review_path.exists() and not force:
            skipped.append(question_id)
            continue
        summary = audit_item(conn, question_id, packet_path)
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        question = packet["question"]
        review = {
            "question_id": question_id,
            "verdict": "pending",
            "reviewer": reviewer,
            "checklist": {
                "stem_complete": False,
                "source_checked": False,
                "duplicate_checked": False,
                "answer_derived": False,
                "solution_checked": False,
            },
            "independent_answer": "",
            "independent_solution": "",
            "answer_check": "pending",
            "solution_check": "pending",
            "knowledge_codes": question["knowledge_codes"],
            "target_causes": question["target_causes"],
            "feature_codes": question["feature_codes"],
            "grade": question["grade"],
            "difficulty": question["difficulty"],
            "question_type": question["question_type"],
            "correction": {},
            "review_note": "",
            "packet_sha256": packet["packet_sha256"],
        }
        reviews_dir.mkdir(parents=True, exist_ok=True)
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        items.append({
            "question_id": question_id,
            "packet": str(packet_path.resolve()),
            "review": str(review_path.resolve()),
            "issues": summary["issues"],
            "near_duplicates": summary["near_duplicate_count"],
        })
    manifest = {
        "schema": "math-audit-work-batch/v1",
        "created_at": now_iso(),
        "source_name": source_name,
        "items": items,
        "skipped_existing_reviews": skipped,
        "next_command": "verify-item <question-id> <review.json> --json",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "prepared": len(items),
        "skipped": len(skipped),
        "manifest": str(manifest_path.resolve()),
        "question_ids": [item["question_id"] for item in items],
        "database_modified": False,
    }


def recommendation_packet(
    conn: sqlite3.Connection,
    error_id: str,
    limit: int,
    project_root: Path,
    keywords: list[str] | None,
    features: list[str] | None,
    out_path: Path,
) -> dict[str, Any]:
    items = recommend(
        conn, error_id, limit, False, project_root, keywords, False, True, features
    )
    packet = {
        "schema": "math-recommendation-review-packet/v1",
        "error_id": error_id,
        "generated_at": now_iso(),
        "review_required": True,
        "save_command": f"assign-recommendations {error_id} <reviewed-plan.json> --save --json",
        "items": items,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "error_id": error_id,
        "candidates": len(items),
        "packet": str(out_path.resolve()),
        "question_ids": [item["question_id"] for item in items],
        "database_modified": False,
    }


def print_output(payload: Any, as_json: bool, pretty_json: bool = False) -> None:
    if as_json:
        if pretty_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            print(f"{key}: {value}")
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                print(" | ".join(f"{key}={value}" for key, value in item.items() if value is not None))
            else:
                print(item)
    else:
        print(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="高中数学错题本数据库工具")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument("--pretty-json", action="store_true", help="indent JSON output for humans")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="initialize database and knowledge taxonomy")
    p.add_argument("--knowledge-file", type=Path, default=DEFAULT_KNOWLEDGE)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("seed", help="import bundled original seed questions")
    p.add_argument("--file", type=Path, default=DEFAULT_SEED)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("import-file", help="import authorized JSON, JSONL, or CSV questions")
    p.add_argument("path", type=Path)
    p.add_argument("--source-name", required=True)
    p.add_argument("--source-url")
    p.add_argument("--license", required=True)
    p.add_argument("--rights-confirmed", action="store_true", required=True)
    p.add_argument("--verified", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("import-url", help="download and import an authorized structured dataset")
    p.add_argument("url")
    p.add_argument("--source-name", required=True)
    p.add_argument("--license", required=True)
    p.add_argument("--rights-confirmed", action="store_true", required=True)
    p.add_argument("--verified", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("register-exam-dir", help="catalog authorized local PDF exam sources")
    p.add_argument("directory", type=Path)
    p.add_argument("--license", required=True)
    p.add_argument("--rights-confirmed", action="store_true", required=True)
    p.add_argument("--manifest-out", type=Path)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("sync-source-manifest", help="sync source notes from a catalog manifest")
    p.add_argument("manifest", type=Path)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("update-source", help="correct provenance for one question source")
    p.add_argument("--source-name", required=True)
    p.add_argument("--source-url", required=True)
    p.add_argument("--license", required=True)
    p.add_argument("--rights-confirmed", action="store_true")
    p.add_argument("--notes")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("record-error", help="store structured image/text error analysis")
    p.add_argument("analysis", type=Path, help="analysis JSON file")
    p.add_argument("--project-root", type=Path, default=Path.cwd())
    p.add_argument("--copy-image", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("grade-preview", help="validate an error analysis without writing")
    p.add_argument("analysis", type=Path, help="analysis JSON file")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("grade-commit", help="validate and store an error analysis")
    p.add_argument("analysis", type=Path, help="analysis JSON file")
    p.add_argument("--project-root", type=Path, default=Path.cwd())
    p.add_argument("--copy-image", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("delete-error", help="remove a mistakenly recorded error")
    p.add_argument("error_id")
    p.add_argument("--project-root", type=Path, default=Path.cwd())
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("recommend", help="recommend verified questions for an error")
    p.add_argument("error_id")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--save", action="store_true")
    p.add_argument("--replace", action="store_true", help="replace saved recommendations for this error")
    p.add_argument("--keyword", action="append", help="boost questions containing this type keyword")
    p.add_argument("--feature", action="append", choices=tuple(FEATURE_CODES), help="match an audited structural feature")
    p.add_argument("--full", action="store_true", help="include answers, solutions, and source URL")
    p.add_argument("--project-root", type=Path, default=Path.cwd())
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("recommend-packet", help="write full recommendation candidates for model review")
    p.add_argument("error_id")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--keyword", action="append")
    p.add_argument("--feature", action="append", choices=tuple(FEATURE_CODES))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--project-root", type=Path, default=Path.cwd())
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("assign-recommendations", help="replace recommendations with a reviewed verified set")
    p.add_argument("error_id")
    p.add_argument("plan", type=Path, help="JSON file containing an items array")
    p.add_argument("--save", action="store_true")
    p.add_argument("--full", action="store_true", help="include answers, solutions, and source URL")
    p.add_argument("--project-root", type=Path, default=Path.cwd())
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("due", help="list due and overdue reviews")
    p.add_argument("--date")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("review", help="mark the next review stage")
    p.add_argument("error_id")
    p.add_argument("--result", choices=("correct", "partial", "wrong"), required=True)
    p.add_argument("--note")
    p.add_argument("--date")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("attempt", help="record a practice attempt")
    p.add_argument("question_id")
    p.add_argument("--error-id")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--correct", action="store_true")
    group.add_argument("--wrong", action="store_true")
    p.add_argument("--answer")
    p.add_argument("--cause-code", choices=tuple(CAUSE_CODES))
    p.add_argument("--note")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("search", help="search question bank")
    p.add_argument("--knowledge")
    p.add_argument("--grade", type=int, choices=(10, 11, 12))
    p.add_argument("--difficulty-min", type=float)
    p.add_argument("--difficulty-max", type=float)
    p.add_argument("--text")
    p.add_argument("--verified", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--full", action="store_true", help="return full question rows")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("sources", help="list compact source records and question counts")
    p.add_argument("--text")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("question", help="show one question in full")
    p.add_argument("question_id")
    p.add_argument("--raw", action="store_true", help="include original raw import JSON")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("knowledge", help="list compact knowledge-point codes")
    p.add_argument("--text")
    p.add_argument("--grade", type=int, choices=(10, 11, 12))
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("causes", help="list compact error-cause codes")
    p.add_argument("--text")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("features", help="list structural feature codes for recommendation")
    p.add_argument("--text")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("annotate", help="tag, enrich, and optionally verify a question")
    p.add_argument("question_id")
    p.add_argument("--knowledge", action="append")
    p.add_argument("--target-cause", action="append", choices=tuple(CAUSE_CODES))
    p.add_argument("--feature", action="append", choices=tuple(FEATURE_CODES))
    p.add_argument("--difficulty", type=float)
    p.add_argument("--grade", type=int, choices=(10, 11, 12))
    p.add_argument("--question-type")
    p.add_argument("--correction-file", type=Path)
    p.add_argument("--stem-file", type=Path)
    p.add_argument("--answer-file", type=Path)
    p.add_argument("--solution-file", type=Path)
    p.add_argument("--replace-tags", action="store_true")
    p.add_argument("--replace-features", action="store_true")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("audit-queue", help="list unverified imported questions")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--source-name")
    p.add_argument("--full", action="store_true", help="include solution and source URL")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("audit-item", help="build one complete question-level audit packet")
    p.add_argument("question_id")
    p.add_argument("--out", type=Path, help="write full packet and return only a compact summary")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("prepare-audit-batch", help="create audit packets and pending review skeletons")
    p.add_argument("--source-name")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--reviewer", default="codex")
    p.add_argument("--force", action="store_true", help="overwrite existing pending review files")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("verify-item", help="apply one structured independent review and optionally verify")
    p.add_argument("question_id")
    p.add_argument("review", type=Path)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("verify-review-batch", help="apply item-level review files from one manifest")
    p.add_argument("manifest", type=Path)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("backfill-features", help="infer auditable structural features for existing questions")
    p.add_argument("--verified-only", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("repair-embedded-options", help="backfill structured A-D options from stems")
    p.add_argument("--verified-only", action="store_true")
    p.add_argument("--source-name")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("agent-context", help="return compact task-specific rules and commands")
    p.add_argument("--task", choices=tuple(AGENT_TASK_CONTEXT), required=True)
    p.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("doctor", help="run one read-only project startup check")
    p.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("handoff", help="return a compact operational handoff snapshot")
    p.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    p.add_argument("--json", action="store_true")

    for name, help_text in (
        ("bank-info", "show canonical bank identity and integrity"),
        ("audit-summary", "group unverified question issues by source"),
        ("stats", "show learner statistics"),
        ("coverage", "audit curriculum coverage"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        conn = connect(args.db)
        if args.command == "init":
            payload = init_database(conn, args.knowledge_file)
        else:
            init_database(conn)
            if args.command == "seed":
                payload = import_records(conn, read_json_records(args.file), "内置原创示例题", None, "Project-Original", True)
            elif args.command == "import-file":
                if not args.rights_confirmed:
                    raise ValueError("--rights-confirmed is required")
                payload = import_records(conn, read_json_records(args.path), args.source_name, args.source_url, args.license, args.verified)
                conn.execute("INSERT OR IGNORE INTO sources(name,url,license,rights_confirmed,retrieved_at) VALUES(?,?,?,?,?)",
                             (args.source_name, args.source_url, args.license, 1, now_iso()))
                conn.commit()
            elif args.command == "import-url":
                if not args.rights_confirmed:
                    raise ValueError("--rights-confirmed is required")
                records, raw_path = fetch_json(args.url, args.db.parent / "raw")
                payload = import_records(conn, records, args.source_name, args.url, args.license, args.verified)
                payload["raw_path"] = str(raw_path)
                conn.execute("INSERT OR IGNORE INTO sources(name,url,license,rights_confirmed,retrieved_at) VALUES(?,?,?,?,?)",
                             (args.source_name, args.url, args.license, 1, now_iso()))
                conn.commit()
            elif args.command == "register-exam-dir":
                if not args.rights_confirmed:
                    raise ValueError("--rights-confirmed is required")
                payload = register_exam_directory(
                    conn, args.directory, args.license, args.manifest_out
                )
            elif args.command == "sync-source-manifest":
                payload = sync_source_manifest(conn, args.manifest)
            elif args.command == "update-source":
                payload = update_source_metadata(
                    conn,
                    args.source_name,
                    args.source_url,
                    args.license,
                    args.rights_confirmed,
                    args.notes,
                )
            elif args.command == "record-error":
                analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
                payload = {"error_id": record_error(conn, analysis, args.project_root, args.copy_image)}
            elif args.command == "grade-preview":
                analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
                payload = validate_error_analysis(conn, analysis)
                payload["database_modified"] = False
            elif args.command == "grade-commit":
                analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
                payload = {
                    "error_id": record_error(conn, analysis, args.project_root, args.copy_image),
                    "database_modified": True,
                }
            elif args.command == "delete-error":
                payload = delete_error(conn, args.error_id, args.project_root)
            elif args.command == "recommend":
                payload = recommend(
                    conn, args.error_id, max(1, min(args.limit, 10)), args.save,
                    args.project_root, args.keyword, args.replace, args.full, args.feature,
                )
            elif args.command == "recommend-packet":
                payload = recommendation_packet(
                    conn, args.error_id, max(1, min(args.limit, 10)), args.project_root,
                    args.keyword, args.feature, args.out,
                )
            elif args.command == "assign-recommendations":
                plan = json.loads(args.plan.read_text(encoding="utf-8"))
                payload = assign_recommendations(
                    conn, args.error_id, plan.get("items") or [], args.project_root,
                    args.save, args.full,
                )
            elif args.command == "due":
                payload = review_due(conn, parse_date(args.date))
            elif args.command == "review":
                payload = mark_review(conn, args.error_id, args.result, args.note, parse_date(args.date))
            elif args.command == "attempt":
                args.correct = not args.wrong
                payload = {"attempt_id": record_attempt(conn, args)}
            elif args.command == "search":
                payload = search_questions(conn, args)
            elif args.command == "sources":
                payload = list_sources(conn, args.text)
            elif args.command == "question":
                payload = question_detail(conn, args.question_id, args.raw)
            elif args.command == "knowledge":
                payload = list_knowledge_points(conn, args.text, args.grade)
            elif args.command == "causes":
                payload = list_cause_codes(args.text)
            elif args.command == "features":
                payload = list_feature_codes(args.text)
            elif args.command == "annotate":
                payload = annotate_question(conn, args)
            elif args.command == "audit-queue":
                payload = audit_queue(
                    conn, max(1, min(args.limit, 500)), args.source_name, args.full
                )
            elif args.command == "audit-item":
                payload = audit_item(conn, args.question_id, args.out)
            elif args.command == "prepare-audit-batch":
                payload = prepare_audit_batch(
                    conn, args.source_name, max(1, min(args.limit, 100)), args.out_dir,
                    args.reviewer, args.force,
                )
            elif args.command == "verify-item":
                payload = apply_verification_review(conn, args.question_id, args.review)
            elif args.command == "verify-review-batch":
                payload = apply_verification_review_batch(conn, args.manifest)
            elif args.command == "backfill-features":
                payload = backfill_question_features(conn, args.verified_only)
            elif args.command == "repair-embedded-options":
                payload = repair_embedded_options(
                    conn, args.verified_only, args.source_name
                )
            elif args.command == "agent-context":
                payload = agent_context(conn, args.db, args.project_root, args.task)
            elif args.command == "doctor":
                payload = doctor(conn, args.db, args.project_root)
            elif args.command == "handoff":
                payload = handoff_snapshot(conn, args.db, args.project_root)
            elif args.command == "bank-info":
                payload = bank_info(conn, args.db)
            elif args.command == "audit-summary":
                payload = audit_summary(conn)
            elif args.command == "stats":
                payload = stats(conn)
            elif args.command == "coverage":
                payload = coverage(conn)
            else:
                parser.error(f"unknown command: {args.command}")
                return 2
        print_output(payload, getattr(args, "json", False), args.pretty_json)
        return 0
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
