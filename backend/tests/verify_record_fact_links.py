import json
from datetime import datetime, timezone

from tests.verify_v2_migration import ROOT, require
from django.db import connection, transaction
from archives.models import ArchiveFact, ArchiveFactVersion
from records.models import Record, RecordVersion, RecordField
from records.service import read_version
from archives.record_facts import initial_fact_content, correction_fact_content, create_record_facts, correct_record_facts
from jsonschema import Draft202012Validator, FormatChecker
from configurations.service import compute_checksum
from django.core.serializers.json import DjangoJSONEncoder
from records.queries import QueryRejected
from unittest import TestCase


checked_versions, checked_fields, correction_fields, write_guard_checks = 0, 0, 0, 0
schema = json.loads((ROOT / 'backend/records/search.schema.json').read_text())
Draft202012Validator.check_schema(schema)
validator = Draft202012Validator({**schema, '$ref': '#/$defs/RecordView'}, format_checker=FormatChecker())


def fact_snapshot_checksum():
    values = list(ArchiveFactVersion.objects.order_by('id').values(
        'id', 'fact_id', 'version', 'record_version_id', 'field_id', 'medical_time_id', 'available_at'))
    return compute_checksum(json.loads(json.dumps(values, cls=DjangoJSONEncoder)))


with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    before = fact_snapshot_checksum()
    for version in RecordVersion.objects.select_related('patient').order_by('id'):
        record = Record.objects.get(id=version.record_id, patient=version.patient)
        result = read_version(version, record)
        require('fact_refs' in result, '记录详情没有返回可追溯的逐项事实版本引用。')
        validator.validate(result)
        fields = {field.id: field for field in RecordField.objects.filter(record_version_id=version.id, patient=version.patient)}
        require(len(result['fact_refs']) == len(fields), '记录事实引用未覆盖全部准确字段。')
        seen = set()
        for reference in result['fact_refs']:
            saved = ArchiveFactVersion.objects.get(id=reference['version_id'], fact_id=reference['fact_id'],
                patient_id=version.patient_id, record_version_id=version.id, field_id=reference['field_id'])
            require(reference['version'] == str(saved.version), '记录事实引用的版本不一致。')
            require(str(saved.field_id) not in seen, '记录事实引用存在重复字段。')
            seen.add(str(saved.field_id))
            field = fields[saved.field_id]
            require(saved.medical_time_id == field.medical_time_id and saved.source_anchor_id == field.anchor_id
                and saved.confirmation_id == field.confirmation_id, '记录事实引用改变了医学时间、来源或本人确认。')
            ArchiveFact.objects.get(id=saved.fact_id, patient_id=version.patient_id)
            content = initial_fact_content(field, version)
            require(content['field_id'] == field.id and content['record_version_id'] == version.id
                and content['medical_time_id'] == field.medical_time_id and content['confirmation_id'] == field.confirmation_id,
                '事实创建内容没有使用准确保存字段。')
            require(content['context_type'] == content['assertion_state'] == 'unknown'
                and content['episode_id'] is None and content['documented_time_id'] is None,
                '事实创建内容推断了未提供的语境或文书时间。')
            require(content['time_origin'] == (field.sample_time_origin if field.sample_time_origin in ('item', 'observation', 'therapy') else 'unknown'),
                '事实时间来源没有保留准确字段输入，或使用了未经逐项确认的共同时间。')
            if version.supersedes_version is not None:
                previous = RecordVersion.objects.get(record_id=version.record_id, patient=version.patient,
                    version=version.supersedes_version)
                previous_field = RecordField.objects.get(record_version_id=previous.id, patient=version.patient, ordinal=field.ordinal)
                previous_fact = ArchiveFactVersion.objects.get(field_id=previous_field.id, patient_id=version.patient_id)
                content = correction_fact_content(field, version, previous_field, previous_fact)
                require(content['field_id'] == field.id and content['record_version_id'] == version.id
                    and content['medical_time_id'] == field.medical_time_id and content['confirmation_id'] == field.confirmation_id,
                    '更正事实内容没有使用准确新字段。')
                require(content['source_anchor_id'] == previous_fact.source_anchor_id
                    and content['document_version_id'] == previous_fact.document_version_id
                    and content['documented_time_id'] == previous_fact.documented_time_id,
                    '更正事实内容改变了原有出处或文书时间。')
                correction_fields += 1
            checked_fields += 1
        checked_versions += 1
    after = fact_snapshot_checksum()
    require(before == after, '事实引用检查改变了原有历史内容。')
with transaction.atomic():
    before = fact_snapshot_checksum()
    for record in Record.objects.filter(status='active').order_by('id'):
        version = RecordVersion.objects.get(record_id=record.id, patient_id=record.patient_id, version=record.current_version)
        with TestCase().assertRaises(QueryRejected) as failure:
            create_record_facts(version)
        require(failure.exception.reason_code == 'FACT_INITIAL_VERSION_INVALID', '既有版本重复创建事实没有准确拒绝。')
        write_guard_checks += 1
        if version.supersedes_version is not None:
            previous = RecordVersion.objects.get(record_id=record.id, patient_id=record.patient_id, version=version.supersedes_version)
            with TestCase().assertRaises(QueryRejected) as failure:
                correct_record_facts(version, previous)
            require(failure.exception.reason_code == 'FACT_CORRECTION_VERSION_INVALID', '已经提交的更正没有准确拒绝重新采用。')
            write_guard_checks += 1
    require(before == fact_snapshot_checksum(), '写入拒绝检查改变了原有事实版本。')
report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'PASS',
    'scope': 'CURRENT_RECORD_FACT_REFERENCES_READ_ONLY', 'record_versions': checked_versions,
    'field_references': checked_fields, 'existing_correction_field_contents': correction_fields,
    'projection_transaction_read_only': True, 'write_guard_checks': write_guard_checks, 'guard_business_writes': 0,
    'new_records_created': False,
    'authenticated_creation_and_correction': 'NOT_RUN', 'clinical_acceptance': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
(ROOT / '.planning-work/t41-record-fact-links.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False))
