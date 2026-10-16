"""Shared domain writer after patient/source/reference admission.

Callers must validate and remap every input reference before calling. This
writer deliberately grants no verification, clinical eligibility, or file
availability. All effects participate in the caller's transaction.
"""
from decimal import Decimal
from uuid import UUID
from records.models import Report
from case_details.import_admission import CaseImportRejected
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


def import_domain_entities(*, case, patient, source_revision, original_files, now, existing_report_ids=()):
    # Existing authorized originals are bound explicitly. A manifest alone is
    # never sufficient evidence that bytes are in object storage.
    for item in case["imaging_studies"]:
        groups = {original_files[link['file_id']].file_group_id
            for link in case['image_links'] if link['study_id'] == item['id']}
        if len(groups) > 1:
            raise CaseImportRejected('CASE_REPORT_GROUP_AMBIGUOUS')
        if UUID(item['report_id']) in existing_report_ids:
            Report.objects.get(id=item['report_id'], patient=patient, source_revision=source_revision)
            continue
        Report.objects.create(
            id=UUID(item["report_id"]), patient=patient, source_revision=source_revision,
            report_status=Report.ReportStatus.UNKNOWN,
            file_group_id=next(iter(groups)) if groups else None, created_at=now,
        )


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
                statement_id=statement.id, patient_id=patient.id, evidence_field_id=UUID(evidence["evidence_field_id"]),
                evidence_record_version_id=UUID(evidence["evidence_record_version_id"]), role=evidence["role"],
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
            source_field_id=UUID(item["source_field_id"]), file_id=original_files[item['file_id']].id, study_id=UUID(item["study_id"]) if item["study_id"] else None,
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
                file_id=original_files[reference['file_id']].id if reference["file_id"] else None, role=reference["role"],
            )

