"""Convert extracted Dongzhimen questions to import-ready JSONL with correct encoding."""
import json

with open("data/imports/2026-07-18-dongzhimen/questions.jsonl", "r", encoding="utf-8") as f:
    questions = [json.loads(l) for l in f]

# Map section names to question types (actual Chinese chars, not unicode escapes)
type_map = {
    "一、单选题": "单选题",
    "二、填空题": "填空题",
    "三、解答题": "解答题",
    "四、单选题": "单选题",
}

def convert_diff(d):
    """Convert 0-1 difficulty (lower=harder) to 1-5 scale."""
    if d >= 0.85: return 2.0
    if d >= 0.70: return 2.5
    if d >= 0.55: return 3.0
    if d >= 0.40: return 3.5
    if d >= 0.25: return 4.0
    return 4.5

imports = []
for q in questions:
    answer = q["answer"].strip().replace("\n", "; ")
    qtype = type_map.get(q["section"], "解答题")
    diff = convert_diff(float(q["difficulty"]) if q["difficulty"] else 0.65)
    
    entry = {
        "stem": q["stem"],
        "options": q["options"] or None,
        "answer": answer,
        "solution": q["solution"],
        "grade": 11,
        "question_type": qtype,
        "difficulty": diff,
        "source_name": "北京市东直门中学2025-2026学年高二下学期期末考试数学试题",
        "source_url": "https://zujuan.xkw.com/11p3365591.html",
        "source_year": "2026",
        "license": "User-Provided",
        "verified": 0
    }
    imports.append(entry)

# Verify
for i, e in enumerate(imports[:3]):
    print(f"Entry {i}: type={e['question_type']!r} diff={e['difficulty']}")

out_path = "data/imports/2026-07-18-dongzhimen/import.jsonl"
with open(out_path, "w", encoding="utf-8") as f:
    for entry in imports:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

print(f"Wrote {len(imports)} entries to {out_path}")
