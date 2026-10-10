import csv
import hashlib
import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from markdown_it import MarkdownIt
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle


ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    ROOT / "design-system.md",
    ROOT / "screens/page-specifications.md",
    ROOT / "states/state-specifications.md",
    ROOT / "alerts/specifications.md",
    ROOT / "assets/asset-specifications.md",
    ROOT / "assumptions-and-open-questions.md",
    ROOT / "requirements-review.md",
    ROOT / "ai-assistance-review.md",
]
PARSER = MarkdownIt("commonmark").enable("table")
FONT = "ArialUnicodeEmbedded"
pdfmetrics.registerFont(TTFont(FONT, "/Library/Fonts/Arial Unicode.ttf"))
BODY = ParagraphStyle("Body", fontName=FONT, fontSize=10, leading=16, wordWrap="CJK", spaceAfter=8, textColor=colors.HexColor("#1C1C1E"))
HEADING = {
    "h1": ParagraphStyle("H1", parent=BODY, fontSize=22, leading=30, spaceAfter=18, textColor=colors.HexColor("#0060CE"), keepWithNext=True),
    "h2": ParagraphStyle("H2", parent=BODY, fontSize=15, leading=22, spaceBefore=14, spaceAfter=8, keepWithNext=True),
    "h3": ParagraphStyle("H3", parent=BODY, fontSize=12, leading=18, spaceBefore=10, spaceAfter=6, keepWithNext=True),
}
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=8.5, leading=13, spaceAfter=0)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inline_text(token):
    require(token.children is not None, "Markdown 内联内容缺少解析结果")
    return "".join(" " if child.type in {"softbreak", "hardbreak"} else child.content for child in token.children)


def make_table(rows):
    require(bool(rows), "表格不能为空")
    count = len(rows[0])
    require(all(len(row) == count for row in rows), "表格列数不一致")
    table = Table(rows, colWidths=[499 / count] * count, repeatRows=1, hAlign=TA_LEFT)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9F2FF")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F8FA")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#D1D1D6")),
    ]))
    return table


def add_markdown(path, story):
    tokens = PARSER.parse(path.read_text(encoding="utf-8"))
    heading = None
    rows = None
    row = None
    heading_texts = []
    for token in tokens:
        if token.type == "heading_open":
            heading = token.tag
        elif token.type == "heading_close":
            heading = None
        elif token.type == "table_open":
            rows = []
        elif token.type == "tr_open":
            row = []
        elif token.type == "tr_close":
            require(rows is not None and row is not None, "表格状态缺失")
            rows.append(row)
            row = None
        elif token.type == "table_close":
            require(rows is not None, "表格内容缺失")
            story.extend([make_table(rows), Spacer(1, 12)])
            rows = None
        elif token.type == "inline":
            content = inline_text(token)
            if row is not None:
                row.append(Paragraph(escape(content), CELL))
            elif heading is not None:
                require(heading in HEADING, "标题级别尚未定义")
                heading_texts.append(content)
                story.append(Paragraph(escape(content), HEADING[heading]))
            else:
                story.append(Paragraph(escape(content), BODY))
        elif token.type in {"fence", "code_block", "html_block"}:
            raise ValueError("当前 PDF 来源包含未定义的内容类型")
    return heading_texts


def footer(canvas, document):
    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(colors.HexColor("#636366"))
    canvas.drawString(48, 28, "sJIA UI 3.0 · 设计规格 · 页面内容为设计示例")
    canvas.drawRightString(A4[0] - 48, 28, str(document.page))
    canvas.restoreState()


def luminance(value):
    components = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [component / 12.92 if component <= 0.04045 else ((component + 0.055) / 1.055) ** 2.4 for component in components]
    return sum(component * weight for component, weight in zip(linear, [0.2126, 0.7152, 0.0722]))


def contrast_checks(tokens):
    checks = []
    for theme in ("light", "dark"):
        values = tokens["color"][theme]
        pairs = [
            ("正文", values["text"]["primary"], values["surface"]["raised"]),
            ("辅助文字", values["text"]["secondary"], values["surface"]["raised"]),
            ("操作文字", values["action"]["text"], values["surface"]["raised"]),
            ("按钮文字", values["text"]["inverse"], values["action"]["primary"]),
            ("待处理文字", values["feedback"]["warning"], values["feedback"]["warningSurface"]),
            ("错误文字", values["feedback"]["error"], values["surface"]["raised"]),
            ("完成文字", values["feedback"]["success"], values["surface"]["raised"]),
        ]
        for name, foreground, background in pairs:
            light, dark = sorted([luminance(foreground), luminance(background)], reverse=True)
            ratio = (light + 0.05) / (dark + 0.05)
            require(ratio >= 4.5, f"文字对比度不足：{theme} {name} {ratio:.2f}")
            checks.append({"theme": theme, "name": name, "ratio": round(ratio, 3)})
    for name, values in tokens["alertDesign"].items():
        light, dark = sorted([luminance(values["text"]), luminance(values["background"])], reverse=True)
        ratio = (light + 0.05) / (dark + 0.05)
        require(ratio >= 4.5, f"预警文字对比度不足：{name}")
        checks.append({"theme":"alertDesign","name":name,"ratio":round(ratio,3)})
    return checks


def main():
    tokens = json.loads((ROOT / "tokens.json").read_text(encoding="utf-8"))
    contrasts = contrast_checks(tokens)
    with (ROOT / "route-design-map.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    require(len(rows) == 25, "业务页面、评审目录及新增面板数量不一致")
    story = []
    headings = []
    for index, source in enumerate(SOURCES):
        if index:
            story.append(PageBreak())
        headings.extend(add_markdown(source, story))
    output = ROOT / "design-specifications.pdf"
    document = SimpleDocTemplate(str(output), pagesize=A4, leftMargin=48, rightMargin=48, topMargin=44, bottomMargin=48, title="儿童数字孪生 UI 设计规格", author="sJIA 守护者联盟", allowSplitting=1)
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    reader = PdfReader(output)
    texts = [page.extract_text() for page in reader.pages]
    require(all(text.strip() for text in texts), "PDF 存在无法读取文字的页面")
    compact = "".join("".join(text.split()) for text in texts)
    for heading in headings:
        require("".join(heading.split()) in compact, f"PDF 缺少标题：{heading}")
    for identifier in [f"ST{index:02}" for index in range(1, 19)] + [f"PH{index:02}" for index in range(1, 7)] + [f"TW{index:02}" for index in range(1, 9)]:
        require(identifier in compact, f"PDF 缺少状态：{identifier}")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "capability": "DESIGN",
        "evidence_kind": "STRUCTURE",
        "business_screen_specifications": 23,
        "additional_review_screen": 1,
        "additional_twin_panel": 1,
        "state_specifications": 32,
        "pdf_pages": len(reader.pages),
        "pdf_heading_checks": len(headings),
        "contrast_checks": contrasts,
        "visual_verified": False,
        "business_verified": False,
        "source_sha256": {str(source.relative_to(ROOT)): hashlib.sha256(source.read_bytes()).hexdigest() for source in SOURCES},
        "pdf_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    evidence = ROOT.parent / ".ui-work/design-check.json"
    evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
