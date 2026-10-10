import csv
import json
from pathlib import Path

from PIL import Image
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
import pypdfium2 as pdfium


root = Path(__file__).resolve().parents[1]
pdfmetrics.registerFont(TTFont("BoardChinese", "/Library/Fonts/Arial Unicode.ttf"))
with (root / "route-design-map.csv").open(newline="", encoding="utf-8") as stream:
    entries = [row for row in csv.DictReader(stream) if row["screen_id"] not in {"S00", "TW-PANEL"}]
boards = [(row["screen_id"] + " " + row["name"], root / row["artifact_path"]) for row in entries]
boards.extend((name, root / "screens/png" / filename) for name, filename in [
    ("儿童资料展开面板", "TW-PANEL.png"),
    ("深色首页", "S03-dark.png"),
    ("报告确认页面", "S05-confirm.png"),
    ("治疗确认页面", "S06-confirm.png"),
    ("报告拍照引导", "S05-camera.png"),
    ("治疗拍照引导", "S06-camera.png"),
    ("症状语音文字核对", "S04-voice-review.png"),
    ("症状语音权限提示", "S04-voice-permission.png"),
])
boards.extend((path.stem + " 状态组件设计", path) for path in sorted((root / "states/png").glob("*.png")))
boards.extend((path.stem + " 预警情景设计", path) for path in sorted((root / "alerts/png").glob("*.png")))
output = root / "high-fidelity-boards.pdf"
document = canvas.Canvas(str(output), pagesize=(595.28, 841.89))
document.setTitle("sJIA 儿童数字孪生高保真图稿")
for index, (name, path) in enumerate(boards, 1):
    document.setFillColorRGB(0.94, 0.96, 0.98)
    document.rect(0, 0, 595.28, 841.89, fill=1, stroke=0)
    document.setFillColorRGB(0.1, 0.2, 0.33)
    document.setFont("BoardChinese", 14)
    document.drawString(32, 809, name)
    with Image.open(path) as picture:
        scale = min(495 / picture.width, 722 / picture.height)
        width, height = picture.width * scale, picture.height * scale
        document.drawImage(ImageReader(picture), (595.28 - width) / 2, 64 + (722 - height) / 2, width=width, height=height, mask="auto")
    document.setFont("BoardChinese", 8)
    document.setFillColorRGB(0.38, 0.44, 0.52)
    document.drawString(32, 25, "UI 3.0 · 登录后设计图稿 · 内容为设计示例")
    document.drawRightString(563, 25, str(index))
    document.showPage()
document.save()
reader = PdfReader(output)
if len(reader.pages) != len(boards):
    raise ValueError("PDF 图稿数量不一致")
for page in reader.pages:
    if not list(page.images):
        raise ValueError("图稿页面缺少图片")
    fonts = page["/Resources"]["/Font"]
    chinese_fonts = [font.get_object() for font in fonts.values() if "ArialUnicode" in str(font.get_object().get("/BaseFont"))]
    if not chinese_fonts or not all("/FontFile2" in font["/FontDescriptor"] for font in chinese_fonts):
        raise ValueError("中文字体没有完整嵌入")
checks = {"pages":len(boards),"embedded_chinese_font":True,"image_on_every_page":True}
(root / "checks/board-pdf-check.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
document = pdfium.PdfDocument(output)
for index in (0, 11, len(boards) - 1):
    image = document[index].render(scale=1.5).to_pil()
    image.save(root.parent / ".ui-work" / f"board-pdf-{index + 1}.png")
print(json.dumps(checks, ensure_ascii=False))
