"""Offline, bounded addition to an already admitted fictional family dataset.

Uses unchanged backend authorization, record/archive writers, local validation,
summary execution and disposition fences. Never provisions identities, sessions,
permissions, configurations, provider responses or clinical decisions.
"""
import json
from datetime import datetime, timedelta
from uuid import uuid5

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from authorization.policy import evaluate
from configurations.evidence import artifact_verification_scope, inspect_approved_evidence
from configurations.field_config import validate_record_configuration
from configurations.models import ConfigVersion
from core.demo_archives import ensure_demo_manual_archive
from core.demo_experience import KEY, identity, require_test_context
from core.demo_files_summary import (_identity, ensure_demo_image_attachment,
    ensure_demo_report_attachment, ensure_demo_summary)
from core.demo_records import (_admit_context, _attachment_fields, _existing_receipt,
    DEMO_VERSION, demo_identity)
from core.demo_visit_files import render_demo_visit_png
from core.demo_worker import worker_scope
from drafts.models import Draft
from drafts.service import create_draft
from files.models import FileGroup, FileObject
from identity.models import Patient, Principal
from records.input_validation import validate_family_record_input
from records.models import Record, RecordVersion
from records.service import create_record
from requests.models import WriteIntent, WriteRequest
from requests.service import commit_intent, compute_digest, prepare_intent
from retention.content_fences import assert_content_available, content_reader
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from sources.operations import RetentionRules
from summaries.inputs import SummaryInput
from summaries.models import Summary
from summaries.submission import summary_permission, summary_receipt
from tasks.models import Task

from enrichment_plan import (BATCH_KEY, BATCH_NAMESPACE, PATIENT_KEYS, anchor_day,
    draft_plan, record_plan, report_rows, visit_plan)


class EnrichmentRejected(ValueError):
    pass


def _scope(username, principal_id, acknowledge_test_only):
    scope = worker_scope(username=username, principal_id=principal_id,
        acknowledge_test_only=acknowledge_test_only)
    revision, content, reason = inspect_approved_evidence(
        f'urn:sjia:source-revision:{identity("source-revision")}', purpose='software_test')
    if reason:
        raise EnrichmentRejected(f'ENRICHMENT_SOURCE_UNAVAILABLE:{reason}')
    anchor = datetime.fromisoformat(json.loads(content)['as_of'])
    anchor_day(anchor)
    actor = Principal.objects.get(id=scope.actor_id)
    patients = {key: Patient.objects.get(id=identity(f'patient:{key}')) for key in PATIENT_KEYS}
    uses = {key: DataUseAuthorization.objects.get(id=identity(f'use:{key}')) for key in PATIENT_KEYS}
    runtime = ConfigVersion.objects.get(id=scope.runtime_id)
    policies = {kind: RetentionPolicy.objects.get(id__in=runtime.payload['retention_policy_ids'],
        data_class=kind, status='active')
        for kind in ('observation', 'report', 'therapy', 'draft', 'request', 'file')}
    configs = {kind: ConfigVersion.objects.get(config_key=f'{KEY}:fields:{kind}',
        source_revision=revision, status='enabled') for kind in ('observation', 'report', 'therapy')}
    actor, patients, _, revision, uses = _admit_context(actor=actor, patients=patients,
        retention_policies=policies, source_revision=revision, use_authorizations=uses, field_configs=configs)
    return actor, patients, policies, revision, uses, configs, anchor


def _attachment(actor, patient, revision, use, policies, *, key, content=None, report=None):
    group_id = _identity(BATCH_NAMESPACE, patient, actor, f'report:{key}')
    existed = FileGroup.objects.filter(id=group_id).exists()
    common = dict(actor=actor, patient=patient, source_revision=revision, use_authorization=use,
        retention_policy=policies['file'], request_retention_policy=policies['request'],
        namespace=BATCH_NAMESPACE, report_key=key)
    if report is not None:
        group = ensure_demo_report_attachment(**common, **report)
    else:
        group = ensure_demo_image_attachment(**common, content=content)
    file = FileObject.objects.get(file_group_id=group.id, patient=patient, purpose='original', status='available')
    return group, file, not existed


