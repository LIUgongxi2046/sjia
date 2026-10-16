"""Actual HTTP snapshot transactions and real audit, on synthetic rows only."""
from uuid import uuid4

from django.test import TransactionTestCase
from django.utils import timezone

from audit.models import AuditEvent
from authorization.models import AccessGrant
from identity.models import Patient, Principal
from relations.models import Relation, RelationApplication
from tests.synthetic_fixtures import SyntheticEvidence, ensure_synthetic_audit_policy
from tests import test_T04


class SnapshotHTTPTransactionTests(TransactionTestCase):
    def setUp(self):
        scope = self.settings(SJIA_ENVIRONMENT=f'snapshot-test-{uuid4()}')
        scope.enable()
        self.addCleanup(scope.disable)
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id=f'SYNTHETIC-SNAPSHOT-{uuid4()}', status='active', created_at=now)
        self.audit = ensure_synthetic_audit_policy(self.actor)
        self.fixture = SyntheticEvidence(self, self.actor)
        self.synthetic = self.fixture
        self.addCleanup(self.cleanup_rows)
        self.config = self.fixture.configuration('content', test_T04.FieldConfigReadTest._payload(self))
        application = self.fixture.create(RelationApplication, version=1, applicant=self.actor, patient=self.patient,
            evidence_ref='synthetic-test-only', status='approved', checked_by=self.actor, checked_at=now, created_at=now)
        relation = self.fixture.create(Relation, patient=self.patient, guardian=self.actor, application=application,
            status='active', version=1, checked_by=self.actor, checked_at=now, valid_from=now)
        for action in ('read_raw', 'read_history', 'write_record', 'read_summary'):
            self.fixture.create(AccessGrant, patient=self.patient, principal=self.actor, relation_id=relation.id,
                action=action, field_codes=['temp', 'rash'], record_scope='all', purpose='family_observation',
                granted_by=self.actor, evidence_ref='synthetic-test-only', valid_from=now)
        session = self.client.session
        session['_sjia_principal_id'] = str(self.actor.id)
        session['_sjia_auth_epoch'] = self.actor.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()
        self.addCleanup(lambda: session.delete(session.session_key))

    def cleanup_rows(self):
        # Django flush does not enumerate schema-qualified ORM tables here.
        # Remove only exact test-owned IDs, never truncate a shared table.
        from django.db import transaction
        # Current-version back references are already DEFERRABLE in the schema.
        # Keep exact owned deletes in one transaction so cycles close at commit.
        with transaction.atomic():
            AuditEvent.objects.filter(actor_id=self.actor.id).delete()
            type(self.fixture.gate).objects.filter(pk=self.fixture.gate.pk).update(active_release=None, runtime_config=None)
            for row in reversed(self.fixture.created):
                type(row).objects.filter(pk=row.pk).delete()
            self.audit.delete()
            self.patient.delete()
            self.actor.delete()

    def _synthetic_record(self):
        from archives.models import ArchiveFact, ArchiveFactVersion
        from medical_time.models import MedicalTime
        from records.models import Record, RecordVersion, RecordField
        from records.service import create_record
        from sources.models import DataUseAuthorization
        use = self.fixture.create(DataUseAuthorization, patient=self.patient, source_revision=self.fixture.source,
            purpose='family_observation', environment=self.fixture.gate.environment,
            evidence_ref='synthetic-test-only', approved_by=self.actor, approved_at=timezone.now())
        result = create_record(patient=self.patient, actor=self.actor, kind='observation', capture_method='manual',
            origin_type='self_report', retention_policy=self.fixture.retention, use_authorization_id=use.id,
            fields=[{'field_code': code, 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': number,
                'raw_unit': 'C', 'sample_time_origin': 'observation',
                'medical_time': {'precision': 'date', 'local_date': '2026-09-15', 'resolution_status': 'local_only'}}
                for code, number in [('temp', '38.0'), ('not_permitted', '999')]])
        # Track every exact service-created row in dependency order for cleanup.
        for model in (MedicalTime, Record, RecordVersion, RecordField, ArchiveFact, ArchiveFactVersion):
            self.fixture.created.extend(model.objects.filter(patient_id=self.patient.id))
        self.patient.refresh_from_db()
        return result, RecordField.objects.get(record_version_id=result['record_version_id'], field_code='temp')

    def test_field_config_snapshot_reads_and_audits_without_outer_transaction(self):
        response = self.client.get(f'/api/patients/{self.patient.id}/field-config?kind=observation')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['state'], 'available')
        self.assertEqual([field['field_code'] for field in response.json()['fields']], ['temp', 'rash'])
        self.assertTrue(all(field['writable'] for field in response.json()['fields']))
        self.assertTrue(AuditEvent.objects.filter(actor_id=self.actor.id, action='field_config_read_view').exists())

    def test_summary_listing_keeps_its_snapshot_and_audits_empty_result(self):
        response = self.client.get(f'/api/patients/{self.patient.id}/summaries')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['summaries'], [])
        self.assertTrue(AuditEvent.objects.filter(actor_id=self.actor.id, action='summary_collection').exists())

    def test_history_uses_repeatable_read_before_permission_and_query(self):
        from django.db import connection
        _, field = self._synthetic_record()
        observations = []
        def observe(execute, sql, params, many, context):
            if 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ' in sql:
                observations.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(observe):
            response = self.client.post(f'/api/patients/{self.patient.id}/history/search', {}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()['entries']), 1)
        self.assertEqual(response.json()['entries'][0]['field_count'], 1)
        self.assertEqual(response.json()['items'][0]['sources'][0]['field_id'], str(field.id))
        self.assertEqual(response.json()['items'][0]['medical_time']['local_date'], '2026-09-15')
        self.assertEqual(len(observations), 1)
        self.assertTrue(AuditEvent.objects.filter(actor_id=self.actor.id, action='history_search_view').exists())

    def test_summary_http_reads_real_qualified_snapshot_with_field_scope(self):
        from datetime import date
        from summaries.models import SummaryItem
        from summaries.service import generate_summary
        from tasks.models import Task
        from tests.integration.helpers import activate_summary_configs
        activate_summary_configs(self)
        _, field = self._synthetic_record()
        summary = generate_summary(patient=self.patient, requested_by=self.actor,
            from_date=date(2026,9,1), to_date=date(2026,9,30), scope_timezone='Asia/Shanghai',
            include_unknown_time=False, field_codes=['temp'], environment=self.fixture.gate.environment)
        self.fixture.created.extend([Task.objects.get(id=summary.task_id), summary])
        self.fixture.created.extend(SummaryItem.objects.filter(summary_id=summary.id))
        listed = self.client.get(f'/api/patients/{self.patient.id}/summaries')
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual([row['summary_id'] for row in listed.json()['summaries']], [str(summary.id)])
        response = self.client.get(f'/api/summaries/{summary.id}')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()['needs_regeneration'])
        self.assertEqual(len(response.json()['items']), 1)
        self.assertEqual(response.json()['items'][0]['field_id'], str(field.id))
        self.assertEqual(response.json()['items'][0]['snapshot']['field']['raw_numeric'], '38.0')
        self.assertTrue(AuditEvent.objects.filter(actor_id=self.actor.id, action='summary_read').exists())


    def _record_query_snapshot(self, operation):
        from django.db import connection
        statements, state = [], {'block': None, 'starts': 0, 'settings': None}
        def observe(execute, sql, params, many, context):
            if sql == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY':
                result = execute(sql, params, many, context)
                state['starts'] += 1
                state['block'] = connection.atomic_blocks[0]
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_setting('transaction_isolation'), current_setting('transaction_read_only')")
                    state['settings'] = cursor.fetchone()
                return result
            if state['block'] is not None and state['block'] in connection.atomic_blocks:
                statements.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(observe):
            response = operation()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(state['starts'], 1)
        self.assertEqual(state['settings'], ('repeatable read', 'on'))
        # psycopg commit()/begin() need not pass through execute_wrapper; capture
        # the actual atomic block identity, excluding the separate outcome audit.
        self.assertFalse(any('FOR UPDATE' in sql or 'FOR SHARE' in sql for sql in statements))
        return response.json()

    def test_record_detail_uses_read_only_snapshot_and_field_scope(self):
        record, field = self._synthetic_record()
        data = self._record_query_snapshot(lambda: self.client.get(f"/api/records/{record['record_id']}"))
        self.assertEqual([row['field_id'] for row in data['fields']], [str(field.id)])

    def test_record_version_uses_read_only_snapshot_and_field_scope(self):
        record, field = self._synthetic_record()
        data = self._record_query_snapshot(lambda: self.client.get(f"/api/record-versions/{record['record_version_id']}"))
        self.assertEqual([row['field_id'] for row in data['fields']], [str(field.id)])

    def test_record_version_history_uses_read_only_snapshot(self):
        record, _ = self._synthetic_record()
        data = self._record_query_snapshot(lambda: self.client.get(f"/api/records/{record['record_id']}/history"))
        self.assertEqual(data['current_version'], '1')
        self.assertEqual(len(data['versions']), 1)

    def test_record_source_uses_read_only_snapshot(self):
        record, field = self._synthetic_record()
        data = self._record_query_snapshot(lambda: self.client.get(
            f"/api/record-versions/{record['record_version_id']}/fields/{field.id}/source"))
        self.assertEqual(data['field_id'], str(field.id))

    def test_record_search_uses_read_only_snapshot_and_field_scope(self):
        _, field = self._synthetic_record()
        data = self._record_query_snapshot(lambda: self.client.post(
            f'/api/patients/{self.patient.id}/records/search', {}, content_type='application/json'))
        self.assertEqual(len(data['records']), 1)
        self.assertEqual([row['field_id'] for row in data['records'][0]['detail']['fields']], [str(field.id)])

    def test_concurrent_revoke_is_coherent_and_applies_to_next_record_query(self):
        from threading import Thread
        from django.db import connection, connections
        record, field = self._synthetic_record()
        state, errors = {'snapshot': False, 'revoked': False}, []
        def revoke():
            try:
                AccessGrant.objects.filter(principal=self.actor, patient=self.patient,
                    action='read_raw').update(revoked_at=timezone.now())
            except Exception as error:
                errors.append(error)
            finally:
                connections['default'].close()
        def observe(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if sql == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY':
                state['snapshot'] = True
            elif (state['snapshot'] and not state['revoked'] and sql.startswith('SELECT')
                    and Principal._meta.db_table in sql):
                state['revoked'] = True
                thread = Thread(target=revoke)
                thread.start()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive(), 'revocation blocked by a pure-read query lock')
                self.assertEqual(errors, [])
            return result
        with connection.execute_wrapper(observe):
            response = self.client.get(f"/api/records/{record['record_id']}")
        self.assertTrue(state['revoked'])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row['field_id'] for row in response.json()['fields']], [str(field.id)])
        denied = self.client.get(f"/api/records/{record['record_id']}")
        self.assertEqual(denied.status_code, 403, denied.content)
