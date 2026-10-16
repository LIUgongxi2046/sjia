from copy import deepcopy
from uuid import uuid4

from django.test import SimpleTestCase

from records.input_validation import validate_family_record_input
from records.service import RecordRejected


class RecordInputCloseoutTest(SimpleTestCase):
    def setUp(self):
        self.payload = {'kind': 'observation', 'capture_method': 'manual',
            'field_config_version': str(uuid4()), 'fields': [{'field_code': 'temperature',
                'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '0',
                'raw_unit': 'C', 'sample_time_origin': 'observation'}]}

    def test_current_wire_contract_preserves_zero_and_optional_unknowns(self):
        self.assertEqual(validate_family_record_input(deepcopy(self.payload)), self.payload)
        self.payload['fields'][0].update(value_state='unknown', raw_numeric=None)
        self.assertEqual(validate_family_record_input(deepcopy(self.payload)), self.payload)

    def test_authority_and_unknown_top_level_keys_cannot_be_silently_ignored(self):
        for key in ('patient_id', 'author_id', 'verifier_id', 'verification_status', 'quality_status',
            'approved_by', 'use_authorization_id', 'source_revision_id', 'extra'):
            with self.subTest(key=key), self.assertRaisesRegex(RecordRejected, 'RECORD_INPUT_INVALID'):
                validate_family_record_input({**self.payload, key: str(uuid4())})

    def test_server_generated_provenance_is_not_caller_assignable(self):
        for key in ('confirmation_id', 'anchor_id', 'verification_status', 'author_id', 'field_id'):
            value = deepcopy(self.payload)
            value['fields'][0][key] = str(uuid4())
            with self.subTest(key=key), self.assertRaisesRegex(RecordRejected, 'RECORD_FIELD_INPUT_INVALID'):
                validate_family_record_input(value)

    def test_candidate_identity_requires_specific_candidate_set(self):
        self.payload['fields'][0]['candidate_field_id'] = str(uuid4())
        with self.assertRaisesRegex(RecordRejected, 'CANDIDATE_SET_REQUIRED'):
            validate_family_record_input(self.payload)
        self.payload['candidate_set_id'] = str(uuid4())
        self.assertEqual(validate_family_record_input(self.payload), self.payload)

    def test_invalid_nonfinite_or_boolean_decimal_is_structured_rejection(self):
        for value in ('not-a-number', 'NaN', 'Infinity', True, {}, []):
            self.payload['fields'][0]['raw_numeric'] = value
            with self.subTest(value=value), self.assertRaisesRegex(RecordRejected, 'FIELD_DECIMAL_INVALID'):
                validate_family_record_input(self.payload)

    def test_attachments_and_draft_versions_have_explicit_shape(self):
        for extra in ({'file_ids': 'wrong'}, {'file_ids': ['not-uuid']},
            {'draft_id': str(uuid4()), 'draft_version': True}, {'draft_version': '1'},
            {'report': {'approved_by': str(uuid4())}}):
            with self.subTest(extra=extra), self.assertRaises(RecordRejected):
                validate_family_record_input({**self.payload, **extra})

    def test_malformed_field_and_text_shapes_are_rejected_before_authorization(self):
        for key, value in (('field_code', []), ('raw_text', {}), ('medical_time', []),
                ('sample_time_origin', 'unrecognized'), ('qualifier', 'approximately')):
            data = deepcopy(self.payload)
            data['fields'][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(RecordRejected, 'RECORD_FIELD_INPUT_INVALID'):
                validate_family_record_input(data)
        with self.assertRaisesRegex(RecordRejected, 'RECORD_INPUT_INVALID'):
            validate_family_record_input({**self.payload, 'source_organization': {}})

    def test_medical_times_reject_fabricated_server_fields_and_invalid_precision(self):
        valid = {'precision': 'year', 'local_year': 2020, 'resolution_status': 'local_only'}
        self.payload['fields'][0]['medical_time'] = valid
        self.assertEqual(validate_family_record_input(self.payload), self.payload)
        for time in ({**valid, 'id': str(uuid4())}, {**valid, 'instant': '2020-01-01T00:00:00Z'},
                {**valid, 'timezone_name': 'UTC'}, {'precision': 'date', 'local_date': '2026-02-31', 'resolution_status': 'local_only'},
                {'precision': 'minute', 'local_datetime': '2026-01-01T01:20:30', 'resolution_status': 'local_only'},
                {'precision': 'date', 'local_date': '2026-01-01', 'timezone_name': 'Not/AZone', 'resolution_status': 'local_only'}):
            self.payload['fields'][0]['medical_time'] = time
            with self.subTest(time=time), self.assertRaisesRegex(RecordRejected, 'MEDICAL_TIME_INPUT_INVALID'):
                validate_family_record_input(self.payload)

    def test_report_time_and_numeric_wire_types_are_strict(self):
        for value in (1, 1.5, '1e2', '+1', '01'):
            self.payload['fields'][0]['raw_numeric'] = value
            with self.subTest(value=value), self.assertRaisesRegex(RecordRejected, 'FIELD_DECIMAL_INVALID'):
                validate_family_record_input(self.payload)
        self.payload['fields'][0]['raw_numeric'] = '0'
        self.payload['report'] = {'sample_time': {'precision': 'unknown', 'resolution_status': 'resolved'}}
        with self.assertRaisesRegex(RecordRejected, 'MEDICAL_TIME_INPUT_INVALID'):
            validate_family_record_input(self.payload)


class CorrectionTimeInputTests(SimpleTestCase):
    def test_changed_time_cannot_claim_server_resolution(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from records.correction import CorrectionRejected, medical_time_for_correction
        previous = SimpleNamespace(medical_time_id=None)
        with patch('records.correction.medical_time_view', return_value=None), patch('records.correction._create_medical_time') as create:
            with self.assertRaisesRegex(CorrectionRejected, 'MEDICAL_TIME_INPUT_INVALID'):
                medical_time_for_correction(object(), previous, {'medical_time': {'precision': 'minute',
                    'local_datetime': '2026-01-01T01:00:00', 'timezone_name': 'UTC',
                    'instant': '2026-01-01T01:00:00Z', 'resolution_status': 'resolved'}})
            create.assert_not_called()

    def test_unchanged_server_projection_reuses_exact_saved_time(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from records.correction import medical_time_for_correction
        identity, patient, saved = uuid4(), object(), object()
        value = {'precision': 'minute', 'resolution_status': 'resolved', 'instant': '2026-01-01T01:00:00Z'}
        with patch('records.correction.medical_time_view', return_value=value), patch('records.correction.MedicalTime.objects.get', return_value=saved) as read:
            self.assertIs(medical_time_for_correction(patient, SimpleNamespace(medical_time_id=identity), {'medical_time': deepcopy(value)}), saved)
            read.assert_called_once_with(id=identity, patient=patient)

    def test_changed_valid_local_time_is_resolved_by_existing_writer(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from records.correction import medical_time_for_correction
        value = {'precision': 'year', 'local_year': 2026, 'resolution_status': 'local_only'}
        patient, saved = object(), object()
        with patch('records.correction.medical_time_view', return_value=None), patch('records.correction._create_medical_time', return_value=saved) as create:
            self.assertIs(medical_time_for_correction(patient, SimpleNamespace(medical_time_id=None), {'medical_time': value}), saved)
            create.assert_called_once_with(patient, value)
