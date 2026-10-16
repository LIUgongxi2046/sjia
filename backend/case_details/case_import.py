"""真实病例领域导入：从设计投影导入 18 张扩展表、记录与医学时间。

使用确定性标识（uuid5）建立记录、字段、医学时间与全部病例实体；
不运行临床规则，clinical_rule_eligible 保持 false。
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid5

from django.db import transaction

from case_details.models import (
    ClinicalEpisode,
    ClinicalStatement,
    DataQualityIssue,
    DataQualityIssueReference,
    ExternalScoreComponent,
    ExternalScoreObservation,
    FollowupEncounter,
    FollowupReference,
    ImagingAssetLink,
    ImagingStudy,
    ImagingStudySite,
    MedicationEvent,
    MedicationEventItem,
    PatientProfileVersion,
    ProcedureEvent,
    RecordFieldGroup,
    RecordFieldGroupMember,
    StatementEvidence,
)
from identity.models import Patient
from medical_time.models import MedicalTime
from records.models import Record, RecordField, RecordVersion, Report
from case_details.import_admission import CaseImportRejected, load_projection, admit_case
from audit.api import audit_policy
from audit.service import write_audit

def _record_id(patient_namespace: UUID, record_version_id: str) -> str:
    return str(uuid5(patient_namespace, "record/" + record_version_id))


@transaction.atomic
def import_case(*, patient: Patient, operator, retention_policy, use_authorization_id, source_revision=None,
        projection_path=None, expected_projection_sha256=None, auth_epoch=None, file_bindings=None) -> dict:
    if source_revision is None:
        raise CaseImportRejected('CASE_SOURCE_REQUIRED')
    projection = load_projection(projection_path, expected_projection_sha256, patient.id)
    audit_policy(lock=False)
    run, original_files, replay = admit_case(projection=projection, digest=expected_projection_sha256,
        patient=patient, operator=operator, auth_epoch=auth_epoch, retention_policy=retention_policy,
        use_authorization_id=use_authorization_id, source_revision=source_revision, file_bindings=file_bindings or {})
    policy = audit_policy()
    if replay:
        write_audit(actor=operator, patient=patient, action='import_case', object_type='ingestion_run',
            object_id=run.id, outcome='allowed', reason_code='IDEMPOTENT_REPLAY', retention_policy=policy)
        return {'status': 'IMPORTED', 'ingestion_run_id': str(run.id), 'replayed': True,
            'medical_times': len(projection['medical_times']), 'fields': len(projection['fields']),
            'records': len({field['record_version_id'] for field in projection['fields']}), 'clinical_rule_eligible': False}
    namespace = UUID(projection["patient_id"])
    now = datetime.now(timezone.utc)

    for item in projection["medical_times"]:
        MedicalTime.objects.create(
            id=UUID(item["id"]), patient=patient,
            raw_text=item["raw_text"], precision=item["precision"], local_year=item["local_year"],
            local_month=item["local_month"], end_year=item["end_year"], end_month=item["end_month"],
            relative_text=item["relative_text"], local_date=item["local_date"], local_datetime=item["local_datetime"],
            timezone_name=item["timezone_name"], utc_offset_minutes=item["utc_offset_minutes"],
            instant=item['instant'], resolution_status=item["resolution_status"],
        )

    versions: dict[str, tuple] = {}
    for field in projection["fields"]:
        versions.setdefault(field["record_version_id"], []).append(field)
    for record_version_id, fields in versions.items():
        record = Record.objects.create(
            id=UUID(_record_id(namespace, record_version_id)), patient=patient, kind=Record.Kind.PROFESSIONAL,
            current_version=1, status=Record.Status.ACTIVE, created_at=now,
        )
        version = RecordVersion.objects.create(
            id=UUID(record_version_id), patient=patient, record_id=record.id, version=1, author=operator,
            origin_type=RecordVersion.OriginType.AUTHORIZED_CLINICAL, capture_method=RecordVersion.CaptureMethod.AUTHORIZED_IMPORT,
            source_revision=source_revision, use_authorization_id=use_authorization_id,
            quality_status=RecordVersion.QualityStatus.PENDING, quality_reasons=[], recorded_at=now, retention_policy=retention_policy,
        )
        for ordinal, field in enumerate(fields, start=1):
            RecordField.objects.create(
                id=UUID(field["id"]), patient=patient, record_version_id=UUID(record_version_id), ordinal=ordinal,
                field_code=field["original_name"], concept_code=None, original_name=field["original_name"],
                value_type=field["value_type"], value_state="known" if field["value_type"] == "decimal" or field['raw_text'] else "unknown",
                raw_text=field["raw_text"], raw_numeric=Decimal(str(field["raw_numeric"])) if field["raw_numeric"] is not None else None,
                qualifier=field["qualifier"], raw_unit=field["raw_unit"], report_flag=field["report_flag"],
                reference_interval=field["reference_interval"], medical_time_id=field.get('medical_time_id'),
                sample_time_origin="report", quality_reasons=[],
            )
        versions[record_version_id] = (record, version)

    from case_details.domain_import import import_domain_entities

    import_domain_entities(case=projection['case_details'], patient=patient, source_revision=source_revision,
        original_files=original_files, now=now)

    from django.db.models import F
    run.status, run.finished_at = 'completed', datetime.now(timezone.utc)
    run.save(update_fields=['status', 'finished_at'])
    Patient.objects.filter(id=patient.id).update(data_epoch=F('data_epoch') + 1)
    write_audit(actor=operator, patient=patient, action='import_case', object_type='ingestion_run',
        object_id=run.id, outcome='committed', retention_policy=policy)
    return {
        "status": "IMPORTED",
        'ingestion_run_id': str(run.id), 'replayed': False,
        "medical_times": len(projection['medical_times']),
        "fields": len(projection['fields']),
        "records": len(versions),
        "clinical_rule_eligible": False,
    }
