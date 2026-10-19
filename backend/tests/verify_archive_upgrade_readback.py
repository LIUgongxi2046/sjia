import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import unittest

import django
from jsonschema import Draft202012Validator, FormatChecker

django.setup()

from archives.inputs import PROTOCOL, REGISTRY
from archives.models import ArchiveFact, ArchiveFactVersion
from archives.queries import current_fact_versions, fact_read, fact_sources, fact_versions
from authorization.policy import matching_case_grants
from case_details.evaluation_inventory import SourceManifest
from configurations.evidence import backend_artifact_manifest
from configurations.service import compute_checksum
from django.db import connection, transaction
from files.models import FileObject, FilePage
from files.http_content import verified_file_response
from identity.models import Patient, Principal
from medical_time.models import MedicalTime
from recognition.models import SourceAnchor
from records.models import Record, RecordField, RecordVersion
from records.queries import QueryRejected, record_history_read, record_version_read, source_resolve
from records.service import read_record
from retention.content_fences import content_reader
from verification.models import Verification


parser = argparse.ArgumentParser(description='在准确升级副本重新读取记录、事实、历史及未知时间')
parser.add_argument('--upgrade-report', required=True, type=Path)
parser.add_argument('--upgrade-report-sha256', required=True)
parser.add_argument('--source-manifest', required=True, type=Path)
parser.add_argument('--source-manifest-sha256', required=True)
parser.add_argument('--expected-facts', required=True, type=int)
parser.add_argument('--expected-versions', required=True, type=int)
parser.add_argument('--output', required=True, type=Path)
arguments = parser.parse_args()
os.umask(0o077)


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def exact_json(path, checksum):
    content = path.read_bytes()
    require(hashlib.sha256(content).hexdigest() == checksum, 'ARCHIVE_READBACK_EXACT_INPUT_REQUIRED')
    return json.loads(content)


def time_content(identity, patient):
    if identity is None:
        return None
    value = MedicalTime.objects.get(id=identity, patient=patient)
    result = {name: getattr(value, name) for name in (
        'precision', 'raw_text', 'timezone_name', 'resolution_status', 'local_year', 'local_month',
        'end_year', 'end_month', 'relative_text', 'utc_offset_minutes')}
    for name in ('local_date', 'local_datetime', 'instant'):
        item = getattr(value, name)
        result[name] = item.isoformat() if item is not None else None
    return result


require(not arguments.output.exists() and arguments.expected_facts > 0
    and arguments.expected_versions >= arguments.expected_facts, 'ARCHIVE_READBACK_EXACT_OUTPUT_REQUIRED')
upgrade = exact_json(arguments.upgrade_report, arguments.upgrade_report_sha256)
source = SourceManifest.model_validate(exact_json(arguments.source_manifest, arguments.source_manifest_sha256))
require(upgrade['result'] == 'PASS' and upgrade['phase'] == 'migrated' and upgrade['read_only']
    and source.original_attachment_comparison == 'PASS', 'ARCHIVE_READBACK_APPROVED_UPGRADE_REQUIRED')
artifact = compute_checksum(backend_artifact_manifest())
require(artifact == upgrade['backend_artifact_sha256'], 'ARCHIVE_READBACK_SAME_IMPLEMENTATION_REQUIRED')
validator = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/FactView'}, registry=REGISTRY, format_checker=FormatChecker())
source_ids = {item.id for item in source.sources}
allowed_codes = {'archive_text', 'report_item', 'archive_time'}
states = Counter()
observations = []
pagination_requests = 0
record_read_count = 0
test = unittest.TestCase()

