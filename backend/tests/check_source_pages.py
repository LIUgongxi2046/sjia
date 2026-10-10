import io
import json
import sys
from pathlib import Path

import pymupdf
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from files.errors import FileRejected
from files.source_pages import document_bytes, inspect_source, render_source_page

limits = {'max_pages': 100, 'max_pixels': 40000000}
checks = []


def check(name, condition):
    assert condition, name
    checks.append(name)
    print('PASS ' + name, flush=True)


text = '\n'.join(f'SIMULATION资料第{number:03d}项：铁蛋白 {1800 + number}.00 ng/mL；采样 2026-09-01 08:30。' for number in range(100))
raw = text.encode('utf-8')
rows = inspect_source(raw, 'text/plain', limits)
check('中文长文本具有连续准确原页', len(rows) > 1 and [row['number'] for row in rows] == list(range(1, len(rows) + 1)))
with pymupdf.open(stream=document_bytes(raw, 'text/plain', 100), filetype='pdf') as document:
    extracted = ''.join(page.get_text() for page in document)
    check('全部模拟数值及中文内容保留', all(f'{1800 + number}.00' in extracted for number in range(100)) and '铁蛋白' in extracted)
    pdf = document.tobytes()
    encrypted = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw='simulation-password', owner_pw='simulation-owner')
pdf_rows = inspect_source(pdf, 'application/pdf', limits)
check('PDF原页尺寸及页数准确', pdf_rows == rows)
for row in rows:
    image_bytes = render_source_page(raw, 'text/plain', row['number'], limits)
    with Image.open(io.BytesIO(image_bytes)) as image:
        check('文本原页' + str(row['number']) + '可以按准确尺寸处理', image.size == (row['width'], row['height']))
try:
    inspect_source(encrypted, 'application/pdf', limits)
except FileRejected as failure:
    check('加密PDF明确拒绝', failure.reason_code == 'PDF_CONTENT_INVALID')
else:
    raise AssertionError('加密PDF未拒绝')
try:
    inspect_source(raw, 'text/plain', {'max_pages': 1, 'max_pixels': 40000000})
except FileRejected as failure:
    check('文本超限明确拒绝并保留全部原输入', failure.reason_code == 'FILE_PAGE_LIMIT_EXCEEDED' and raw == text.encode('utf-8'))
else:
    raise AssertionError('超限文本未拒绝')
(ROOT / '.planning-work/t40-source-pages.json').write_text(json.dumps({'mode': 'SIMULATION', 'status': 'PASS',
    'checks': checks, 'text_page_count': len(rows), 'visual_comparison': 'NOT_RUN'}, ensure_ascii=False, indent=2))
