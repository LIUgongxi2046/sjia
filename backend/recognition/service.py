"""OCR 候选、本人确认与报告登记。

OCR 输出只进入候选区；本人逐项 accepted/modified/unknown/excluded 确认；
确认不授予专业核对资格；确认后创建正式报告与 record_attachment。
"""
from datetime import datetime, timezone
from decimal import Decimal

from django.db import transaction

from files.models import FileObject
from recognition.models import CandidateConfirmation, CandidateField, CandidateSet, SourceAnchor
from records.models import Record, RecordAttachment, RecordField, RecordVersion
from records.models import Report


class RecognitionRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


@transaction.atomic
def create_candidate_set(
    *,
    patient,
    file_group_id,
    file_group_version,
    task_id,
    provider,
    model_version,
    provider_request_id,
    schema_version,
    raw_response_file_id=None,
) -> CandidateSet:
    return CandidateSet.objects.create(
        patient=patient,
        file_group_id=file_group_id,
        file_group_version=file_group_version,
        task_id=task_id,
        provider=provider,
        model_version=model_version,
        provider_request_id=provider_request_id,
        schema_version=schema_version,
        raw_response_file_id=raw_response_file_id,
        status=CandidateSet.Status.PENDING_CONFIRMATION,
        created_at=datetime.now(timezone.utc),
    )


@transaction.atomic
def add_source_anchor(*, patient, page_id, original_text, anchor_kind="page", x=None, y=None, width=None, height=None) -> SourceAnchor:
    return SourceAnchor.objects.create(
        patient=patient,
        page_id=page_id,
        anchor_kind=anchor_kind,
        coordinate_system="decoded_pixel",
        x=x,
        y=y,
        width=width,
        height=height,
        original_text=original_text,
    )


@transaction.atomic
def add_candidate_field(*, candidate_set, field_path, field_config_version, recognition_status, raw_text=None, proposed_field=None, anchor_id=None, confidence=None) -> CandidateField:
    return CandidateField.objects.create(
        patient=candidate_set.patient,
        candidate_set_id=candidate_set.id,
        field_path=field_path,
        raw_text=raw_text,
        field_config_version=field_config_version,
        proposed_field=proposed_field,
        anchor_id=anchor_id,
        recognition_status=recognition_status,
        confidence=Decimal(str(confidence)) if confidence is not None else None,
    )


@transaction.atomic
def confirm_candidate(*, candidate_field, confirmed_by, decision, confirmed_text=None) -> CandidateConfirmation:
    return CandidateConfirmation.objects.create(
        patient=candidate_field.patient,
        candidate_field_id=candidate_field.id,
        confirmed_by=confirmed_by,
        confirmed_text=confirmed_text,
        decision=decision,
        confirmed_at=datetime.now(timezone.utc),
    )


def all_candidates_decided(candidate_set) -> bool:
    total = CandidateField.objects.filter(candidate_set_id=candidate_set.id).count()
    decided = CandidateConfirmation.objects.filter(candidate_field_id__in=CandidateField.objects.filter(candidate_set_id=candidate_set.id).values_list("id", flat=True)).count()
    return total > 0 and total == decided


@transaction.atomic
def finalize_candidate_set(*, candidate_set) -> CandidateSet:
    if not all_candidates_decided(candidate_set):
        raise RecognitionRejected("CANDIDATES_NOT_DECIDED")
    candidate_set.status = CandidateSet.Status.CONFIRMED
    candidate_set.save(update_fields=["status"])
    return candidate_set


def confirmed_report_fields(candidate_set) -> list[dict]:
    candidates = CandidateField.objects.filter(candidate_set_id=candidate_set.id).order_by("field_path")
    confirmations = {
        c.candidate_field_id: c
        for c in CandidateConfirmation.objects.filter(candidate_field_id__in=candidates.values_list("id", flat=True))
    }
    fields = []
    for candidate in candidates:
        confirmation = confirmations.get(candidate.id)
        if confirmation is None or confirmation.decision in ("excluded", "unknown"):
            continue
        proposed = candidate.proposed_field or {}
        fields.append({
            "field_code": proposed.get("field_code", candidate.field_path.strip("/fields/")),
            "concept_code": proposed.get("concept_code"),
            "value_type": proposed.get("value_type", "text"),
            "value_state": "known" if proposed.get("raw_numeric") is not None or proposed.get("categorical_value") is not None or proposed.get("raw_text") else "unknown",
            "raw_text": confirmation.confirmed_text if confirmation.decision == "modified" else proposed.get("raw_text"),
            "raw_numeric": proposed.get("raw_numeric"),
            "categorical_value": proposed.get("categorical_value"),
            "qualifier": proposed.get("qualifier"),
            "raw_unit": proposed.get("raw_unit"),
            "sample_time_origin": "report",
            "confirmation_id": confirmation.id,
        })
    return fields


@transaction.atomic
def register_report(*, patient, actor, candidate_set, source_revision, report_status, retention_policy, use_authorization_id, sample_time_id=None, result_time_id=None, source_organization=None, external_ref=None) -> dict:
    if candidate_set.status != CandidateSet.Status.CONFIRMED:
        raise RecognitionRejected("CANDIDATE_SET_NOT_CONFIRMED")
    now = datetime.now(timezone.utc)
    report = Report.objects.create(
        patient=patient,
        source_revision=source_revision,
        external_ref=external_ref,
        source_organization=source_organization,
        report_status=report_status,
        sample_time_id=sample_time_id,
        result_time_id=result_time_id,
        file_group_id=candidate_set.file_group_id,
        created_at=now,
    )
    record = Record.objects.create(patient=patient, kind=Record.Kind.REPORT, current_version=1, status=Record.Status.ACTIVE, created_at=now)
    version = RecordVersion.objects.create(
        patient=patient,
        record_id=record.id,
        version=1,
        author=actor,
        origin_type=RecordVersion.OriginType.REPORT_TRANSCRIPTION,
        capture_method=RecordVersion.CaptureMethod.OCR_CONFIRMED,
        source_revision=source_revision,
        report_id=report.id,
        use_authorization_id=use_authorization_id,
        quality_status=RecordVersion.QualityStatus.PENDING,
        quality_reasons=[],
        recorded_at=now,
        retention_policy=retention_policy,
    )
    fields = confirmed_report_fields(candidate_set)
    for ordinal, field in enumerate(fields, start=1):
        RecordField.objects.create(
            patient=patient,
            record_version_id=version.id,
            ordinal=ordinal,
            field_code=field["field_code"],
            concept_code=field["concept_code"],
            value_type=field["value_type"],
            value_state=field["value_state"],
            raw_text=field["raw_text"],
            raw_numeric=Decimal(str(field["raw_numeric"])) if field.get("raw_numeric") is not None else None,
            categorical_value=field.get("categorical_value"),
            qualifier=field.get("qualifier"),
            raw_unit=field.get("raw_unit"),
            sample_time_origin=field["sample_time_origin"],
            confirmation_id=field.get("confirmation_id"),
            quality_reasons=[],
        )
    for ordinal, file_object in enumerate(FileObject.objects.filter(file_group_id=candidate_set.file_group_id, patient=patient).order_by("id"), start=1):
        RecordAttachment.objects.create(record_version_id=version.id, patient_id=patient.id, file_id=file_object.id, ordinal=ordinal)
    return {"report_id": str(report.id), "record_id": str(record.id), "record_version_id": str(version.id)}
