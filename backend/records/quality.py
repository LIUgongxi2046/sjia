from django.db import transaction
from jsonschema import Draft202012Validator, FormatChecker

from archives.inputs import REGISTRY, ROOT, SCHEMAS
from archives.models import ArchiveFact, ArchiveFactVersion
from archives.queries import fact_read
from records.models import RecordField, RecordVersion
from records.queries import QueryRejected


DATASET = SCHEMAS[(ROOT / 'data-design/dataset.schema.json').as_uri()]
FIELD_FORMAT = Draft202012Validator({'$schema': DATASET['$schema'], '$defs': DATASET['$defs'],
    '$ref': '#/$defs/clinicalField'},
    registry=REGISTRY, format_checker=FormatChecker())


@transaction.atomic
def validate_record_format(version):
    version = RecordVersion.objects.select_for_update().get(id=version.id, patient_id=version.patient_id)
    fields = list(RecordField.objects.filter(record_version_id=version.id,
        patient_id=version.patient_id).order_by('ordinal'))
    facts = {row.field_id: row for row in ArchiveFactVersion.objects.filter(
        record_version_id=version.id, patient_id=version.patient_id)}
    if not fields or set(facts) != {field.id for field in fields}:
        raise QueryRejected('RECORD_FORMAT_FACTS_INCOMPLETE')
    reasons = []
    for field in fields:
        row = facts[field.id]
        fact = ArchiveFact.objects.get(id=row.fact_id, patient_id=version.patient_id)
        clinical = fact_read(fact=fact, patient=version.patient, version=row)['field']['field']
        errors = sorted(FIELD_FORMAT.iter_errors(clinical),
            key=lambda error: (error.json_path, str(error.validator)))
        reasons.extend(f'FIELD_FORMAT_INVALID:{field.id}:{error.json_path}:{error.validator}' for error in errors)
    version.quality_status = 'needs_correction' if reasons else 'valid'
    version.quality_reasons = reasons
    version.save(update_fields=['quality_status', 'quality_reasons'])
    return version
