import csv
import hashlib
import json
from html import escape
from pathlib import Path

from PIL import Image
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[1]
CATALOG = json.loads((PROJECT / '.ui-work/ui2-catalog.json').read_text())
SCREENS = CATALOG['screens']
STATES = CATALOG['states']


def write_csv(name, columns, rows):
    with (ROOT / name).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


mapping = []
assets = []
links = []
for screen in SCREENS:
    mapping.append({
        'source_id': screen['route'], 'screen_id': screen['id'], 'name': screen['name'],
        'source_type': 'INFERRED' if screen['group'] == '受控支持' else 'EXPLICIT',
        'requirement_refs': screen['requirements'].replace(' ', ';'), 'status': 'CREATED',
        'required_states': ';'.join(screen['states']), 'artifact_path': 'index.html',
        'notes': f"?screen={screen['key']}；{screen['note']}",
    })
write_csv('screen-design-map.csv', list(mapping[0]), mapping)

paths = [
    ('child-master', '../assets/master/child-neutral-front.png', '儿童形象 Master'),
    ('child-1x', '../assets/1x/child-neutral-front@1x.png', '儿童形象 1x'),
    ('child-2x', '../assets/2x/child-neutral-front@2x.png', '首页儿童形象 2x'),
    ('logo-master', '../identity/master/logo-embrace.png', '品牌图形 Master'),
    ('logo-1x', '../identity/1x/logo-embrace@1x.png', '品牌图形 1x'),
    ('logo-2x', '../identity/2x/logo-embrace@2x.png', '品牌图形 2x'),
    ('clinical-icons', '../assets/master/clinical-3d-icons.png', '家庭任务三维图标'),
    ('utility-icons', '../assets/master/utility-3d-icons.png', '查看与历史三维图标'),
    ('extended-icons', '../assets/master/extended-3d-icons.png', '辅助状态三维图标'),
]
for asset_id, relative, usage in paths:
    path = ROOT / relative
    with Image.open(path) as picture:
        width, height = picture.size
        image_format = picture.format
        transparency = 'A' in picture.getbands()
        picture.verify()
    assets.append({
        'asset_id': asset_id, 'file_path': relative, 'usage': usage, 'width': width, 'height': height,
        'format': image_format, 'transparency': str(transparency).lower(),
        'source': '既有 ui-delivery 品牌与三维资产；沿用原始生成记录',
        'license_or_restriction': '项目设计资产；通用形象不代表真实患儿；保持原比例',
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
    })
write_csv('asset-manifest.csv', list(assets[0]), assets)
for screen in SCREENS:
    for asset_id in ['clinical-icons', 'utility-icons', 'extended-icons']:
        links.append({'screen_id': screen['id'], 'asset_id': asset_id, 'usage': '共享页面与状态组件', 'required': 'true'})
    if screen['key'] == 'home':
        links.append({'screen_id': screen['id'], 'asset_id': 'child-2x', 'usage': '通用儿童形象', 'required': 'true'})
    if screen['key'] in ['home', 'login', 'account']:
        links.append({'screen_id': screen['id'], 'asset_id': 'logo-2x', 'usage': '品牌或独立评审区域', 'required': 'true'})
write_csv('page-asset-map.csv', list(links[0]), links)

pdfmetrics.registerFont(TTFont('ChineseUI', '/Library/Fonts/Arial Unicode.ttf'))
base = ParagraphStyle('Body', fontName='ChineseUI', fontSize=10, leading=17, wordWrap='CJK', textColor=colors.HexColor('#334D69'), spaceAfter=10)
heading = ParagraphStyle('Heading', parent=base, fontSize=21, leading=30, spaceBefore=17, spaceAfter=18, keepWithNext=True, textColor=colors.HexColor('#173F70'))
subheading = ParagraphStyle('Subheading', parent=base, fontSize=13, leading=21, spaceBefore=14, spaceAfter=9, keepWithNext=True)
small = ParagraphStyle('Small', parent=base, fontSize=8, leading=13)


def para(text, style=base):
    return Paragraph(escape(text), style)


