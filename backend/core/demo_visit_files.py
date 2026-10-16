"""Exact, readable fictional family visit notes, never hospital/provider output."""
import io
import json
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from core.demo_experience import KEY, NAMESPACE, identity, require_test_context
from core.demo_files_summary import ensure_demo_image_attachment

FONT_PATH = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')


def medical_date(day):
    return {'raw_text': day.isoformat(), 'precision': 'date', 'local_year': None,
        'local_month': None, 'end_year': None, 'end_month': None, 'relative_text': None,
        'local_date': day.isoformat(), 'local_datetime': None, 'timezone_name': None,
        'utc_offset_minutes': None, 'instant': None, 'resolution_status': 'local_only'}


def demo_visit_note(*, child_key, as_of):
    if child_key not in ('child_a', 'child_b') or type(as_of) is not date:
        raise ValueError('DEMO_VISIT_INPUT_INVALID')
    child = {'child_a': '安安', 'child_b': '乐乐'}[child_key]
    days = [as_of - timedelta(days=10), as_of - timedelta(days=2)]
    descriptions = ['按既定安排门诊复诊，家属携带日常观察记录。',
        '再次门诊复诊，家属整理检验报告与近期观察情况。']
    episodes = [{'kind': 'outpatient', 'start_time': medical_date(day),
        'end_time': None, 'description': description}
        for day, description in zip(days, descriptions, strict=True)]
    lines = ['体验账号 · 示例资料', f'{child}的家庭就诊备忘',
        '资料类型：家庭补充说明；未经专业核对', '就诊地点：示例儿童医院', '',
        f'第一次就诊：{days[0].isoformat()} · 门诊', descriptions[0], '',
        f'第二次就诊：{days[1].isoformat()} · 门诊', descriptions[1], '',
        '本文为虚构的家庭整理示例，不是医院出具的病历。',
        '未使用自动识别，不包含诊断结论或用药建议。']
    return {'lines': lines, 'confirmed_text': '\n'.join(lines), 'proposed_episodes': episodes,
        'organization': '示例儿童医院', 'documented_time': medical_date(days[-1]),
        'child_key': child_key, 'as_of': as_of.isoformat()}


def render_demo_visit_png(note):
    lines = note.get('lines')
    if (not isinstance(lines, list) or not 1 <= len(lines) <= 20
            or any(not isinstance(line, str) or len(line) > 60 or '\n' in line for line in lines)
            or note.get('confirmed_text') != '\n'.join(lines)):
        raise ValueError('DEMO_VISIT_TEXT_INVALID')
    if not FONT_PATH.is_file():
        raise ValueError('DEMO_CHINESE_RENDER_FONT_REQUIRED')
    image = Image.new('RGB', (1280, 180 + 66 * len(lines)), '#f7f9fc')
    draw = ImageDraw.Draw(image)
    body = ImageFont.truetype(str(FONT_PATH), size=30, index=2)
    title = ImageFont.truetype(str(FONT_PATH), size=42, index=2)
    for index, line in enumerate(lines):
        draw.text((64, 64 + index * 66), line, font=title if index == 1 else body,
            fill='#163c50' if index < 4 else '#364756')
    metadata = PngImagePlugin.PngInfo()
    metadata.add_itxt('Description', '体验账号 · 示例资料。虚构家庭备忘；不是医疗文书。')
    metadata.add_itxt('demo-content', json.dumps({'schema_version': 'example-family-visit-1',
        'synthetic': True, 'clinical_document': False, 'ocr_performed': False,
        **note}, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    stream = io.BytesIO()
    image.save(stream, format='PNG', pnginfo=metadata)
    return stream.getvalue()


def ensure_demo_visit_attachment(*, actor, patient, child_key, as_of, source_revision,
        use_authorization, retention_policy, request_retention_policy):
    require_test_context(acknowledge_test_only=True)
    if (patient.id != identity(f'patient:{child_key}')
            or source_revision.id != identity('source-revision')
            or source_revision.source.source_key != KEY
            or source_revision.source.owner != KEY
            or use_authorization.id != identity(f'use:{child_key}')):
        raise ValueError('DEMO_VISIT_SCOPE_INVALID')
    note = demo_visit_note(child_key=child_key, as_of=as_of)
    group = ensure_demo_image_attachment(actor=actor, patient=patient, source_revision=source_revision,
        use_authorization=use_authorization, retention_policy=retention_policy,
        request_retention_policy=request_retention_policy, namespace=NAMESPACE,
        report_key=f'{child_key}-family-visits', content=render_demo_visit_png(note))
    return group, note