with content_reader(), transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cursor.execute('SELECT current_database(),current_user,system_identifier::text,current_setting(\'server_version\') FROM pg_control_system()')
        identity = cursor.fetchone()
        require(tuple(upgrade[key] for key in ('database', 'role', 'cluster_id', 'server_version')) == identity,
            'ARCHIVE_READBACK_EXACT_DATABASE_REQUIRED')
        cursor.execute('SHOW transaction_read_only')
        require(cursor.fetchone() == ('on',), 'ARCHIVE_READBACK_READ_ONLY_REQUIRED')
    files = {str(row.id): row for row in FileObject.objects.filter(id__in=source_ids, purpose='original', status='available')}
    require(set(files) == source_ids, 'ARCHIVE_READBACK_APPROVED_ORIGINALS_REQUIRED')
    require(all(files[item.id].checksum == item.sha256 and files[item.id].size_bytes == item.size_bytes
        for item in source.sources), 'ARCHIVE_READBACK_ORIGINAL_VERSION_CHANGED')
    fields = {row.id: row for row in RecordField.objects.all()}
    anchors = {row.id: row for row in SourceAnchor.objects.filter(id__in={row.anchor_id for row in fields.values()})}
    pages = {row.id: row for row in FilePage.objects.filter(id__in={row.page_id for row in anchors.values()})}
    historical_files = {row.id: row for row in FileObject.objects.filter(id__in={row.file_id for row in pages.values()})}
    approved_content = {(item.sha256, item.size_bytes) for item in source.sources}
    require(all(row.purpose == 'original' and row.status == 'available'
        and (row.checksum, row.size_bytes) in approved_content for row in historical_files.values()),
        'ARCHIVE_READBACK_APPROVED_HISTORICAL_ORIGINAL_REQUIRED')
    require(len(fields) == arguments.expected_versions and all(row.field_code in allowed_codes
        and row.anchor_id in anchors and anchors[row.anchor_id].page_id in pages
        and pages[anchors[row.anchor_id].page_id].file_id in historical_files
        and row.patient_id == anchors[row.anchor_id].patient_id == pages[anchors[row.anchor_id].page_id].patient_id
        == historical_files[pages[anchors[row.anchor_id].page_id].file_id].patient_id
        for row in fields.values()), 'ARCHIVE_READBACK_APPROVED_FIELD_SCOPE_REQUIRED')
    versions = list(ArchiveFactVersion.objects.order_by('fact_id', 'version'))
    facts = list(ArchiveFact.objects.order_by('id'))
    require(len(facts) == arguments.expected_facts and len(versions) == arguments.expected_versions
        and {row.field_id for row in versions} == set(fields), 'ARCHIVE_READBACK_EXACT_FACT_SCOPE_REQUIRED')
    facts_by_id = {row.id: row for row in facts}
    records = {row.id: row for row in Record.objects.filter(patient_id__in={row.patient_id for row in facts})}
    record_versions = {row.id: row for row in RecordVersion.objects.filter(record_id__in=records)}
    require({row.record_version_id for row in fields.values()} == set(record_versions),
        'ARCHIVE_READBACK_EXACT_RECORD_SCOPE_REQUIRED')
    patients = {row.id: row for row in Patient.objects.filter(id__in={row.patient_id for row in files.values()}
        | {row.patient_id for row in historical_files.values()})}
    require(len(patients) > 1 and all(row.patient_id in patients for row in facts),
        'ARCHIVE_READBACK_ACTUAL_SOURCE_GROUPS_REQUIRED')
    for record in records.values():
        patient = patients[record.patient_id]
        initial = next(row for row in record_versions.values() if row.record_id == record.id and row.version == 1)
        actor = Principal.objects.get(id=initial.author_id)
        for code in {row.field_code for row in fields.values() if row.record_version_id in {
                version.id for version in record_versions.values() if version.record_id == record.id}}:
            require(any(grant.purpose == 'family_observation' and grant.evidence_ref.strip()
                for grant in matching_case_grants(actor, patient, 'read_raw', datetime.now(timezone.utc),
                    field_codes=[code], record_id=record.id, author_id=initial.author_id, require_relation=True)),
                'ARCHIVE_READBACK_EXISTING_FIELD_PERMISSION_REQUIRED')
        history = record_history_read(patient=patient, record_id=record.id)
        expected = sorted((row for row in record_versions.values() if row.record_id == record.id), key=lambda row: row.version)
        require(history['current_version'] == str(record.current_version)
            and [row['record_version_id'] for row in history['versions']] == [str(row.id) for row in expected],
            'ARCHIVE_READBACK_RECORD_HISTORY_CHANGED')
        current = read_record(record.id, patient)
        require(current['version'] == record.current_version, 'ARCHIVE_READBACK_CURRENT_RECORD_CHANGED')
        for version in expected:
            value = record_version_read(patient=patient, record_version_id=version.id)
            actual_fields = {row['field_id']: row for row in value['fields']}
            references = {row['field_id']: row for row in value['fact_refs']}
            selected = [row for row in fields.values() if row.record_version_id == version.id]
            require(set(actual_fields) == set(references) == {str(row.id) for row in selected},
                'ARCHIVE_READBACK_RECORD_FIELDS_CHANGED')
            for field in selected:
                row = actual_fields[str(field.id)]
                for name in ('field_code', 'original_name', 'value_type', 'value_state', 'raw_text',
                        'categorical_value', 'qualifier', 'raw_unit', 'method', 'reference_interval', 'report_flag'):
                    require(row[name] == getattr(field, name), 'ARCHIVE_READBACK_RECORD_VALUE_CHANGED:' + name)
                require(row['raw_numeric'] == (str(field.raw_numeric) if field.raw_numeric is not None else None)
                    and row['medical_time'] == time_content(field.medical_time_id, patient),
                    'ARCHIVE_READBACK_RECORD_TIME_OR_DECIMAL_CHANGED')
                fact_version = next(item for item in versions if item.field_id == field.id)
                require(references[str(field.id)] == {'field_id': str(field.id), 'fact_id': str(fact_version.fact_id),
                    'version_id': str(fact_version.id), 'version': str(fact_version.version)},
                    'ARCHIVE_READBACK_RECORD_FACT_REFERENCE_CHANGED')
            if version.version == record.current_version:
                require(value == current, 'ARCHIVE_READBACK_CURRENT_AND_VERSION_READER_DIFFER')
            record_read_count += 1
    for patient in patients.values():
        selected_facts = [row for row in facts if row.patient_id == patient.id]
        selected_versions = [row for row in versions if row.patient_id == patient.id]
        current = current_fact_versions(selected_facts, patient)
        require(set(current) == {row.id for row in selected_facts}, 'ARCHIVE_READBACK_CURRENT_FACTS_CHANGED')
        sources = fact_sources(selected_versions, patient)
        for version in selected_versions:
            field = fields[version.field_id]
            value = fact_read(fact=facts_by_id[version.fact_id], patient=patient, version=version)
            error = next(validator.iter_errors(value), None)
            require(error is None, 'ARCHIVE_READBACK_FACT_SCHEMA_INVALID:' + (error.json_path if error else ''))
            clinical = value['field']['field']
            for name in ('field_code', 'original_name', 'value_type', 'value_state', 'raw_text',
                    'categorical_value', 'qualifier', 'raw_unit', 'method', 'reference_interval', 'report_flag'):
                require(clinical[name] == getattr(field, name), 'ARCHIVE_READBACK_FACT_VALUE_CHANGED:' + name)
            require(clinical['raw_numeric'] == (str(field.raw_numeric) if field.raw_numeric is not None else None)
                and clinical['medical_time'] == time_content(field.medical_time_id, patient)
                and value['context']['medical_time'] == clinical['medical_time']
                and value['context']['documented_time'] == time_content(version.documented_time_id, patient),
                'ARCHIVE_READBACK_FACT_TIME_OR_DECIMAL_CHANGED')
            require(sources[version.id] == (field, record_versions[version.record_version_id])
                and value['source_anchor_id'] == str(field.anchor_id)
                and value['source']['field_id'] == str(field.id)
                and value['source']['record_version_id'] == str(field.record_version_id)
                and value['source']['file_id'] == str(pages[anchors[field.anchor_id].page_id].file_id),
                'ARCHIVE_READBACK_FACT_SOURCE_CHANGED')
            require(value['available_at'] == (version.available_at.isoformat() if version.available_at else None)
                and value['availability_status'] == version.availability_status
                and value['context']['raw_time_text'] == version.raw_time_text
                and value['context']['time_origin'] == version.time_origin
                and value['context']['context_type'] == version.context_type
                and value['context']['assertion_state'] == version.assertion_state,
                'ARCHIVE_READBACK_UNKNOWN_CONTEXT_CHANGED')
            resolved = source_resolve(patient=patient, record_version_id=field.record_version_id, field_id=field.id)
            require(resolved['field_id'] == str(field.id) and resolved['field_code'] == field.field_code,
                'ARCHIVE_READBACK_LEGACY_SOURCE_READER_CHANGED')
            states[version.time_origin] += 1
            observations.append(compute_checksum(value))
        for fact in selected_facts:
            seen = []
            cursor = None
            expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
            while True:
                history = fact_versions(fact=fact, patient=patient, scope={'purpose': 'upgrade_readback'},
                    limit=1, expires_at=expiry, cursor=cursor)
                pagination_requests += 1
                seen.extend(row['version_id'] for row in history['items'])
                cursor = history['next_cursor']
                if cursor is None:
                    break
                require(len(seen) <= arguments.expected_versions, 'ARCHIVE_READBACK_HISTORY_MUST_TERMINATE')
            expected = [str(row.id) for row in sorted(selected_versions, key=lambda row: row.version, reverse=True)
                if row.fact_id == fact.id]
            require(seen == expected and len(seen) == len(set(seen)), 'ARCHIVE_READBACK_FACT_HISTORY_CHANGED')
        if selected_facts:
            other = next(row for row in patients.values() if row.id != patient.id)
            with test.assertRaises(QueryRejected):
                current_fact_versions(selected_facts, other)
            with test.assertRaises(RecordField.DoesNotExist):
                fact_sources(selected_versions, other)
    require(Verification.objects.count() == 0, 'ARCHIVE_READBACK_NO_NEW_PROFESSIONAL_QUALIFICATION')
    with connection.cursor() as cursor:
        cursor.execute('SHOW transaction_read_only')
        require(cursor.fetchone() == ('on',), 'ARCHIVE_READBACK_READ_ONLY_MUST_REMAIN')