def table(rows):
    content = [[para(value, small) for value in row] for row in rows]
    result = Table(content, colWidths=[145, 340], repeatRows=1, hAlign='LEFT')
    result.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E7EFF8')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F6F8FB')]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 9), ('RIGHTPADDING', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    return result


story = [Spacer(1, 50), para('sJIA 守护者联盟', heading), para('高保真 UI 2.0 设计规范', heading), para('对应 PRD V2.0.0 | 2026-10-07'), Spacer(1, 20),
         para('39 个家庭与受控支持页面。深蓝顶部、三维图标、通用儿童插画和三项导航。页面稿采用可点击浏览器设计，需求、状态和来源明确关联。'),
         para('本文件为设计规范；病例资料保持未提供或未知，没有虚构姓名、检查结果或实际通知。状态切换用于查看组件样式，不构成真实业务结果。'),
         para('本地入口：http://127.0.0.1:5188/?screen=home'),
         para('当前未执行截图、图像识别和视觉比对。构建、清单与浏览器 DOM 检查见 ui-audit.md。'), PageBreak(), para('设计系统与组件', heading)]
story.append(table([
    ['项目', '规格'], ['品牌与底色', '顶部 #173F70；主要操作 #2369AE；内容背景 #F1F5FA；卡片白色；摘要采用温暖纸面。'],
    ['字体与尺寸', '系统中文字体；页面标题 23 px，正文与输入 16 px，辅助文字独立分级；内容自然换行。'],
    ['间距与组件', '4 px 基础尺度；主要内边距 18-20 px；卡片圆角 20 px；主要按钮至少 52 px；主要触控目标至少 44 px。'],
    ['响应式', '390 x 844 CSS px 基准；320、360、390、430、580 px；主体自然滚动；主导航独立占位。'],
    ['键盘与动效', '清晰焦点；弹层 Escape 关闭、焦点返回；支持减少动态设置。'],
    ['原件与字段', '数值、比较符、单位、医学时间、来源各自确认；未知时间不填当前时刻；本人确认不授予专业资格。'],
    ['资产复用', '儿童和 Logo 使用既有 Master、1x、2x；三个透明图标表来自同一设计体系；辅助功能使用 lucide-react。'],
    ['操作状态', '默认、焦点、按下、禁用、等待、错误与完成分别表达；设计反馈说明未执行真实保存。'],
    ['隐私与存储', '本次不连接业务 API，不上传原件或发送通知，不写入浏览器持久资料；正式草稿使用服务端确认。'],
]))
story.extend([PageBreak(), para('状态与内容要求', heading)])
story.append(table([['状态', '设计文字与限制']] + [[label, text or '使用该页面的原始资料未知状态，不填入虚构病例。'] for _, label, text in STATES]))
story.append(para('临床三级配色仅定义设计表达，触发阈值、等级、准确文字及家庭可见范围仍须相应批准。未取得真实数据的页面呈现空内容、待选择或待确认。'))
for group in dict.fromkeys(screen['group'] for screen in SCREENS):
    story.extend([PageBreak(), para(group, heading)])
    for screen in SCREENS:
        if screen['group'] != group:
            continue
        story.extend([para(f"{screen['id']}  {screen['name']}", subheading),
                      para(screen['note']), para(f"需求：{screen['requirements']} | 路由：{screen['route']}", small),
                      para('适用状态：' + '、'.join(next(item[1] for item in STATES if item[0] == state) for state in screen['states']), small),
                      para(f"页面入口：?screen={screen['key']}；实际数据、核对和发布状态由后续服务接入。", small)])


def footer(canvas, document):
    canvas.setFont('ChineseUI', 8)
    canvas.setFillColor(colors.HexColor('#8494A6'))
    canvas.drawString(54, 30, 'sJIA 守护者联盟 · UI 2.0 · DESIGN')
    canvas.drawRightString(A4[0] - 54, 30, str(document.page))


pdf_path = ROOT / 'design-specifications.pdf'
SimpleDocTemplate(str(pdf_path), pagesize=A4, leftMargin=54, rightMargin=54, topMargin=42, bottomMargin=52, title='sJIA 守护者联盟 UI 2.0 设计规范', author='产品设计').build(story, onFirstPage=footer, onLaterPages=footer)
reader = PdfReader(pdf_path)
pdf_text = '\n'.join(page.extract_text() for page in reader.pages)
if not all(screen['name'] in pdf_text for screen in SCREENS):
    raise ValueError('PDF 页面目录不完整')
result = {'screens': len(SCREENS), 'statePresentations': sum(len(screen['states']) for screen in SCREENS), 'assets': len(assets), 'pdfPages': len(reader.pages), 'pdfAllScreenNames': True, 'visualVerified': False}
(ROOT / 'checks/export-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False))
