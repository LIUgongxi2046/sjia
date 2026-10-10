import json
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader


root = Path(__file__).resolve().parents[1]
path = root / "design-specifications.pdf"
reader = PdfReader(path)
for page in reader.pages:
    fonts = [font.get_object() for font in page["/Resources"]["/Font"].values()]
    chinese = [font for font in fonts if "ArialUnicode" in str(font.get("/BaseFont"))]
    if not chinese or not all("/FontFile2" in font["/FontDescriptor"] for font in chinese):
        raise ValueError("中文字体缺少嵌入文件")
document = pdfium.PdfDocument(path)
rendered = []
for index in (0, len(reader.pages) // 2, len(reader.pages) - 1):
    picture = document[index].render(scale=1.5).to_pil()
    picture.save(root.parent / ".ui-work" / f"spec-pdf-{index + 1}.png")
    if picture.convert("L").getextrema()[0] > 200:
        raise ValueError("PDF 页面缺少可见内容")
    rendered.append(index + 1)
result = {"pages": len(reader.pages), "embedded_chinese_font": True, "rendered_pages": rendered}
(root / "checks/spec-pdf-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
