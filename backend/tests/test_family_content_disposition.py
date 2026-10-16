"""Native synthetic request bridge: family request != approval or execution."""
import json
from uuid import uuid4
from django.conf import settings
from django.db import IntegrityError,transaction
from django.test import RequestFactory,TransactionTestCase
from django.utils import timezone
from identity.models import Principal
from authorization.models import AccessGrant
from relations.models import Relation,RelationApplication
from retention.content_engine import preview_plan
from retention.content_family import create_family_request
from retention.models import ContentDispositionFamilyRequest,ContentDispositionPlan,ContentErasure
from retention.disposition import DispositionRejected
from tests import test_content_disposition_engine as fixtures


class FamilyContentDispositionNativeTests(TransactionTestCase):
    def setUp(self):
        self.f=fixtures.ContentDispositionNativeTests()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        f=self.f
        self.family=Principal.objects.create(account_status='active',created_at=f.now,updated_at=f.now)
        application=RelationApplication.objects.create(applicant=self.family,patient=f.patient,version=1,
            evidence_ref=f.evidence.reference,status='approved',checked_by=f.actor,checked_at=f.now,created_at=f.now)
        self.relation=Relation.objects.create(patient=f.patient,guardian=self.family,application=application,
            status='active',version=1,checked_by=f.actor,checked_at=f.now,valid_from=f.now)
        self.grants={}
        for action in ('request_disposition','read_history'):
            self.grants[action]=AccessGrant.objects.create(principal=self.family,patient=f.patient,relation_id=self.relation.id,
                action=action,field_codes=[],record_scope='all',purpose='family_observation',granted_by=f.actor,
                evidence_ref=f.evidence.reference,valid_from=f.now)
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        ensure_synthetic_audit_policy(f.actor,case=f)

    def body(self,policy=None):
        f=self.f
        return {'schema_version':'content-disposition-family-request-1','request_id':str(uuid4()),
            'policy_id':str((policy or f.policy()).id),'targets':[{'record_version_id':str(row.id),'version':str(row.version)} for row in [f.first,f.second]]}

    def create(self,body):
        return create_family_request(actor=self.family,auth_epoch=self.family.auth_epoch,patient_id=self.f.patient.id,data=body)

    def plan(self,row,*,roots=None):
        f=self.f
        return preview_plan(actor=f.actor,auth_epoch=f.actor.auth_epoch,patient_id=f.patient.id,policy_id=row.policy_id,
            family_request_id=row.id,data={'schema_version':'content-disposition-request-1',
                'targets':roots or [f.target(f.first),f.target(f.second)],'hold_id':str(f.hold.id),'hold_version':1})

    def http(self,view,*,method='GET',actor=None,audience='family',data=None,**route):
        from django.middleware.csrf import _get_new_csrf_string,CSRF_SESSION_KEY
        actor=actor or self.family
        csrf=_get_new_csrf_string()
        if method=='GET':request=RequestFactory().get('/synthetic-content-request')
        else:request=RequestFactory().post('/synthetic-content-request',data=json.dumps(data),content_type='application/json',HTTP_X_CSRFTOKEN=csrf)
        request.COOKIES[settings.CSRF_COOKIE_NAME]=csrf
        request.session={'_sjia_principal_id':str(actor.id),'_sjia_auth_epoch':actor.auth_epoch,'_sjia_audience':audience}
        if settings.CSRF_USE_SESSIONS:request.session[CSRF_SESSION_KEY]=csrf
        return view(request,**route)

    def test_http_family_request_support_link_execution_and_limited_reread(self):
        from api.views_family_content_disposition import family_content_disposition_requests,family_content_disposition_read
        from api.views_content_disposition import content_disposition_family_requests,content_disposition_preview
        from retention.content_schemas import ContentFamilyRequestView
        body=self.body()
        response=self.http(family_content_disposition_requests,method='POST',data=body,patient_id=self.f.patient.id)
        self.assertEqual(response.status_code,201,response.data)
        ContentFamilyRequestView.model_validate(response.data)
        self.assertEqual(response.data['client_request_id'],body['request_id'])
        self.assertEqual(response.data['status'],'requested')
        self.assertFalse(ContentErasure.objects.exists())
        row=ContentDispositionFamilyRequest.objects.get(id=response.data['request_id'])
        support=self.http(content_disposition_family_requests,actor=self.f.actor,audience='support',patient_id=self.f.patient.id)
        self.assertEqual(support.status_code,200,support.data)
        self.assertEqual(support.data['requests'][0]['request_id'],str(row.id))
        f=self.f
        preview=self.http(content_disposition_preview,method='POST',actor=f.actor,audience='support',
            patient_id=f.patient.id,data={'policy_id':str(row.policy_id),'family_request_id':str(row.id),
            'request':{'schema_version':'content-disposition-request-1','targets':[f.target(f.first),f.target(f.second)],
                'hold_id':str(f.hold.id),'hold_version':1}})
        self.assertEqual(preview.status_code,201,preview.data)
        plan=ContentDispositionPlan.objects.get(id=preview.data['plan_id'])
        self.assertEqual(plan.manifest['family_request_id'],str(row.id))
        self.assertEqual(plan.family_request_id,row.id)
        self.f.execute(self.f.approve(plan))
        read=self.http(family_content_disposition_read,request_id=row.id)
        self.assertEqual(read.status_code,200,read.data)
        self.assertTrue(read.data['logical_payload_erased'])
        self.assertFalse(read.data['all_copies_erased'])
        self.assertEqual(read.data['status'],'logical_erasure_complete')
        self.assertEqual(read.data['targets'],body['targets'])
        self.assertNotIn('manifest',read.data)
        self.assertNotIn('items',read.data)
        self.assertNotIn(fixtures.CANARY,json.dumps(read.data))
        duplicate=self.create(body)
        self.assertEqual(duplicate.id,row.id)
        self.assertEqual(ContentDispositionFamilyRequest.objects.count(),1)

    def test_no_policy_default_and_only_authorized_record_versions_are_listed(self):
        from api.views_family_content_disposition import family_content_disposition_options
        response=self.http(family_content_disposition_options,patient_id=self.f.patient.id)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['policies'],[])
        self.assertIsNone(response.data['default_policy'])
        self.assertTrue(any(row['record_version_id']==str(self.f.first.id) for row in response.data['targets']))
        self.assertNotIn('adapter',json.dumps(response.data))
        self.assertNotIn(fixtures.CANARY,json.dumps(response.data))
        self.grants['read_history'].field_codes=['synthetic_measurement'];self.grants['read_history'].save(update_fields=['field_codes'])
        denied_scope=self.http(family_content_disposition_options,patient_id=self.f.patient.id)
        self.assertEqual(denied_scope.status_code,200,denied_scope.data)
        self.assertEqual(denied_scope.data['targets'],[])

    def test_exact_idempotency_and_immutable_request_scope(self):
        body=self.body();row=self.create(body)
        self.assertEqual(self.create(body).id,row.id)
        self.f.assert_rejected('CONTENT_FAMILY_REQUEST_CHANGED',self.create,{**body,'targets':body['targets'][:1]})
        with self.assertRaises(IntegrityError),transaction.atomic():
            ContentDispositionFamilyRequest.objects.filter(id=row.id).update(targets=[])
        self.assertEqual(ContentDispositionFamilyRequest.objects.count(),1)

    def test_cross_patient_account_unreadable_and_wrong_version_are_denied(self):
        from api.views_family_content_disposition import family_content_disposition_requests,family_content_disposition_read
        body=self.body();row=self.create(body)
        other=Principal.objects.create(account_status='active',created_at=self.f.now,updated_at=self.f.now)
        hidden=self.http(family_content_disposition_read,actor=other,request_id=row.id)
        self.assertEqual(hidden.status_code,404,hidden.data)
        cross=self.http(family_content_disposition_requests,method='POST',data=body,patient_id=self.f.other.id)
        self.assertEqual(cross.status_code,403,cross.data)
        wrong={**body,'request_id':str(uuid4()),'targets':[{'record_version_id':str(self.f.first.id),'version':'999'}]}
        self.f.assert_rejected('CONTENT_VERSION_CONFLICT',self.create,wrong)
        self.grants['read_history'].revoked_at=timezone.now();self.grants['read_history'].save(update_fields=['revoked_at'])
        from authorization.policy import Denied
        with self.assertRaises(Denied):self.create({**body,'request_id':str(uuid4())})
        self.assertEqual(ContentDispositionFamilyRequest.objects.count(),1)

    def test_support_cannot_expand_or_replace_family_roots(self):
        row=self.create(self.body())
        self.f.assert_rejected('CONTENT_FAMILY_REQUEST_SCOPE_CHANGED',self.plan,row,roots=[self.f.target(self.f.first)])
        plan=self.plan(row)
        self.f.assert_rejected('CONTENT_FAMILY_PLAN_ALREADY_EXISTS',self.plan,row)
        self.assertEqual(ContentDispositionPlan.objects.count(),1)
        self.assertEqual(plan.manifest['roots'],[self.f.target(self.f.first),self.f.target(self.f.second)])

    def test_family_permission_revocation_after_approval_blocks_execution(self):
        row=self.create(self.body());plan=self.f.approve(self.plan(row))
        self.grants['request_disposition'].revoked_at=timezone.now();self.grants['request_disposition'].save(update_fields=['revoked_at'])
        from authorization.policy import Denied
        with self.assertRaises(Denied):self.f.execute(plan)
        self.assertFalse(ContentErasure.objects.exists())
        self.assertTrue(self.f.store.get(self.f.original.object_key))

    def test_family_cannot_approve_or_execute_even_a_known_linked_plan(self):
        from api.views_content_disposition import content_disposition_approve,content_disposition_execute
        row=self.create(self.body());plan=self.plan(row)
        for view,extra in [(content_disposition_approve,{'source_revision_id':str(self.f.evidence.source.id)}),
                (content_disposition_execute,{})]:
            response=self.http(view,method='POST',plan_id=plan.id,data={'expected_manifest_sha256':plan.manifest_sha256,**extra})
            self.assertEqual(response.status_code,403,response.data)
            self.assertEqual(response.data['code'],'SUPPORT_AUDIENCE_REQUIRED')
        self.assertFalse(ContentErasure.objects.exists())

    def test_later_correction_is_never_implicitly_added_to_requested_versions(self):
        from records.models import Record,RecordVersion
        f=self.f
        row=self.create(self.body())
        newer=RecordVersion.objects.create(patient=f.patient,record_id=f.record.id,version=3,supersedes_version=2,
            author=f.actor,origin_type='self_report',capture_method='manual',source_revision=f.evidence.source,
            source_organization='SYNTHETIC_NEW_VERSION_MUST_SURVIVE',use_authorization_id=f.authorization.id,
            quality_status='valid',quality_reasons=[],correction_reason='SYNTHETIC_NEW_CORRECTION',
            corrected_at=f.now,recorded_at=f.now,retention_policy=f.evidence.retention)
        Record.objects.filter(id=f.record.id).update(current_version=3)
        plan=self.plan(row)
        self.assertNotIn(str(newer.id),[item.get('object_id') for item in plan.manifest['items']])
        f.execute(f.approve(plan))
        newer.refresh_from_db()
        self.assertIsNone(newer.content_erased_at)
        self.assertEqual(newer.source_organization,'SYNTHETIC_NEW_VERSION_MUST_SURVIVE')
        self.assertEqual(Record.objects.get(id=f.record.id).current_version,3)


    def test_assigned_record_scope_can_request_only_its_exact_versions(self):
        from api.views_family_content_disposition import family_content_disposition_options
        for grant in self.grants.values():
            grant.record_scope='assigned';grant.assigned_record_id=self.f.record.id
            grant.save(update_fields=['record_scope','assigned_record_id'])
        result=self.http(family_content_disposition_options,patient_id=self.f.patient.id)
        self.assertEqual(result.status_code,200,result.data)
        self.assertTrue(result.data['targets'])
        self.assertTrue(all(row['record_id']==str(self.f.record.id) for row in result.data['targets']))
        self.assertIsNotNone(self.create(self.body()).id)


    def test_logout_relogin_does_not_cancel_persisted_application_or_bypass_current_grants(self):
        from api.views_family_content_disposition import family_content_disposition_read
        from django.db.models import F
        body=self.body();row=self.create(body)
        original_epoch=row.requested_auth_epoch
        Principal.objects.filter(id=self.family.id).update(auth_epoch=F('auth_epoch')+1)
        self.family.refresh_from_db()
        self.assertNotEqual(self.family.auth_epoch,original_epoch)
        read=self.http(family_content_disposition_read,request_id=row.id)
        self.assertEqual(read.status_code,200,read.data)
        self.assertEqual(read.data['status'],'requested')
        self.assertEqual(self.create(body).id,row.id)
        plan=self.f.approve(self.plan(row))
        self.f.execute(plan)
        completed=self.http(family_content_disposition_read,request_id=row.id)
        self.assertEqual(completed.status_code,200,completed.data)
        self.assertTrue(completed.data['logical_payload_erased'])
        row.refresh_from_db()
        self.assertEqual(row.requested_auth_epoch,original_epoch)

    def current_only_grant(self):
        self.grants['read_history'].revoked_at=timezone.now()
        self.grants['read_history'].save(update_fields=['revoked_at'])
        return AccessGrant.objects.create(principal=self.family,patient=self.f.patient,relation_id=self.relation.id,
            action='read_raw',field_codes=[],record_scope='all',purpose='family_observation',granted_by=self.f.actor,
            evidence_ref=self.f.evidence.reference,valid_from=self.f.now)

    def test_current_version_can_be_requested_with_read_raw_without_history_but_old_version_cannot(self):
        from api.views_family_content_disposition import (family_content_disposition_options,
            family_content_disposition_requests,family_content_disposition_read)
        grant=self.current_only_grant()
        body=self.body();body['targets']=body['targets'][1:]
        options=self.http(family_content_disposition_options,patient_id=self.f.patient.id)
        self.assertEqual(options.status_code,200,options.data)
        self.assertEqual([row['record_version_id'] for row in options.data['targets']],[str(self.f.second.id)])
        created=self.http(family_content_disposition_requests,method='POST',patient_id=self.f.patient.id,data=body)
        self.assertEqual(created.status_code,201,created.data)
        recovered=self.http(family_content_disposition_read,request_id=created.data['request_id'])
        self.assertEqual(recovered.status_code,200,recovered.data)
        self.assertEqual(recovered.data['targets'],body['targets'])
        old={**body,'request_id':str(uuid4()),'targets':[{'record_version_id':str(self.f.first.id),'version':'1'}]}
        denied=self.http(family_content_disposition_requests,method='POST',patient_id=self.f.patient.id,data=old)
        self.assertEqual(denied.status_code,403,denied.data)
        self.assertEqual(ContentDispositionFamilyRequest.objects.count(),1)
        # A field-scoped read does not grant whole-version deletion scope.
        grant.field_codes=['synthetic_measurement'];grant.save(update_fields=['field_codes'])
        partial=self.http(family_content_disposition_requests,method='POST',patient_id=self.f.patient.id,
            data={**body,'request_id':str(uuid4())})
        self.assertEqual(partial.status_code,403,partial.data)
        self.assertEqual(ContentDispositionFamilyRequest.objects.count(),1)

    def test_current_only_request_cannot_process_the_selected_version_after_it_becomes_history(self):
        from records.models import Record,RecordVersion
        from authorization.policy import Denied
        self.current_only_grant()
        body=self.body();body['targets']=body['targets'][1:]
        row=self.create(body)
        f=self.f
        newer=RecordVersion.objects.create(patient=f.patient,record_id=f.record.id,version=3,supersedes_version=2,
            author=f.actor,origin_type='self_report',capture_method='manual',source_revision=f.evidence.source,
            source_organization='SYNTHETIC_CURRENT_PERMISSION_RECHECK',use_authorization_id=f.authorization.id,
            quality_status='valid',quality_reasons=[],correction_reason='SYNTHETIC_CORRECTION',
            corrected_at=f.now,recorded_at=f.now,retention_policy=f.evidence.retention)
        Record.objects.filter(id=f.record.id).update(current_version=3)
        with self.assertRaises(Denied):
            self.plan(row,roots=[f.target(f.second)])
        self.assertFalse(ContentDispositionPlan.objects.exists())
        self.assertFalse(ContentErasure.objects.exists())
        self.assertTrue(RecordVersion.objects.filter(id__in=[f.second.id,newer.id]).count()==2)
