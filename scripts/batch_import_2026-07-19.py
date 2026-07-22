"""
Batch process 20 Beijing G11 docx exams through the approved OMML pipeline:
  extract_docx_omml.py → build_omml_exam_import.py

Usage:
  python scripts\batch_import_2026-07-19.py
"""
import subprocess
import sys
import os
import shutil
from pathlib import Path

PROJECT = Path(r"C:\Users\Administrator\Documents\Codex\2026-07-17\new-chat-5\math-error-notebook")
DOWNLOADS = Path(r"C:\Users\Administrator\Downloads")
BATCH = "2026-07-19-g11-beijing-20"
IMPORT_DIR = PROJECT / "data" / "imports" / BATCH
SKILL_SCRIPTS = PROJECT / ".agents" / "skills" / "math-error-notebook" / "scripts"

EXTRACT_SCRIPT = PROJECT / "scripts" / "extract_docx_omml.py"
BUILD_SCRIPT = PROJECT / "scripts" / "build_omml_exam_import.py"

# Map: (source_filename, short_dir_name, semester)
EXAMS = [
    ("北京十一学校2025-2026学年第四学段教与学质量诊断高二数学试题.docx", "01-bj-11xuexiao-s2-diagnosis", 2),
    ("北京市东直门中学2025-2026学年高二上学期十月月考数学试卷.docx", "02-dongzhimen-s1-oct", 1),
    ("北京市中国人民大学附属中学2025-2026学年高二上学期10月考数学试卷.docx", "03-rdfz-s1-oct", 1),
    ("北京市中国人民大学附属中学2025-2026学年高二上学期期中练习数学试题.docx", "04-rdfz-s1-mid", 1),
    ("北京市中国人民大学附属中学2025-2026学年高二下学期数学统练三.docx", "05-rdfz-s2-drill3", 2),
    ("北京市北京师范大学附属实验中学2025-2026学年高二上学期阶段测试一（10月月考）数学试题.docx", "06-bnu-experiment-s1-oct", 1),
    ("北京市清华大学附属中学朝阳学校2025-2026学年高二下学期期中数学试题.docx", "07-tsinghua-chaoyang-s2-mid", 2),
    ("北京市第一七一中学2025-2026学年高二上学期第一次（10月）月考数学试卷.docx", "08-171zhong-s1-oct", 1),
    ("北京市第二中学2025-2026学年高二上学期第一学段考试数学试卷.docx", "09-beijing-no2-s1-stage1", 1),
    ("北京市第二中学2025-2026学年高二下学期第六学段考试（期末）数学试卷.docx", "10-beijing-no2-s2-final", 2),
    ("北京市第二中学2025-2026学年高二第五学段考试数学试卷.docx", "11-beijing-no2-s2-stage5", 2),
    ("北京市第五中学2025-2026学年高二上学期第一次阶段检测数学试卷.docx", "12-beijing-no5-s1-oct", 1),
    ("北京市第八十中学2025-2026学年高二上学期11月期中考试数学试题.docx", "13-80zhong-s1-mid", 1),
    ("北京市第八十中学2025-2026学年高二下学期5月阶段检测数学试题.docx", "14-80zhong-s2-may", 2),
    ("北京市第四中学2025-2026学年高二下学期期中考试数学试卷.docx", "15-beijing-no4-s2-mid", 2),
    ("北京市陈经纶中学2025-2026学年高二上学期10月月考数学试卷.docx", "16-chenjinglun-s1-oct", 1),
    ("北京市陈经纶中学2025-2026学年高二下学期六月学习诊断数学试题.docx", "17-chenjinglun-s2-june", 2),
    ("北京市首都师范大学附属中学2025-2026学年第二学期6月诊断高二数学试题.docx", "18-shoushi-fuzhong-s2-june", 2),
    ("北京市首都师范大学附属中学2025-2026学年高二上学期期中练习数学试题.docx", "19-shoushi-fuzhong-s1-mid", 1),
    ("北京理工大学附中2025-2026学年高二上学期10月月考数学试题.docx", "20-beijing-ligong-s1-oct", 1),
]

TOTAL = len(EXAMS)

def run(cmd, cwd=None):
    """Run a command and return success."""
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  FAILED (exit {result.returncode})")
        if result.stderr:
            print(f"  stderr: {result.stderr[:500]}")
        return False
    # Print compact output
    out = result.stdout.strip()
    if out:
        print(f"  OK: {out[:300]}")
    return True

def main():
    os.chdir(str(PROJECT))
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)

    success_count = 0
    fail_list = []

    for idx, (filename, short_name, semester) in enumerate(EXAMS, 1):
        src = DOWNLOADS / filename
        exam_dir = IMPORT_DIR / short_name
        media_dir = exam_dir / "media"
        docx_dst = exam_dir / "exam.docx"

        print(f"\n{'='*60}")
        print(f"[{idx}/{TOTAL}] {short_name} (s{semester})")
        print(f"  Source: {filename[:60]}...")

        if not src.exists():
            print(f"  SKIP: source file not found")
            fail_list.append((short_name, "source not found"))
            continue

        # Step 1: Copy docx
        exam_dir.mkdir(parents=True, exist_ok=True)
        media_dir.mkdir(parents=True, exist_ok=True)

        if not docx_dst.exists():
            shutil.copy2(str(src), str(docx_dst))
            print(f"  Copied docx")

        # Step 2: extract_docx_omml.py
        json_path = exam_dir / "omml_extract.json"
        md_path = exam_dir / "omml_extract.md"

        if not json_path.exists():
            print(f"  Running extract_docx_omml.py...")
            cmd = [
                sys.executable, "-B",
                str(EXTRACT_SCRIPT),
                str(docx_dst),
                "--json", str(json_path),
                "--markdown", str(md_path),
                "--media-dir", str(media_dir),
            ]
            if not run(cmd):
                fail_list.append((short_name, "extract failed"))
                continue

        # Step 3: build_omml_exam_import.py
        questions_path = exam_dir / "questions.jsonl"
        summary_path = exam_dir / "parse_summary.json"

        if not questions_path.exists():
            print(f"  Running build_omml_exam_import.py...")
            cmd = [
                sys.executable, "-B",
                str(BUILD_SCRIPT),
                str(exam_dir),
                "--relative-dir", short_name,
                "--batch-name", BATCH,
                "--grade", "11",
                "--semester", str(semester),
                "--source-year", "2025-2026",
            ]
            if not run(cmd, cwd=PROJECT):
                fail_list.append((short_name, "build failed"))
                continue

        success_count += 1
        print(f"  DONE ({success_count}/{TOTAL} ok so far)")

    print(f"\n{'='*60}")
    print(f"SUMMARY: {success_count}/{TOTAL} succeeded")
    if fail_list:
        print(f"FAILURES:")
        for name, reason in fail_list:
            print(f"  - {name}: {reason}")
    else:
        print("All exams processed successfully!")

if __name__ == "__main__":
    main()