def _record(actor, patient, policies, revision, use, configs, spec, attachment):
    kind = spec['kind']
    key = f'{BATCH_KEY}/{spec["key"]}'
    target = f'/api/patients/{patient.id}/records'
    fields, file_ids = _attachment_fields(spec, attachment, patient=patient,
        actor=actor, source_revision=revision, use=use)
    payload = {'kind': kind, 'capture_method': 'manual',
        'origin_type': 'report_transcription' if kind == 'report' else 'self_report',
        'field_config_version': str(configs[kind].id), 'fields': fields,
        'source_organization': spec['source_organization'], 'file_ids': file_ids}
    if kind == 'report':
        payload['report'] = spec['report_metadata']
    validate_family_record_input(payload)
    validate_record_configuration(configs[kind].id, kind, fields)
    evaluate(principal=actor, patient=patient, action='write_record', purpose='family_observation',
        environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=actor.auth_epoch,
        author_id=actor.id, field_codes=[field['field_code'] for field in fields], require_relation=True)
    receipt = _existing_receipt(key=key, actor=actor, patient=patient, operation='record_create',
        target=target, resource_type='record')
    created = receipt is None
    if created:
        intent = prepare_intent(actor=actor, patient=patient, operation_id='record_create', target_key=target,
            request_id=demo_identity(f'{DEMO_VERSION}:{key}'), payload=payload, recovery_payload=payload,
            schema_version='back-json-1', retention_policy=policies['request'])
        receipt = create_record(patient=patient, actor=actor, kind=kind, capture_method='manual',
            origin_type=payload['origin_type'], retention_policy=policies[kind], use_authorization_id=use.id,
            fields=fields, source_revision=revision, source_organization=spec['source_organization'],
            recorded_at=spec['recorded_at'], file_ids=file_ids, report_metadata=spec.get('report_metadata'))
        commit_intent(intent=intent, action='record_create', resource_type='record', resource_id=receipt['record_id'])
    else:
        intent = WriteIntent.objects.get(request_id=demo_identity(f'{DEMO_VERSION}:{key}'))
        if intent.payload_sha256 != compute_digest('record_create', target, 'back-json-1', payload):
            raise EnrichmentRejected('ENRICHMENT_RECORD_INPUT_CONFLICT')
    record = Record.objects.get(id=receipt['record_id'], patient=patient)
    initial = RecordVersion.objects.get(record_id=record.id, patient=patient, version=1)
    if (record.kind != kind or initial.author_id != actor.id or initial.source_revision_id != revision.id
            or initial.use_authorization_id != use.id or initial.source_organization != spec['source_organization']):
        raise EnrichmentRejected('ENRICHMENT_FOREIGN_RECORD')
    assert_content_available('records.record', record.id)
    assert_content_available('records.recordversion', initial.id)
    return receipt, created


def _draft(actor, patient, policy, spec):
    marker = f'backend-draft-1:synthetic:{uuid5(BATCH_NAMESPACE, spec["key"])}'
    existing = list(Draft.all_objects.filter(schema_version=marker))
    if len(existing) > 1:
        raise EnrichmentRejected('ENRICHMENT_DUPLICATE_DRAFT')
    if existing:
        draft = existing[0]
        if (draft.owner_id != actor.id or draft.patient_id != patient.id or draft.form_type != spec['form_type']
                or draft.retention_policy_id != policy.id):
            raise EnrichmentRejected('ENRICHMENT_FOREIGN_DRAFT')
        assert_content_available('drafts.draft', draft.id)
        if not Draft.objects.filter(id=draft.id).exists():
            raise EnrichmentRejected('ENRICHMENT_DRAFT_DISPOSED')
        # User edits and abandoned/submitted/expired drafts remain unchanged.
        return draft, False
    evaluate(principal=actor, patient=patient, action='manage_draft', purpose='family_observation',
        environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=actor.auth_epoch,
        author_id=actor.id, require_relation=True)
    rules = RetentionRules.model_validate(policy.rules)
    expires_at = timezone.now() + timedelta(days=rules.retention_days) if rules.disposition == 'delete' else None
    return create_draft(patient=patient, owner=actor, form_type=spec['form_type'], schema_version=marker,
        payload=spec['payload'], retention_policy=policy, expires_at=expires_at), True


