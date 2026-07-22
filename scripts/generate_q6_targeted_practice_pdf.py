from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "q6_circle_center_targeted_practice.pdf"


def register_fonts() -> tuple[str, str]:
    regular = Path(r"C:\Windows\Fonts\msyh.ttc")
    bold = Path(r"C:\Windows\Fonts\msyhbd.ttc")
    try:
        if regular.is_file() and bold.is_file():
            pdfmetrics.registerFont(TTFont("PracticeCN", str(regular), subfontIndex=0))
            pdfmetrics.registerFont(TTFont("PracticeCN-Bold", str(bold), subfontIndex=0))
            return "PracticeCN", "PracticeCN-Bold"
    except Exception:
        pass
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light", "STSong-Light"


FONT, FONT_BOLD = register_fonts()


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(colors.HexColor("#667085"))
    canvas.drawString(18 * mm, 12 * mm, "高中数学错题本 · 第6题错因专项训练")
    canvas.drawRightString(192 * mm, 12 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


styles = getSampleStyleSheet()
title_style = ParagraphStyle(
    "TitleCN",
    parent=styles["Title"],
    fontName=FONT_BOLD,
    fontSize=19,
    leading=25,
    alignment=TA_CENTER,
    textColor=colors.HexColor("#173B57"),
    spaceAfter=8 * mm,
)
subtitle_style = ParagraphStyle(
    "SubtitleCN",
    parent=styles["Normal"],
    fontName=FONT,
    fontSize=10.5,
    leading=17,
    textColor=colors.HexColor("#475467"),
    spaceAfter=5 * mm,
)
question_style = ParagraphStyle(
    "QuestionCN",
    parent=styles["Normal"],
    fontName=FONT,
    fontSize=12,
    leading=20,
    textColor=colors.HexColor("#101828"),
    spaceAfter=3 * mm,
)
question_title_style = ParagraphStyle(
    "QuestionTitleCN",
    parent=styles["Heading2"],
    fontName=FONT_BOLD,
    fontSize=13,
    leading=20,
    textColor=colors.HexColor("#175CD3"),
    spaceBefore=4 * mm,
    spaceAfter=2 * mm,
)
source_style = ParagraphStyle(
    "SourceCN",
    parent=styles["Normal"],
    fontName=FONT,
    fontSize=8.5,
    leading=13,
    textColor=colors.HexColor("#667085"),
    spaceAfter=4 * mm,
)
answer_style = ParagraphStyle(
    "AnswerCN",
    parent=styles["Normal"],
    fontName=FONT,
    fontSize=11,
    leading=18,
    textColor=colors.HexColor("#101828"),
    spaceAfter=3 * mm,
)


def answer_space(lines: int):
    data = [[""] for _ in range(lines)]
    table = Table(data, colWidths=[174 * mm], rowHeights=[7 * mm] * lines)
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#D0D5DD")),
    ]))
    return table


def build_pdf() -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=19 * mm,
        title="第6题错因专项训练：垂直平分线与圆心轨迹",
        author="高中数学错题本",
    )
    story = [
        Paragraph("第6题错因专项训练", title_style),
        Paragraph("主题：弦的垂直平分线与圆心轨迹　　姓名：____________　日期：____________", subtitle_style),
        Paragraph(
            "训练目标：看到“圆经过两个点”时，立即想到圆心到两点距离相等，因此圆心在线段的垂直平分线上。先独立完成，再查看答案页。",
            subtitle_style,
        ),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#84ADFF"), spaceAfter=3 * mm),
        Paragraph("1　垂直平分线基础", question_title_style),
        Paragraph(
            "在平面直角坐标系中，点 A(1,2)，B(-2,-1)，点 C 在 x 轴上，直线 AC 的斜率为 -1/2。<br/>"
            "(1) 求直线 AC 的方程；<br/>(2) 求线段 AC 的垂直平分线与直线 AB 的交点 D 的坐标。",
            question_style,
        ),
        Paragraph("题库编号 Q-883c68040a31｜难度 3.0｜来源：北京师范大学附属中学2025-2026学年高二期中试题", source_style),
        answer_space(4),
        Paragraph("2　同构训练：两条圆心轨迹联立", question_title_style),
        Paragraph(
            "圆心在直线 3x+y=0 上，并且经过原点 O(0,0) 和点 A(-2,0)，求该圆的方程。",
            question_style,
        ),
        Paragraph("题库编号 Q-35f6a866d190｜难度 3.0｜来源：北京市第四中学2025-2026学年高二期中试题", source_style),
        answer_space(4),
        Paragraph("3　迁移训练：一般位置的弦", question_title_style),
        Paragraph(
            "已知圆 C 经过点 (2,5)、(0,3)，且圆心 C 在直线 x-y+1=0 上，求圆 C 的方程。",
            question_style,
        ),
        Paragraph("题库编号 Q-c67d2f6a9c4d（第1问）｜难度 3.0｜来源：北京市广渠门中学2025-2026学年高二期中试题", source_style),
        answer_space(4),
        PageBreak(),
        Paragraph("答案与解析", title_style),
        Paragraph("1　垂直平分线基础", question_title_style),
        Paragraph(
            "(1) 由点斜式：y-2=-1/2(x-1)，所以 <b>x+2y-5=0</b>。<br/>"
            "(2) 令 y=0 得 C(5,0)。AC 中点为 M(3,1)，AC 斜率为 -1/2，故垂直平分线斜率为 2：y-1=2(x-3)，即 y=2x-5。"
            "直线 AB 为 y=x+1，联立得 <b>D(6,7)</b>。",
            answer_style,
        ),
        Paragraph("2　同构训练", question_title_style),
        Paragraph(
            "O(0,0) 与 A(-2,0) 的中点为 (-1,0)，OA 水平，所以垂直平分线为 <b>x=-1</b>。"
            "与 3x+y=0 联立，得圆心 (-1,3)。半径平方为 1+9=10，故：<br/>"
            "<b>(x+1)<super>2</super>+(y-3)<super>2</super>=10</b>。",
            answer_style,
        ),
        Paragraph("3　迁移训练", question_title_style),
        Paragraph(
            "两点 (2,5)、(0,3) 的中点为 (1,4)，连线斜率为 1，因此垂直平分线为 y-4=-(x-1)，即 <b>x+y-5=0</b>。"
            "与 x-y+1=0 联立，得圆心 C(2,3)。半径为 2，故：<br/>"
            "<b>(x-2)<super>2</super>+(y-3)<super>2</super>=4</b>。",
            answer_style,
        ),
        Spacer(1, 8 * mm),
        HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D0D5DD"), spaceAfter=4 * mm),
        Paragraph(
            "本次纠错口诀：<b>圆过两点 → 两点连成弦 → 圆心在弦的垂直平分线上。</b><br/>"
            "切于一点 → 圆心与切点连线垂直于切线。两条圆心轨迹的交点才是圆心。",
            subtitle_style,
        ),
    ]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return OUTPUT


if __name__ == "__main__":
    print(build_pdf())
