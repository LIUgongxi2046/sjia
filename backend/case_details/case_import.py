"""真实病例领域导入：从设计投影导入 18 张扩展表、记录与医学时间。

使用确定性标识（uuid5）建立记录、字段、医学时间与全部病例实体；
不运行临床规则，clinical_rule_eligible 保持 false。
"""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
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
from files.models import FileGroup, FileObject
from identity.models import Patient
from medical_time.models import MedicalTime
from records.models import Record, RecordField, RecordVersion, Report

PROJECTION_PATH = Path(__file__).resolve().parents[2] / "data-design" / "case-design-projection.json"
MANIFEST_PATH = Path(__file__).resolve().parents[2] / "demo-data" / "current-patient" / "source-manifest.json"


def _record_id(patient_namespace: UUID, record_version_id: str) -> str:
    return str(uuid5(patient_namespace, "record/" + record_version_id))


@transaction.atomic
def import_case(*, patient: Patient, operator, retention_policy, use_authorization_id, source_revision=None, projection_path=None) -> dict:
    projection = json.loads((Path(projection_path) if projection_path else PROJECTION_PATH).read_text(encoding="utf-8"))
    namespace = UUID(projection["patient_id"])
    now = datetime.now(timezone.utc)

    for item in projection["medical_times"]:
        MedicalTime.objects.create(
            id=UUID(item["id"]), patient=patient,
            raw_text=item["raw_text"], precision=item["precision"], local_year=item["local_year"],
            local_month=item["local_month"], end_year=item["end_year"], end_month=item["end_month"],
            relative_text=item["relative_text"], local_date=item["local_date"], local_datetime=item["local_datetime"],
            timezone_name=item["timezone_name"], utc_offset_minutes=item["utc_offset_minutes"],
            instant=None, resolution_status=item["resolution_status"],
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
                value_type=field["value_type"], value_state="known" if field["value_type"] == "decimal" else "unknown",
                raw_text=field["raw_text"], raw_numeric=Decimal(str(field["raw_numeric"])) if field["raw_numeric"] is not None else None,
                qualifier=field["qualifier"], raw_unit=field["raw_unit"], report_flag=field["report_flag"],
                reference_interval=field["reference_interval"], sample_time_origin="report", quality_reasons=[],
            )
        versions[record_version_id] = (record, version)

    # 原件与报告：病例实体引用的文件对象与影像报告。
    file_group = FileGroup.objects.create(
        patient=patient, owner=operator, version=1, status=FileGroup.Status.AVAILABLE,
        source_revision=source_revision, use_authorization_id=use_authorization_id, created_at=now,
    )
    if MANIFEST_PATH.is_file():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for source in manifest["sources"]:
            media_type = "image/png" if source.get("format") == "PNG" else "text/plain"
            FileObject.objects.create(
                id=uuid5(namespace, "file/" + source["id"]), patient=patient, file_group_id=file_group.id,
                object_key=source["path"], object_version=source["sha256"], checksum=source["sha256"],
                size_bytes=source["size_bytes"], media_type=media_type, purpose=FileObject.Purpose.ORIGINAL,
                status=FileObject.Status.AVAILABLE, retention_policy=retention_policy, created_at=now,
            )
    for item in projection["case_details"]["imaging_studies"]:
        Report.objects.create(
            id=UUID(item["report_id"]), patient=patient, report_status=Report.ReportStatus.FINAL,
            file_group_id=file_group.id, created_at=now,
        )

    case = projection["case_details"]

    for item in case["profiles"]:
        PatientProfileVersion.objects.create(
            id=UUID(item["id"]), patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
            source_field_id=UUID(item["source_field_id"]), sex=item["sex"], reported_age_years=item["reported_age_years"],
            reported_age_months=item["reported_age_months"], age_raw_text=item["age_raw_text"],
            age_reference_time_id=UUID(item["age_reference_time_id"]) if item["age_reference_time_id"] else None,
            birth_date=item["birth_date"], patient_name=item["patient_name"], chief_complaint=item["chief_complaint"],
        )
    for item in case["episodes"]:
        ClinicalEpisode.objects.create(
            id=UUID(item["id"]), patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
            source_field_id=UUID(item["source_field_id"]), episode_type=item["episode_type"], label=item["label"],
            start_time_id=UUID(item["start_time_id"]) if item["start_time_id"] else None,
            end_time_id=UUID(item["end_time_id"]) if item["end_time_id"] else None,
            organization_text=item["organization_text"], department_text=item["department_text"],
        )
    for item in case["statements"]:
        statement = ClinicalStatement.objects.create(
            id=UUID(item["id"]), patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
            source_field_id=UUID(item["source_field_id"]), statement_type=item["statement_type"], subject=item["subject"],
            category=item["category"], concept_code=item["concept_code"], label=item["label"],
            assertion_status=item["assertion_status"], status_raw_text=item["status_raw_text"],
            medical_time_id=UUID(item["medical_time_id"]) if item["medical_time_id"] else None,
            body_site=item["body_site"], laterality=item["laterality"],
        )
        for evidence in item.get("evidence", []):
            StatementEvidence.objects.create(
                statement_id=statement.id, patient_id=patient.id, evidence_field_id=UUID(evidence["field_id"]),
                evidence_record_version_id=UUID(evidence["record_version_id"]), role=evidence["role"],
            )
    for item in case["groups"]:
        group = RecordFieldGroup.objects.create(
            id=UUID(item["id"]), patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
            source_field_id=UUID(item["source_field_id"]), group_type=item["group_type"], label=item["label"],
            specimen_text=item["specimen_text"], method_text=item["method_text"],
            medical_time_id=UUID(item["medical_time_id"]) if item["medical_time_id"] else None, source_quote=item["source_quote"],
        )
        for member in item["members"]:
            RecordFieldGroupMember.objects.create(
                group_id=group.id, patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
                field_id=UUID(member["field_id"]), ordinal=member["ordinal"], role=member["role"],
            )
    for item in case["imaging_studies"]:
        study = ImagingStudy.objects.create(
            id=UUID(item["id"]), patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
            source_field_id=UUID(item["source_field_id"]), report_id=UUID(item["report_id"]), modality=item["modality"],
            contrast=item["contrast"], study_time_id=UUID(item["study_time_id"]) if item["study_time_id"] else None,
            external_study_ref=item["external_study_ref"],
        )
        for site in item["sites"]:
            ImagingStudySite.objects.create(
                study_id=study.id, patient_id=patient.id, ordinal=site["ordinal"], body_site=site["body_site"], laterality=site["laterality"],
            )
    for item in case["image_links"]:
        ImagingAssetLink.objects.create(
            id=UUID(item["id"]), patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
            source_field_id=UUID(item["source_field_id"]), file_id=UUID(item["file_id"]), study_id=UUID(item["study_id"]) if item["study_id"] else None,
            link_status=item["link_status"], evidence_field_id=UUID(item["evidence_field_id"]) if item["evidence_field_id"] else None,
            evidence_record_version_id=UUID(item["evidence_record_version_id"]) if item["evidence_record_version_id"] else None,
            confirmed_by=None, confirmed_at=None,
        )
    for item in case["medication_events"]:
        event = MedicationEvent.objects.create(
            id=UUID(item["id"]), patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
            source_field_id=UUID(item["source_field_id"]), action=item["action"],
            medical_time_id=UUID(item["medical_time_id"]) if item["medical_time_id"] else None,
            previous_event_id=UUID(item["previous_event_id"]) if item["previous_event_id"] else None,
            reason_statement_id=UUID(item["reason_statement_id"]) if item["reason_statement_id"] else None, note=item["note"],
        )
        for entry in item["items"]:
            MedicationEventItem.objects.create(
                id=UUID(entry["id"]), event_id=event.id, patient_id=patient.id,
                record_version_id=UUID(item["record_version_id"]), source_field_id=UUID(entry["source_field_id"]),
                ordinal=entry["ordinal"], medication_name=entry["medication_name"], medication_code=entry["medication_code"],
                dose=Decimal(str(entry["dose"])) if entry["dose"] is not None else None, dose_unit=entry["dose_unit"],
                concentration_text=entry["concentration_text"], route_text=entry["route_text"], frequency_text=entry["frequency_text"],
                interval_value=Decimal(str(entry["interval_value"])) if entry["interval_value"] is not None else None, interval_unit=entry["interval_unit"],
            )
    for item in case["procedures"]:
        ProcedureEvent.objects.create(
            id=UUID(item["id"]), patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
            source_field_id=UUID(item["source_field_id"]), procedure_name=item["procedure_name"],
            medical_time_id=UUID(item["medical_time_id"]) if item["medical_time_id"] else None,
            related_medication_event_id=UUID(item["related_medication_event_id"]) if item["related_medication_event_id"] else None,
            body_site=item["body_site"],
        )
    for item in case["scores"]:
        score = ExternalScoreObservation.objects.create(
            id=UUID(item["id"]), patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
            source_field_id=UUID(item["source_field_id"]), instrument=item["instrument"], instrument_version=item["instrument_version"],
            medical_time_id=UUID(item["medical_time_id"]), total_field_id=UUID(item["total_field_id"]),
            context_text=item["context_text"], note=item["note"],
        )
        for component in item["components"]:
            ExternalScoreComponent.objects.create(
                score_id=score.id, patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
                field_id=UUID(component["field_id"]), component_code=component["component_code"], ordinal=component["ordinal"],
            )
    for item in case["followups"]:
        encounter = FollowupEncounter.objects.create(
            id=UUID(item["id"]), patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
            source_field_id=UUID(item["source_field_id"]), kind=item["kind"], label=item["label"],
            medical_time_id=UUID(item["medical_time_id"]), channel_text=item["channel_text"],
        )
        for reference in item["references"]:
            FollowupReference.objects.create(
                id=UUID(reference["id"]), patient_id=patient.id, encounter_id=encounter.id,
                statement_id=UUID(reference["statement_id"]) if reference["statement_id"] else None,
                group_id=UUID(reference["group_id"]) if reference["group_id"] else None,
                score_id=UUID(reference["score_id"]) if reference["score_id"] else None,
                medication_event_id=UUID(reference["medication_event_id"]) if reference["medication_event_id"] else None,
                relation_kind=reference["relation_kind"],
            )
    for item in case["quality_issues"]:
        issue = DataQualityIssue.objects.create(
            id=UUID(item["id"]), patient_id=patient.id, record_version_id=UUID(item["record_version_id"]),
            source_field_id=UUID(item["source_field_id"]), category=item["category"], description=item["description"],
            handling=item["handling"], status=item["status"], resolution_text=item["resolution_text"],
            resolution_field_id=UUID(item["resolution_field_id"]) if item["resolution_field_id"] else None,
            resolution_record_version_id=UUID(item["resolution_record_version_id"]) if item["resolution_record_version_id"] else None,
            resolved_by=None, resolved_at=None,
        )
        for reference in item["references"]:
            DataQualityIssueReference.objects.create(
                id=UUID(reference["id"]), patient_id=patient.id, issue_id=issue.id,
                field_id=UUID(reference["field_id"]) if reference["field_id"] else None,
                record_version_id=UUID(reference["record_version_id"]) if reference["record_version_id"] else None,
                file_id=UUID(reference["file_id"]) if reference["file_id"] else None, role=reference["role"],
            )

    return {
        "status": "IMPORTED",
        "medical_times": MedicalTime.objects.filter(patient=patient).count(),
        "fields": RecordField.objects.filter(patient=patient).count(),
        "records": Record.objects.filter(patient=patient).count(),
        "clinical_rule_eligible": False,
    }