def _summary(actor, patient, policy, anchor):
    start, end = anchor_day(anchor) - timedelta(days=14), anchor_day(anchor)
    target = f'/api/patients/{patient.id}/summaries'
    request_id = _identity(BATCH_NAMESPACE, patient, actor, 'summary')
    intents = list(WriteIntent.all_objects.filter(request_id=request_id))
    if len(intents) > 1:
        raise EnrichmentRejected('ENRICHMENT_DUPLICATE_SUMMARY')
    if intents:
        intent = intents[0]
        expected = SummaryInput.model_validate({'from_date': start.isoformat(), 'to_date': end.isoformat(),
            'scope_timezone': 'Asia/Shanghai', 'include_unknown_time': False,
            'record_kinds': ['observation', 'report', 'therapy'], 'include_source_status': True}).model_dump()
        if (intent.actor_id != actor.id or intent.patient_id != patient.id or intent.operation_id != 'summary_create'
                or intent.target_key != target or intent.recovery_payload != expected
                or intent.payload_sha256 != compute_digest('summary_create', target, '1.0.0', expected)):
            raise EnrichmentRejected('ENRICHMENT_SUMMARY_CONFLICT')
        assert_content_available('requests.writeintent', intent.id)
        summary_permission(actor, patient, actor.auth_epoch, 'family', action='read_summary')
        receipt = summary_receipt(intent)
        if receipt['resource_type'] == 'task':
            task = Task.objects.get(id=receipt['resource_id'], actor=actor, patient=patient, status='completed')
            summary = Summary.objects.get(task_id=task.id, requested_by=actor, patient=patient)
        else:
            summary = Summary.objects.get(id=receipt['resource_id'], requested_by=actor, patient=patient)
            Task.objects.get(id=summary.task_id, actor=actor, patient=patient, status='completed')
        assert_content_available('summaries.summary', summary.id)
        # A later user edit may make this frozen snapshot stale. Do not reset
        # its data epoch, regenerate it or substitute another summary identity.
        return summary, False
    if WriteRequest.all_objects.filter(request_id=request_id).exists():
        raise EnrichmentRejected('ENRICHMENT_ORPHAN_SUMMARY')
    return ensure_demo_summary(actor=actor, patient=patient, retention_policy=policy,
        namespace=BATCH_NAMESPACE, from_date=start, to_date=end), True


def inspect_enrichment(*, username, principal_id, acknowledge_test_only=False):
    """Read identity/receipt preview; final execution also checks original bytes."""
    require_test_context(acknowledge_test_only=acknowledge_test_only)
    with content_reader(), transaction.atomic(), artifact_verification_scope():
        actor, patients, policies, revision, uses, configs, anchor = _scope(username, principal_id, acknowledge_test_only)
        existing_records = 0
        for spec in record_plan(anchor):
            patient = patients[spec['patient_key']]
            receipt = _existing_receipt(key=f'{BATCH_KEY}/{spec["key"]}', actor=actor, patient=patient,
                operation='record_create', target=f'/api/patients/{patient.id}/records', resource_type='record')
            if receipt is not None:
                record = Record.objects.get(id=receipt['record_id'], patient=patient, kind=spec['kind'])
                RecordVersion.objects.get(record_id=record.id, version=1, author=actor,
                    source_revision=revision, use_authorization=uses[spec['patient_key']])
                assert_content_available('records.record', record.id)
                existing_records += 1
        draft_count = 0
        for spec in draft_plan(anchor):
            marker = f'backend-draft-1:synthetic:{uuid5(BATCH_NAMESPACE, spec["key"])}'
            rows = list(Draft.all_objects.filter(schema_version=marker))
            if len(rows) > 1:
                raise EnrichmentRejected('ENRICHMENT_DUPLICATE_DRAFT')
            if rows:
                row = rows[0]
                if row.owner_id != actor.id or row.patient_id != patients[spec['patient_key']].id or row.form_type != spec['form_type']:
                    raise EnrichmentRejected('ENRICHMENT_FOREIGN_DRAFT')
                assert_content_available('drafts.draft', row.id)
                if not Draft.objects.filter(id=row.id).exists():
                    raise EnrichmentRejected('ENRICHMENT_DRAFT_DISPOSED')
                draft_count += 1
        return {'batch': BATCH_KEY, 'mode': 'read_only_identity_and_receipt_preview',
            'planned': {'records': 66, 'originals': 10, 'drafts': 4, 'archives': 4, 'summaries': 2},
            'existing_record_receipts': existing_records, 'existing_drafts': draft_count,
            'anchor': anchor.isoformat(), 'writes': 0,
            'remaining_validation': 'Execution additionally verifies every immutable input, original byte sequence, archive and summary receipt.'}


