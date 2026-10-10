import json
import os
import unittest

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django

django.setup()

from django.db import transaction
from django.utils import timezone

from archives.models import EvaluationDatasetVersion, EvaluationLabel, EvaluationPatientPartition, EvaluationRun
from case_details.evaluation_evidence import approved_evaluation_artifact
from case_details.evaluation_objects import medical_time_proof, object_digest
from case_details.evaluation_units import EvaluationUnitsRejected
from medical_time.models import MedicalTime
from sources.models import SourceRegistry, SourceRevision


class EvaluationSourceRuntimeTest(unittest.TestCase):
    def test_actual_medical_times_have_exact_content_and_patient_proofs(self):
        with transaction.atomic():
            values = list(MedicalTime.objects.order_by('id'))
            self.assertTrue(values, '需要已有医疗时间才能执行当前核查。')
            before = {str(value.id): object_digest(value) for value in values}
            cutoff = timezone.now()
            for value in values:
                with self.subTest(time_id=str(value.id)):
                    if value.instant is not None and value.instant > cutoff:
                        with self.assertRaisesRegex(EvaluationUnitsRejected, '^UNIT_FUTURE_INFORMATION$'):
                            medical_time_proof(value.id, value.patient_id, cutoff)
                    else:
                        proof = medical_time_proof(value.id, value.patient_id, cutoff)
                        self.assertEqual(proof['time_id'], str(value.id))
                        self.assertEqual(proof['patient_id'], str(value.patient_id))
                        self.assertEqual(proof['sha256'], before[str(value.id)])
                        self.assertEqual(proof['precision'], value.precision)
                        self.assertEqual(proof['resolution_status'], value.resolution_status)
            self.assertEqual(before, {str(value.id): object_digest(value) for value in MedicalTime.objects.order_by('id')})
            print(json.dumps({'medical_times_checked': len(values),
                'resolved_instants': sum(value.resolution_status == 'resolved' and value.instant is not None for value in values),
                'future_instants': sum(value.instant is not None and value.instant > cutoff for value in values)}, sort_keys=True))

    def test_current_sources_without_evaluation_approval_are_refused(self):
        with transaction.atomic():
            sources_before = list(SourceRegistry.objects.order_by('id').values())
            revisions_before = list(SourceRevision.objects.order_by('id').values())
            digest_before = {str(row.id): object_digest(row) for row in SourceRevision.objects.order_by('id')}
            self.assertTrue(revisions_before, '需要现有来源登记才能执行当前核查。')
            checked = 0
            for revision in SourceRevision.objects.order_by('id'):
                if 'evaluation_protocol' in revision.allowed_use:
                    continue
                with self.subTest(revision_id=str(revision.id)):
                    with self.assertRaises(EvaluationUnitsRejected):
                        approved_evaluation_artifact(f'urn:sjia:source-revision:{revision.id}',
                            kind='evaluation_protocol', purpose='clinical_evaluation')
                    checked += 1
            self.assertGreater(checked, 0)
            self.assertEqual(sources_before, list(SourceRegistry.objects.order_by('id').values()))
            self.assertEqual(revisions_before, list(SourceRevision.objects.order_by('id').values()))
            self.assertEqual(digest_before, {str(row.id): object_digest(row) for row in SourceRevision.objects.order_by('id')})

    def test_actual_evaluation_catalog_has_no_registered_business_objects(self):
        with transaction.atomic():
            for model in (EvaluationDatasetVersion, EvaluationPatientPartition, EvaluationLabel, EvaluationRun):
                with self.subTest(table=model._meta.db_table):
                    self.assertEqual(model.objects.count(), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