for file in historical_files.values():
    response = verified_file_response(file)
    digest = hashlib.sha256()
    size = 0
    try:
        for chunk in response.streaming_content:
            digest.update(chunk)
            size += len(chunk)
    finally:
        response.close()
    require((digest.hexdigest(), size) == (file.checksum, file.size_bytes)
        and response.closed, 'ARCHIVE_READBACK_HISTORICAL_ORIGINAL_CONTENT_CHANGED')
require(compute_checksum(backend_artifact_manifest()) == artifact, 'ARCHIVE_READBACK_IMPLEMENTATION_CHANGED')
report = {'result': 'PASS', 'checked_at': datetime.now(timezone.utc).isoformat(),
    'database': identity[0], 'role': identity[1], 'cluster_id': identity[2], 'server_version': identity[3],
    'backend_artifact_sha256': artifact, 'upgrade_report_sha256': arguments.upgrade_report_sha256,
    'source_manifest_sha256': arguments.source_manifest_sha256, 'facts': len(facts), 'fact_versions': len(versions),
    'record_versions_read': record_read_count, 'fact_history_requests': pagination_requests,
    'historical_originals_read': len(historical_files),
    'historical_originals_with_distinct_ids': sum(str(row.id) not in source_ids for row in historical_files.values()),
    'time_origins': dict(states), 'fact_response_digest': compute_checksum(observations),
    'read_only': True, 'new_model_calls': 0, 'clinical_qualification': 'NOT_ESTABLISHED',
    'new_migration_execution': 'NOT_RUN', 'http_authorization_acceptance': 'NOT_RUN'}
with arguments.output.open('x') as output:
    json.dump(report, output, ensure_ascii=False, sort_keys=True, indent=2)
print(json.dumps(report, ensure_ascii=False))