def enrich_family_experience(*, username, principal_id, acknowledge_test_only=False):
    require_test_context(acknowledge_test_only=acknowledge_test_only)
    with content_reader(), transaction.atomic(), artifact_verification_scope():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [73109520261012])
        actor, patients, policies, revision, uses, configs, anchor = _scope(username, principal_id, acknowledge_test_only)
        result = {'batch': BATCH_KEY, 'synthetic': True, 'records': {}, 'drafts': {}, 'archives': {},
            'summary_ids': [], 'created': {'records': 0, 'originals': 0, 'drafts': 0, 'archives': 0, 'summaries': 0}}
        for spec in record_plan(anchor):
            child = spec['patient_key']
            patient, use = patients[child], uses[child]
            attachment = None
            if spec['kind'] == 'report':
                rows = report_rows(spec)
                sample = datetime.fromisoformat(spec['report_metadata']['sample_time']['local_date']).date()
                _, file, created = _attachment(actor, patient, revision, use, policies,
                    key=spec['key'].replace('/', '-'), report={'report_date': sample, 'rows': rows})
                source_text = '\n'.join(['EXAMPLE REPORT / NOT FOR CLINICAL USE',
                    f'Sample date: {sample.isoformat()}', *[' | '.join(row) for row in rows]])
                attachment = {'file_ids': [file.id], 'source_file_id': file.id, 'source_text': source_text}
                result['created']['originals'] += created
            receipt, created = _record(actor, patient, policies, revision, use, configs, spec, attachment)
            result['records'][spec['key']] = receipt
            result['created']['records'] += created
        for spec in draft_plan(anchor):
            draft, created = _draft(actor, patients[spec['patient_key']], policies['draft'], spec)
            result['drafts'][spec['key']] = {'id': str(draft.id), 'patient_id': str(draft.patient_id),
                'status': draft.status, 'version': draft.version}
            result['created']['drafts'] += created
        for note in visit_plan(anchor):
            patient = patients[note['child_key']]
            group, _, created = _attachment(actor, patient, revision, uses[note['child_key']], policies,
                key=note['key'], content=render_demo_visit_png(note))
            result['created']['originals'] += created
            from archives.models import ArchiveDocument
            before = ArchiveDocument.objects.filter(patient_id=patient.id).count()
            archive = ensure_demo_manual_archive(actor=actor, patient=patient, file_group=group,
                proposed_episodes=note['proposed_episodes'], documented_time=note['documented_time'],
                organization=note['organization'], confirmed_text=note['confirmed_text'],
                request_key=f'{BATCH_KEY}/{note["key"]}')
            result['archives'][note['key']] = archive
            result['created']['archives'] += ArchiveDocument.objects.filter(patient_id=patient.id).count() - before
        for patient in patients.values():
            summary, created = _summary(actor, patient, policies['request'], anchor)
            result['summary_ids'].append(str(summary.id))
            result['created']['summaries'] += created
        result['anchor'] = anchor.isoformat()
        result['clinical_assessment'] = 'not_run'
        result['external_providers'] = []
        return result
