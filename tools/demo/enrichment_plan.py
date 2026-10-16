"""Bounded fictional inputs for one additive family experience batch."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid5
from zoneinfo import ZoneInfo

from core.demo_records import DEMO_NAMESPACE, PATIENT_KEYS, _date_time, _field
from core.demo_visit_files import medical_date

BATCH_KEY = 'family-enrichment-20261012-v1'
BATCH_NAMESPACE = uuid5(DEMO_NAMESPACE, BATCH_KEY)
ZONE = ZoneInfo('Asia/Shanghai')
LAB_LABELS = {'白细胞计数': 'WBC', '血红蛋白': 'Hemoglobin', '血小板计数': 'Platelets',
    '铁蛋白': 'Ferritin', 'C反应蛋白': 'CRP', '红细胞沉降率': 'ESR',
    '丙氨酸氨基转移酶': 'ALT', '天门冬氨酸氨基转移酶': 'AST'}


def exact_time(day, hour, minute=0):
    # The ordinary backend resolves local input; no instant is forged here.
    local = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    return {'precision': 'minute', 'local_datetime': local.isoformat(),
        'timezone_name': 'Asia/Shanghai', 'utc_offset_minutes': 480,
        'raw_text': local.strftime('%Y-%m-%d %H:%M') + ' Asia/Shanghai',
        'resolution_status': 'local_only'}


def anchor_day(as_of):
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError('ENRICHMENT_AWARE_ANCHOR_REQUIRED')
    return as_of.astimezone(ZONE).date()


def record_plan(as_of):
    day = anchor_day(as_of)
    rows = []
    morning = {'child_a': ('36.8', '36.9', '37.1', '37.3', '37.5', '37.2', '37.0',
        '36.9', '37.0', '36.8', '36.7', '36.8', '36.9', '36.8'),
        'child_b': ('36.6', '36.7', '36.8', '36.7', '36.9', '36.8', '36.7',
        '36.6', '36.8', '36.7', '36.6', '36.7', '36.8', '36.7')}
    evening = {'child_a': ('37.0', '37.2', '37.5', '37.8', '38.0', '37.6', '37.3',
        '37.1', '37.2', '37.0', '36.9', '37.0', '37.1', '37.0'),
        'child_b': ('36.8', '36.9', '37.0', '36.9', '37.1', '37.0', '36.9',
        '36.8', '37.0', '36.9', '36.8', '36.9', '37.0', '36.9')}
    notes = ('早饭后在家休息，记录睡眠和活动情况。', '午后做了轻度活动，晚饭后整理当天记录。',
        '上午阅读和休息，食欲与前一天相近。', '傍晚短时间散步，回家后按原方法测量。',
        '当天主要在家活动，家属补充记录。', '整理近期观察表，准备下次复诊时携带。',
        '按日常安排记录；未补填没有观察到的其他症状。')
    for child in PATIENT_KEYS:
        for index in range(14):
            observed = day - timedelta(days=14 - index)
            for period, hour, values in (('morning', 8, morning), ('evening', 18, evening)):
                measured = exact_time(observed, hour, 30)
                rash = child == 'child_a' and index in (3, 4) and period == 'evening'
                joint = child == 'child_a' and index == 4
                fields = [
                    _field('temp', 'decimal', values[child][index], time=measured,
                        concept_code='temp', original_name='体温', raw_unit='C', qualifier='eq', method='腋下'),
                    _field('rash', 'categorical', 'yes' if rash else 'no', time=deepcopy(measured), original_name='皮疹'),
                    _field('joint_discomfort', 'categorical', 'yes' if joint else 'no', time=deepcopy(measured), original_name='关节不适'),
                    _field('observation_note', 'text', notes[(index + (period == 'evening')) % len(notes)], time=deepcopy(measured)),
                ]
                rows.append({'key': f'{child}/observation-{index + 1:02d}-{period}',
                    'patient_key': child, 'kind': 'observation', 'fields': fields, 'source_organization': None,
                    'recorded_at': datetime.fromisoformat(measured['local_datetime']).replace(tzinfo=ZONE).astimezone(timezone.utc) + timedelta(minutes=5)})
    # Reference ranges/flags below are fictional original-report content, not a
    # dictionary match, clinical normalization, risk calculation or advice.
    panels = {'child_a': [('6.8', '116', '212', '102', '6.4', '16', '21', '27'),
        ('7.9', '114', '198', '138', '10.2', '23', '25', '31'),
        ('7.1', '118', '224', '110', '5.6', '18', '22', '26')],
        'child_b': [('6.2', '121', '260', '62', '2.8', '10', '18', '24'),
        ('6.5', '120', '251', '68', '3.4', '12', '20', '25'),
        ('6.1', '123', '268', '59', '2.1', '9', '17', '23')]}
    definitions = [('白细胞计数', '10^9/L', '4–10'), ('血红蛋白', 'g/L', '110–150'),
        ('血小板计数', '10^9/L', '100–300'), ('铁蛋白', 'ng/mL', '15–150'),
        ('C反应蛋白', 'mg/L', '0–8'), ('红细胞沉降率', 'mm/h', '0–20'),
        ('丙氨酸氨基转移酶', 'U/L', '0–40'), ('天门冬氨酸氨基转移酶', 'U/L', '0–40')]
    for child, visits in panels.items():
        for index, values in enumerate(visits):
            sample = day - timedelta(days=(13, 8, 1)[index])
            fields = [_field('report_item', 'decimal', value, time=_date_time(sample), origin='item',
                original_name=name, raw_unit=unit, qualifier='eq', raw_text=value,
                reference_interval=f'{interval}（示例范围）',
                report_flag='↑' if child == 'child_a' and index == 1 and name in ('C反应蛋白', '红细胞沉降率') else None)
                for value, (name, unit, interval) in zip(values, definitions, strict=True)]
            rows.append({'key': f'{child}/report-{index + 1}', 'patient_key': child,
                'kind': 'report', 'fields': fields, 'source_organization': None,
                'recorded_at': datetime.combine(sample, datetime.min.time(), ZONE).replace(hour=15).astimezone(timezone.utc),
                'report_metadata': {'external_ref': f'示例-扩充-{child[-1].upper()}-{index + 1:02d}',
                    'report_status': 'final', 'sample_time': _date_time(sample), 'result_time': _date_time(sample)}})
    for child, labels in (('child_a', ('D', 'E')), ('child_b', ('F', 'G'))):
        for index, label in enumerate(labels):
            start = _date_time(day - timedelta(days=12 if index == 0 else 5))
            end = _date_time(day - timedelta(days=6 if index == 0 else 1))
            fields = [_field('medication_name', 'text', f'示例药物 {label}', origin='therapy', time=start),
                _field('therapy_status', 'categorical', 'ended', origin='therapy'),
                _field('therapy_start', 'time', origin='therapy', time=start),
                _field('therapy_end', 'time', origin='therapy', time=end),
                _field('therapy_source', 'text', '家庭药物使用备忘（示例）', origin='therapy'),
                _field('therapy_note', 'text', '家属整理的一段已结束的使用记录；虚构药物，不构成用药建议。', origin='therapy'),
                _field('dose', 'decimal', '1', raw_unit='示例单位', qualifier='eq', origin='therapy'),
                _field('dose_unit', 'text', '示例单位', origin='therapy'),
                _field('route_text', 'text', '原资料未注明', origin='therapy'),
                _field('frequency_text', 'text', '原资料未注明', origin='therapy')]
            rows.append({'key': f'{child}/therapy-{label.lower()}', 'patient_key': child,
                'kind': 'therapy', 'fields': fields, 'source_organization': None,
                'recorded_at': datetime.fromisoformat(end['local_date']).replace(tzinfo=ZONE, hour=19).astimezone(timezone.utc)})
    return rows


def report_rows(spec):
    result = []
    for field in spec['fields']:
        interval = field['reference_interval'].replace('（示例范围）', '').replace('–', '-')
        result.append((f'{LAB_LABELS[field["original_name"]]} (ref {interval})',
            field['raw_numeric'] + (' High' if field.get('report_flag') == '↑' else ''), field['raw_unit']))
    return result


def visit_plan(as_of):
    day = anchor_day(as_of)
    result = []
    for child in PATIENT_KEYS:
        name = {'child_a': '安安', 'child_b': '乐乐'}[child]
        for index, (offsets, title, descriptions) in enumerate((
                ((13, 8), '阶段复诊备忘', ('家属携带体温表和既往报告，记录本次门诊到访。', '家属再次整理近期观察与检验资料，补记复诊到访。')),
                ((5, 1), '近期就诊资料清单', ('为本次门诊准备观察记录和药物使用备忘。', '门诊后将检验报告与家庭观察资料归档。'))), 1):
            dates = [day - timedelta(days=offset) for offset in offsets]
            episodes = [{'kind': 'outpatient', 'start_time': medical_date(date), 'end_time': None,
                'description': description} for date, description in zip(dates, descriptions, strict=True)]
            lines = ['示例资料 · 家庭就诊备忘', f'{name}的{title}', '资料来源：家属补充；未经专业核对',
                '就诊地点：示例儿童医院', '', f'{dates[0].isoformat()} · 门诊', descriptions[0], '',
                f'{dates[1].isoformat()} · 门诊', descriptions[1], '', '虚构家庭整理示例，不是医院出具的病历。',
                '不包含诊断结论或用药建议。']
            result.append({'key': f'{child}-enrichment-visits-{index}', 'child_key': child,
                'as_of': day.isoformat(), 'lines': lines, 'confirmed_text': '\n'.join(lines),
                'proposed_episodes': episodes, 'organization': '示例儿童医院', 'documented_time': medical_date(dates[-1])})
    return result


def draft_plan(as_of):
    date = (anchor_day(as_of) - timedelta(days=1)).isoformat()
    empty = {'precision': 'unknown', 'value': '', 'timezone': ''}
    rows = []
    for child in PATIENT_KEYS:
        rows.append({'key': f'{child}/report-draft', 'patient_key': child, 'form_type': 'report',
            'payload': {'candidates': [{'name': '血红蛋白', 'value': None, 'unit': 'g/L',
                'comparator': '=', 'reference': '110–150（示例范围）', 'sampleTime': deepcopy(empty)}],
                'photos': [], 'organization': '', 'reportRef': '示例待补充报告',
                'sampleTime': {'precision': 'date', 'value': date, 'timezone': ''},
                'resultTime': deepcopy(empty), 'reportStatus': 'unknown'}})
        rows.append({'key': f'{child}/therapy-draft', 'patient_key': child, 'form_type': 'therapy',
            'payload': {'medication': '示例药物（待补充）', 'status': '无法确定',
                'note': '待核对家庭备忘中的药物名称、使用状态和起止日期。', 'itemDetails': {},
                'startTime': deepcopy(empty), 'endTime': deepcopy(empty), 'photos': [],
                'source': 'self_report', 'sourceReference': '', 'candidates': [], 'selectedSource': None}})
    return rows
