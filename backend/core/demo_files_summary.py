"""Synthetic demo attachments and summaries through the real local services.

Callers supply an existing principal, synthetic source, permissions and qualified
configuration. This module never provisions identities, credentials, grants,
clinical results or provider responses. Stable namespace keys make reruns reuse
the same immutable uploads and summary request; changed input fails closed.
"""
import io
import json
import re
from datetime import date
from uuid import UUID, uuid5

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from archives.batches import batch_runtime
from authorization.policy import evaluate
from files.errors import FileRejected
from files.models import FileGroup, FileObject, UploadPart
from files.purpose import lock_file_access
from files.service import create_file_group, declare_upload_part, read_object_bytes, store_file_object
from files.storage import digest_bytes
from files.validation import (
    execute_validation, prefix, prepare_validation_intent, retry_validation_intent,
    submit_validation,
)
from records.models import RecordVersion
from requests.policy import request_retention, require_request_policy_binding
from sources.models import DataUseAuthorization, SourceRevision
from summaries.inputs import SummaryInput
from summaries.jobs import execute_summary, lock_summary_scope
from summaries.models import Summary
from summaries.service import SummaryRejected, current_summary_config, read_summary
from summaries.submission import (
    prepare_summary_intent, retry_summary_intent, submit_summary, summary_permission,
)
from tasks.models import OutboxEvent, Task


def _identity(namespace, patient, actor, key):
    if not isinstance(namespace, UUID):
        raise ValueError('Demo namespace must be a UUID')
    return uuid5(namespace, f'demo-v1:{patient.id}:{actor.id}:{key}')


def _test_environment():
    if settings.SJIA_ENVIRONMENT not in ('test', 'synthetic-browser-demo') or settings.DEBUG is not True:
        raise ValueError('DEMO_TEST_ENVIRONMENT_REQUIRED')
    if connection.vendor != 'postgresql' or not str(connection.settings_dict['NAME']).startswith('test_'):
        raise ValueError('DEMO_TEST_DATABASE_REQUIRED')


