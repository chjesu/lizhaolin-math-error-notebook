"""Extract questions from Dongzhimen exam DOCX and output JSONL."""
import sys, io, re, json, os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

with open("data/imports/2026-07-18-dongzhimen/extracted.txt", "r", encoding="utf-8") as f:
    raw = f.read()

questions = []
current = None
section = ""
mode = "idle"

for line in raw.split("\n"):
    line = line.strip()
    if not line:
        continue

    m = re.match(r"^\[\d+\]\s*(.*)", line)
    if not m:
        continue
    text = m.group(1)
    text = text.replace("\xa0", " ")  # non-breaking space

    # Section headers
    if text in ("一、单选题", "二、填空题", "三、解答题", "四、单选题"):
        section = text
        continue

    # Skip header lines
    if "东直门中学" in text or text.startswith("学校:") or "考号" in text:
        continue

    # Question number: starts with digit(s) followed by ．
    qm = re.match(r"^(\d+)．(.+)", text)
    if qm:
        if current:
            questions.append(current)
        current = {
            "section": section,
            "number": int(qm.group(1)),
            "stem": qm.group(2),
            "options_str": "",
            "sub_parts": [],
            "answer": "",
            "difficulty": "",
            "knowledge": "",
            "solution": ""
        }
        mode = "stem"
        continue

    if not current:
        continue

    # Sub-question
    sm = re.match(r"^\((\d+)\)(.+)", text)
    if sm and mode == "stem":
        current["sub_parts"].append(f"({sm.group(1)}) {sm.group(2)}")
        continue

    # Option line
    om = re.match(r"^([A-D])．", text)
    if om and mode in ("stem", "options"):
        if current["options_str"]:
            current["options_str"] += " " + text
        else:
            current["options_str"] = text
        mode = "options"
        continue

    # Answer - multiple formats
    if "【答案】" in text:
        a = text.split("【答案】", 1)[-1].strip()
        a = a.lstrip("．").strip()
        current["answer"] = a
        mode = "answer"
        continue

    # Difficulty
    if "【难度】" in text:
        d = text.split("【难度】", 1)[-1].strip()
        d = d.lstrip("．").strip()
        try:
            current["difficulty"] = float(d)
        except:
            current["difficulty"] = d
        mode = "meta"
        continue

    # Knowledge
    if "【知识点】" in text:
        k = text.split("【知识点】", 1)[-1].strip()
        k = k.lstrip("．").strip()
        current["knowledge"] = k
        mode = "meta"
        continue

    # Solution
    if "【分析】" in text:
        current["solution"] += text.split("【分析】", 1)[-1].strip() + "\n"
        mode = "solution"
        continue
    if "【详解】" in text:
        current["solution"] += text.split("【详解】", 1)[-1].strip() + "\n"
        mode = "solution"
        continue

    # Continuation
    if mode == "stem":
        current["stem"] += text
    elif mode == "solution":
        current["solution"] += text + "\n"

if current:
    questions.append(current)

# Post-process
for q in questions:
    q["solution"] = q["solution"].strip()
    # Build full stem with sub-parts
    if q["sub_parts"]:
        q["stem"] = q["stem"] + "\n" + "\n".join(q["sub_parts"])
    # Parse options from the joined string
    if q["options_str"]:
        parts = re.split(r"(?=[A-D]．)", q["options_str"])
        q["options"] = [p.strip() for p in parts if p.strip()]
    else:
        q["options"] = []
    del q["options_str"]
    del q["sub_parts"]

# Stats
print(f"Total questions: {len(questions)}")
for q in questions:
    has_opt = "MC" if q["options"] else "FB" if "填空" in q.get("section", "") else "SA"
    print(f"  Q{q['number']:>2} [{q['section'][:4]}] {has_opt} | ans={q['answer'][:30]} | diff={q['difficulty']} | sol_len={len(q['solution'])}")

# Write JSONL
out_path = "data/imports/2026-07-18-dongzhimen/questions.jsonl"
with open(out_path, "w", encoding="utf-8") as f:
    for q in questions:
        f.write(json.dumps(q, ensure_ascii=False) + "\n")

print(f"\nSaved {len(questions)} questions to {out_path}")
