import json
from pathlib import Path

from PIL import Image
from pypdf import PdfReader
import pypdfium2 as pdfium
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


root = Path(__file__).resolve().parents[1] / "identity"
catalog = json.loads((root / "catalog.json").read_text())
pdfmetrics.registerFont(TTFont("IdentityChinese", "/Library/Fonts/Arial Unicode.ttf"))
document = canvas.Canvas(str(root / "brand-and-characters.pdf"), pagesize=(960,720))
document.setTitle("sJIA 守护者联盟 - 品牌与儿童形象")


def text(x, y, value, size=14, color="#17283d"):
    document.setFillColor(HexColor(color))
    document.setFont("IdentityChinese", size)
    document.drawString(x, y, value)


def background(title, page):
    document.setFillColor(HexColor("#f5f7fb"))
    document.rect(0,0,960,720,fill=1,stroke=0)
    text(48,668,title,24)
    text(48,30,"sJIA 守护者联盟  /  品牌与形象设计  /  2026.09",10,"#61768d")
    text(888,30,str(page),10,"#61768d")


def picture(path, x, y, width, height):
    with Image.open(path) as source:
        ratio = min(width/source.width,height/source.height)
        w, h = source.width*ratio, source.height*ratio
        document.drawImage(ImageReader(source),x+(width-w)/2,y+(height-h)/2,width=w,height=h,mask="auto")


background("陪伴成长，共同守护",1)
picture(root / "master/logo-embrace.png",530,235,340,340)
text(48,532,"sJIA 守护者联盟",32)
text(48,482,"两侧人物环抱中央儿童，表达家庭陪伴。",16)
text(48,452,"系统蓝色与青绿色形成统一品牌识别。",16)
text(48,364,"品牌图形",18)
text(48,330,"透明 PNG；图形与产品名称独立排版。")
text(48,298,"小尺寸应用保留清晰轮廓，建议至少 32 px。")
text(48,266,"深色界面配合白色图标容器使用。")
text(48,204,"17 个 Master / 34 个尺寸文件 / 16 套儿童形象",14)
document.showPage()
for page_number, season in enumerate(catalog["seasons"],2):
    background(season["label"]+"儿童形象",page_number)
    text(48,628,season["clothes"],14,"#61768d")
    items = [(age,gender) for age in catalog["ages"] for gender in catalog["genders"]]
    for index, (age,gender) in enumerate(items):
        x = 40+index*226
        document.setFillColor(HexColor("#ffffff"))
        document.roundRect(x,126,214,450,20,fill=1,stroke=0)
        asset_id = f'{age["id"]}-{gender["id"]}-{season["id"]}'
        picture(root / "master" / f"{asset_id}.png",x+12,205,190,342)
        text(x+20,176,age["label"]+" / "+gender["label"],14)
        text(x+20,150,age["pose"]+" / 正面形象",11,"#61768d")
    text(48,82,"同一角色的四季版本共享面容、姿态与材质。",12,"#61768d")
    document.showPage()
background("形象选择与资产使用",6)
lines = [
    ("年龄", "6-12 个月采用坐姿，1-3 岁采用站姿；各年龄独立制作身体比例。"),
    ("性别与外观", "男童与女童分类使用相同季节配色，保留监护人的形象选择。"),
    ("季节", "依据地区和家庭选择使用对应服装；不按月份强制切换。"),
    ("资料表达", "角色属于通用设计素材，身体颜色、表情与服装不表达病情。"),
    ("素材规格", "儿童导出 240 × 320 与 480 × 640 槽位尺寸，保持原始比例。"),
    ("使用范围", "交付正面图像与 PNG 品牌标志；不包含矢量源文件或三维模型。"),
]
for index, (title,detail) in enumerate(lines):
    y = 580-index*76
    text(48,y,title,17)
    text(48,y-29,detail,14,"#536b83")
document.save()
reader = PdfReader(root / "brand-and-characters.pdf")
if len(reader.pages) != 6:
    raise ValueError("PDF 页数不一致")
for page in reader.pages:
    fonts = [font.get_object() for font in page["/Resources"]["/Font"].values()]
    chinese = [font for font in fonts if "ArialUnicode" in str(font.get("/BaseFont"))]
    if not chinese or not all("/FontFile2" in font["/FontDescriptor"] for font in chinese):
        raise ValueError("中文字体未嵌入")
rendered = pdfium.PdfDocument(root / "brand-and-characters.pdf")
for index in (0,4,5):
    rendered[index].render(scale=1.3).to_pil().save(root.parent.parent / ".ui-work" / f"identity-pdf-{index+1}.png")
result = {"pages":6,"embedded_chinese_font":True,"rendered_pages":[1,5,6]}
(root / "checks/pdf-check.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps(result))