def render_demo_report_png(*, report_key, report_date, rows):
    """Render exact, supplied synthetic values, never a hospital/OCR document.

    Labels are deliberately ASCII so Pillow's bundled font is sufficient on
    every host. Chinese field labels remain in the associated normal record.
    PNG metadata also retains the full explicitly synthetic source payload.
    """
    if (not isinstance(report_key, str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', report_key)
            or type(report_date) is not date or not isinstance(rows, (list, tuple))
            or not 1 <= len(rows) <= 16):
        raise ValueError('Invalid synthetic report input')
    values = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise ValueError('Each report row needs label, value and unit')
        for cell, maximum in zip(row, (32, 24, 20), strict=True):
            if (not isinstance(cell, str) or len(cell) > maximum
                    or any(ord(char) < 32 or ord(char) > 126 for char in cell)):
                raise ValueError('Report cells must be bounded printable ASCII')
        if not row[0].strip() or not row[1].strip():
            raise ValueError('Report label and value are required')
        values.append(list(row))
    payload = {'schema_version': 'synthetic-demo-report-1', 'synthetic': True,
        'clinical_document': False, 'report_key': report_key,
        'report_date': report_date.isoformat(), 'rows': values}
    image = Image.new('RGB', (1120, 440 + 64 * len(values)), '#f4f7fb')
    draw = ImageDraw.Draw(image)
    heading, body, small = (ImageFont.load_default(size=size) for size in (38, 24, 19))
    draw.rectangle((0, 0, 1120, 155), fill='#153e59')
    draw.text((48, 33), 'EXAMPLE REPORT', font=heading, fill='white')
    draw.text((48, 96), 'NOT FOR CLINICAL USE', font=body, fill='#e6f5ff')
    draw.text((48, 181), f'Report: {report_key}', font=small, fill='#153e59')
    draw.text((48, 214), f'Sample date: {report_date.isoformat()} | Fictional patient', font=small, fill='#153e59')
    positions = (48, 590, 860)
    for x, label in zip(positions, ('Example measurement', 'Value', 'Unit'), strict=True):
        draw.text((x, 268), label, font=body, fill='#153e59')
    for index, row in enumerate(values):
        y = 315 + index * 64
        draw.rectangle((32, y - 8, 1088, y + 49), fill='white' if index % 2 == 0 else '#e9f0f6')
        for x, width, cell in zip(positions, (516, 248, 212), row, strict=True):
            size = min(24, int(24 * width / max(1, body.getlength(cell))))
            draw.text((x, y), cell, font=ImageFont.load_default(size=max(1, size)), fill='#203746')
    draw.text((48, image.height - 72), 'Invented sample values for software demonstration only.', font=small, fill='#5b3941')
    draw.text((48, image.height - 42), 'No diagnosis, clinical verification or treatment recommendation.', font=small, fill='#5b3941')
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('Description', 'SYNTHETIC DEMO. Not a medical report. No clinical use.')
    metadata.add_text('demo-content', json.dumps(payload, sort_keys=True, separators=(',', ':')))
    stream = io.BytesIO()
    image.save(stream, format='PNG', pnginfo=metadata)
    return stream.getvalue()


def _request_policy(supplied):
    actual = request_retention(settings.SJIA_ENVIRONMENT)
    if actual.id != supplied.id:
        raise ValueError('Demo request retention does not match the active policy')
    return actual


@transaction.atomic
def _attachment_upload(*, actor, patient, source_revision, use_authorization,
        retention_policy, namespace, report_key, content):
    _, current_actor, patient = prefix(actor.id, patient.id)
    evaluate(principal=current_actor, patient=patient, action='write_record',
        purpose='family_observation', environment=settings.SJIA_ENVIRONMENT,
        session_auth_epoch=actor.auth_epoch, author_id=actor.id, whole_resource=True)
    revision = SourceRevision.objects.select_related('source').get(id=source_revision.id)
    authorization = DataUseAuthorization.objects.get(id=use_authorization.id)
    now = timezone.now()
    if (revision.source.sensitivity != 'synthetic' or revision.status != 'approved'
            or revision.source.status != 'approved' or not revision.rights_evidence_ref
            or 'family_observation' not in revision.allowed_use
            or authorization.patient_id != patient.id or authorization.source_revision_id != revision.id
            or authorization.environment != settings.SJIA_ENVIRONMENT
            or authorization.purpose != 'family_observation' or authorization.revoked_at is not None
            or authorization.approved_at > now
            or authorization.valid_until is not None and authorization.valid_until <= now):
        raise FileRejected('DEMO_SYNTHETIC_SOURCE_REQUIRED')
    runtime = batch_runtime()
    if (str(retention_policy.id) not in runtime.payload['retention_policy_ids']
            or retention_policy.data_class != 'file' or retention_policy.status != 'active'
            or retention_policy.approved_by_id is None or retention_policy.approved_at is None
            or not retention_policy.evidence_ref):
        raise FileRejected('DEMO_FILE_RETENTION_REQUIRED')
    if 'image/png' not in runtime.payload['limits']['media_types'] or len(content) > runtime.payload['limits']['max_upload_bytes']:
        raise FileRejected('DEMO_ATTACHMENT_LIMIT_EXCEEDED')
    group_id = _identity(namespace, patient, actor, f'report:{report_key}')
    group = FileGroup.objects.select_for_update().filter(id=group_id).first()
    if group is None:
        group = create_file_group(patient=patient, owner=current_actor, source_revision=revision,
            use_authorization_id=authorization.id, group_id=group_id)
    if (group.patient_id != patient.id or group.owner_id != actor.id or group.source_revision_id != revision.id
            or group.use_authorization_id != authorization.id or group.draft_id is not None or group.version != 1):
        raise FileRejected('DEMO_ATTACHMENT_SCOPE_CHANGED')
    upload_id = _identity(namespace, patient, actor, f'report-upload:{report_key}')
    parts = list(UploadPart.objects.select_for_update().filter(file_group_id=group.id))
    if not parts:
        part = declare_upload_part(file_group=group, client_upload_id=upload_id, ordinal=1,
            declared_checksum=digest_bytes(content), declared_size_bytes=len(content), declared_media_type='image/png')
    elif (len(parts) == 1 and parts[0].client_upload_id == upload_id and parts[0].ordinal == 1
            and parts[0].patient_id == patient.id and parts[0].declared_checksum == digest_bytes(content)
            and parts[0].declared_size_bytes == len(content) and parts[0].declared_media_type == 'image/png'):
        part = parts[0]
    else:
        raise FileRejected('DEMO_ATTACHMENT_INPUT_CHANGED')
    original = store_file_object(file_group=group, upload_part=part, data=content,
        purpose=FileObject.Purpose.ORIGINAL, retention_policy=retention_policy, limits=runtime.payload['limits'])
    return group, original


def ensure_demo_report_attachment(*, actor, patient, source_revision, use_authorization,
        retention_policy, request_retention_policy, namespace, report_key, report_date, rows):
    """Return one real, validated FileGroup; repeat calls never create duplicates."""
    _test_environment()
    content = render_demo_report_png(report_key=report_key, report_date=report_date, rows=rows)
    return ensure_demo_image_attachment(actor=actor, patient=patient, source_revision=source_revision,
        use_authorization=use_authorization, retention_policy=retention_policy,
        request_retention_policy=request_retention_policy, namespace=namespace,
        report_key=report_key, content=content)


def ensure_demo_image_attachment(*, actor, patient, source_revision, use_authorization,
        retention_policy, request_retention_policy, namespace, report_key, content):
    """Validate and persist exact rendered example PNG bytes via the normal pipeline."""
    _test_environment()
    if not isinstance(content, bytes) or not content.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('DEMO_PNG_REQUIRED')
    with transaction.atomic():
        _request_policy(request_retention_policy)
        group, original = _attachment_upload(actor=actor, patient=patient, source_revision=source_revision,
            use_authorization=use_authorization, retention_policy=retention_policy, namespace=namespace,
            report_key=report_key, content=content)
        target = f'/api/uploads/{group.id}/complete'
        payload = {'expected_version': str(group.version), 'file_ids': [str(original.id)]}
        data = {'operation_id': 'file_group_complete', 'target_key': target,
            'request_id': str(_identity(namespace, patient, actor, f'report-validation:{report_key}')),
            'schema_version': '1.0.0', 'payload': payload}
        require_request_policy_binding(actor=actor, data=data, policy=request_retention_policy)
        intent = prepare_validation_intent(actor=actor, auth_epoch=actor.auth_epoch, audience='family',
            data=data, retention_policy=request_retention_policy)
        if intent.status != 'committed' and intent.expires_at <= timezone.now():
            intent = retry_validation_intent(actor=actor, auth_epoch=actor.auth_epoch, audience='family',
                intent=intent, expected_generation=str(intent.generation))
        receipt = submit_validation(actor=actor, auth_epoch=actor.auth_epoch, audience='family',
            target=target, payload=payload, headers={'X-Write-Intent': str(intent.id),
                'X-Intent-Generation': str(intent.generation), 'Idempotency-Key': str(intent.request_id)})
    task = Task.objects.get(id=receipt['task_id'])
    event = OutboxEvent.objects.get(task=task, event_type='file_validation.requested')
    execute_validation(event, 'demo-local-file-validation')
    task.refresh_from_db()
    group.refresh_from_db()
    if task.status != 'completed' or group.status != FileGroup.Status.AVAILABLE:
        raise FileRejected('DEMO_ATTACHMENT_NOT_COMPLETED')
    with transaction.atomic():
        lock_file_access(original, actor, actor.auth_epoch, purpose='family_observation')
        if read_object_bytes(original.id) != content:
            raise FileRejected('DEMO_ATTACHMENT_READBACK_MISMATCH')
    return group


def ensure_demo_summary(*, actor, patient, retention_policy, namespace, from_date, to_date,
        scope_timezone='Asia/Shanghai'):
    """Run the ordinary local summary pipeline and verify its frozen record data.

    Invoke only after every demo record is saved. A later changed scope or stale
    summary is reported explicitly; this helper never overwrites user activity.
    """
    _test_environment()
    payload = SummaryInput.model_validate({'from_date': from_date.isoformat(), 'to_date': to_date.isoformat(),
        'scope_timezone': scope_timezone, 'include_unknown_time': False,
        'record_kinds': ['observation', 'report', 'therapy'], 'include_source_status': True}).model_dump()
    with transaction.atomic():
        _, _, current_patient = lock_summary_scope(actor.id, patient.id, settings.SJIA_ENVIRONMENT)
        _request_policy(retention_policy)
        if current_summary_config('summary', settings.SJIA_ENVIRONMENT).payload['purpose'] != 'software_test':
            raise SummaryRejected('DEMO_SOFTWARE_SUMMARY_REQUIRED')
        if RecordVersion.objects.filter(patient=current_patient).exclude(source_revision__source__sensitivity='synthetic').exists():
            raise SummaryRejected('DEMO_SYNTHETIC_RECORDS_REQUIRED')
        target = f'/api/patients/{patient.id}/summaries'
        data = {'operation_id': 'summary_create', 'target_key': target,
            'request_id': str(_identity(namespace, patient, actor, 'summary')),
            'schema_version': '1.0.0', 'payload': payload}
        require_request_policy_binding(actor=actor, data=data, policy=retention_policy)
        intent = prepare_summary_intent(actor=actor, auth_epoch=actor.auth_epoch, audience='family',
            data=data, retention_policy=retention_policy)
        if intent.status != 'committed' and (intent.status == 'failed' or intent.expires_at <= timezone.now()):
            intent = retry_summary_intent(actor=actor, auth_epoch=actor.auth_epoch, audience='family',
                intent_id=intent.id, expected_generation=str(intent.generation))
        receipt = submit_summary(actor=actor, patient=current_patient, auth_epoch=actor.auth_epoch, audience='family',
            target=target, data=payload, headers={'intent_id': str(intent.id),
                'generation': str(intent.generation), 'request_id': str(intent.request_id)})
    task = Task.objects.get(id=receipt['task_id'])
    execute_summary(OutboxEvent.objects.get(task=task, event_type='summary.requested'), 'demo-local-summary')
    task.refresh_from_db()
    if task.status != 'completed':
        raise SummaryRejected('DEMO_SUMMARY_NOT_COMPLETED')
    with transaction.atomic():
        _, _, current_patient = lock_summary_scope(actor.id, patient.id, settings.SJIA_ENVIRONMENT)
        permission = summary_permission(actor, current_patient, actor.auth_epoch, 'family', action='read_summary')
        summary = Summary.objects.get(task_id=task.id, patient=current_patient, requested_by=actor)
        result = read_summary(summary.id, current_patient)
        if result['needs_regeneration'] or not result['items']:
            raise SummaryRejected('DEMO_SUMMARY_NOT_READABLE')
        fields = {item['snapshot']['field']['field_code'] for item in result['items']}
        if not fields <= set(permission.grant.field_codes):
            raise SummaryRejected('DEMO_SUMMARY_READ_SCOPE_CHANGED')
    return summary
