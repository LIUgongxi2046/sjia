import argparse
import json
from datetime import datetime, timezone

from tests.verify_v2_migration import ROOT, require
from django.conf import settings
from django.db import connection, transaction
from jsonschema import Draft202012Validator, FormatChecker
from api.family_home import home_projection
from authorization.models import AccessGrant
from alerting.models import AlertEvent
from family_content.alert_detail import alert_content
from family_content.models import EnvironmentGate
from family_content.service import release_available
from records.models import RecordField
from drafts.models import Draft
from drafts.service import list_restorable_drafts
from api.views_family2 import _draft_view
from relations.models import Relation, RelationApplication
from relations.queries import relation_list_read, relation_view, application_list_read, application_view
from relations.application_requests import application_request_view
from requests.models import WriteIntent
from requests.service import request_recent_account
from identity.models import Principal
from django.urls import resolve, reverse


arguments = argparse.ArgumentParser()
arguments.add_argument('--frontend-json', action='store_true')
arguments.add_argument('--route-json', action='store_true')
options = arguments.parse_args()
schema = json.loads((ROOT / 'backend/api/family-read.schema.json').read_text())
Draft202012Validator.check_schema(schema)
validators = {name: Draft202012Validator({**schema, '$ref': f'#/$defs/{name}'}, format_checker=FormatChecker())
              for name in ['HomeView', 'AlertProjection', 'DraftView', 'DraftList']}
relation_schema = json.loads((ROOT / 'backend/relations/read.schema.json').read_text())
Draft202012Validator.check_schema(relation_schema)
validators.update({name: Draft202012Validator({**relation_schema, '$ref': f'#/$defs/{name}'}, format_checker=FormatChecker())
    for name in ['RelationList', 'RelationView', 'ApplicationList', 'ApplicationView', 'ApplicationRequestView', 'ApplicationRequestList']})
responses, checks = [], []


def validate(name, value):
    errors = list(validators[name].iter_errors(value))
    diagnostics = '; '.join(
        '/'.join(str(part) for part in detail.absolute_path) + ':' + detail.validator
        for error in errors[:10] for detail in [error, *error.context])
    require(not errors, '家庭返回资料未通过 Schema：' + diagnostics)
    if options.frontend_json:
        responses.append({'schema': name, 'response': value})


with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    gate = EnvironmentGate.objects.filter(environment=settings.SJIA_ENVIRONMENT).select_related('active_release__contact_version').first()
    available = bool(gate and gate.enabled and gate.active_release and release_available(gate.active_release))
    grants = AccessGrant.objects.filter(action='read_raw').select_related('patient', 'principal').order_by('id')
    for grant in grants:
        value = home_projection(patient=grant.patient, principal=grant.principal, field_codes=grant.field_codes, environment=settings.SJIA_ENVIRONMENT)
        validate('HomeView', value)
        require(value['service_state'] == ('enabled' if available else 'disabled'), '服务状态与当前运行环境不一致。')
        if not available:
            require(value['assessment_state'] == 'configuration_pending' and value['assessed_at'] is None
                    and value['current_alert_id'] is None and value['read_state'] == 'not_applicable', '未启用状态返回了可采用的评估资料。')
        checks.append({'scope': 'EXISTING_GRANT_METADATA', 'service_state': value['service_state'], 'data_state': value['data_state'],
                       'processing_state': value['processing_state'], 'read_state': value['read_state'], 'schema': 'PASS'})
    event_count = 0
    for event in AlertEvent.objects.select_related('patient', 'release').order_by('id'):
        field_codes = list(RecordField.objects.filter(patient=event.patient).values_list('field_code', flat=True).distinct())
        content = alert_content(event, field_codes)
        value = content
        validate('AlertProjection', value)
        if not available:
            require(value['validity'] == 'invalid' and value['content'] is None, '未启用状态返回了获准提示正文。')
        event_count += 1
    require(checks, '当前数据库缺少可读取的授权范围元数据。')
    draft_count, draft_scope_count, restorable_count = 0, 0, 0
    draft_statuses, seen_scopes = {}, set()
    for draft in Draft.objects.select_related('owner', 'patient').order_by('id'):
        validate('DraftView', _draft_view(draft))
        draft_count += 1
        draft_statuses[draft.status] = draft_statuses.get(draft.status, 0) + 1
        scope = (draft.owner_id, draft.patient_id)
        if scope in seen_scopes:
            continue
        seen_scopes.add(scope)
        restorable = list(list_restorable_drafts(owner=draft.owner, patient=draft.patient))
        value = {'drafts': [_draft_view(item) for item in restorable]}
        validate('DraftList', value)
        require(all(item.owner_id == draft.owner_id and item.patient_id == draft.patient_id
                    and item.status == 'editing' and (item.expires_at is None or item.expires_at > datetime.now(timezone.utc))
                    for item in restorable), '可恢复草稿不符合本人、患儿、保存状态或有效期条件。')
        draft_scope_count += 1
        restorable_count += len(restorable)
    relation_count, application_count, guardian_scopes, applicant_scopes = 0, 0, set(), set()
    relation_statuses, application_statuses = {}, {}
    route_identity = {'relation_id': None, 'application_id': None, 'intent_id': None}
    for relation in Relation.objects.select_related('guardian', 'patient').order_by('id'):
        value = relation_view(relation, relation.guardian, detail=True)
        validate('RelationView', value)
        require(value['relation_id'] == str(relation.id) and value['patient_id'] == str(relation.patient_id)
                and value['auth_epoch'] == str(relation.auth_epoch), '关联读取对象、患儿或授权版本与保存记录不一致。')
        now = datetime.now(timezone.utc)
        if relation.status != 'active' or relation.valid_from > now or (relation.valid_until and relation.valid_until <= now):
            require(not value['actions'], '当前无效关联返回了可用授权动作。')
        relation_count += 1
        relation_statuses[relation.status] = relation_statuses.get(relation.status, 0) + 1
        if route_identity['relation_id'] is None:
            route_identity['relation_id'] = str(relation.id)
        if relation.guardian_id not in guardian_scopes:
            guardian_scopes.add(relation.guardian_id)
            listed = relation_list_read(relation.guardian)
            validate('RelationList', listed)
            expected = set(str(value) for value in Relation.objects.filter(guardian_id=relation.guardian_id).values_list('id', flat=True))
            require(set(value['relation_id'] for value in listed['relations']) == expected,
                    '本人关联列表与当前人员的保存范围不一致。')
    for application in RelationApplication.objects.select_related('applicant').order_by('id'):
        value = application_view(application, application.applicant, detail=True)
        validate('ApplicationView', value)
        require(value['application_id'] == str(application.id) and value['version'] == str(application.version)
                and value['evidence_ref'] == application.evidence_ref and value['note'] == application.note
                and value['requested_access'] == application.requested_access, '本人申请详情与当前保存内容不一致。')
        application_count += 1
        application_statuses[application.status] = application_statuses.get(application.status, 0) + 1
        if route_identity['application_id'] is None:
            route_identity['application_id'] = str(application.id)
        if application.applicant_id not in applicant_scopes:
            applicant_scopes.add(application.applicant_id)
            listed = application_list_read(application.applicant)
            validate('ApplicationList', listed)
            expected = set(str(value) for value in RelationApplication.objects.filter(applicant_id=application.applicant_id).values_list('id', flat=True))
            require(set(value['application_id'] for value in listed['applications']) == expected,
                    '本人申请列表与当前人员的保存范围不一致。')
    request_count, request_scope_count = 0, 0
    for principal in Principal.objects.order_by('id'):
        intents = list(request_recent_account(principal.id, ['application_create', 'application_withdraw']))
        value = {'requests': [application_request_view(intent) for intent in intents]}
        validate('ApplicationRequestList', value)
        require(all(intent.actor_id == principal.id and intent.patient_id is None for intent in intents), '申请保存请求超出本人范围。')
        request_scope_count += 1
        for item in value['requests']:
            validate('ApplicationRequestView', item)
            request_count += 1
    if options.route_json:
        intent_id = WriteIntent.objects.order_by('id').values_list('id', flat=True).first()
        require(intent_id is not None, '当前数据库缺少可用于匿名请求检查的保存意图。')
        route_identity['intent_id'] = str(intent_id)
        require(route_identity['application_id'] is not None, '当前数据库缺少可用于匿名申请接口检查的对象。')
    require(reverse('application_create') == '/api/relation-applications'
            and resolve('/api/relation-applications').url_name == 'application_create', '申请创建路径与保存意图的准确目标不一致。')
    for application in RelationApplication.objects.order_by('id'):
        target = reverse('application_withdraw', kwargs={'application_id': application.id})
        matched = resolve(target)
        require(matched.url_name == 'application_withdraw' and matched.kwargs['application_id'] == application.id,
                '申请撤回路径与实际保存对象不一致。')

report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'PASS', 'scope': 'CURRENT_DATABASE_READ_ONLY_PROJECTION',
          'checks': checks, 'existing_alert_events': event_count, 'transaction_read_only': True,
          'clinical_data_created': False, 'read_receipt_created': False, 'credentials_submitted': False, 'provider_called': False,
          'authenticated_http_validation': 'NOT_RUN', 'positive_enabled_publication': 'NOT_RUN',
          'actual_read_receipt_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
report['draft_read_projection'] = {'existing_drafts': draft_count, 'existing_status_counts': draft_statuses,
                                   'existing_owner_patient_scopes': draft_scope_count, 'restorable_drafts': restorable_count,
                                   'drafts_created': False, 'drafts_changed': False,
                                   'actual_restore_and_abandon_validation': 'NOT_RUN'}
report['relation_read_projection'] = {'existing_relations': relation_count, 'relation_status_counts': relation_statuses,
    'guardian_scopes': len(guardian_scopes), 'existing_applications': application_count,
    'application_status_counts': application_statuses, 'applicant_scopes': len(applicant_scopes),
    'applications_created': False, 'relations_changed': False, 'actual_approval_and_revocation_validation': 'NOT_RUN'}
report['application_request_projection'] = {'existing_account_scopes': request_scope_count, 'existing_application_requests': request_count,
    'request_payload_created': False, 'actual_submission_and_recovery': 'NOT_RUN'}
if options.route_json:
    require(route_identity['relation_id'] is not None, '当前数据库缺少可用于匿名路由检查的关联标识。')
    print(json.dumps(route_identity))
else:
    (ROOT / '.planning-work/t54-family-read.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'report': report, 'responses': responses} if options.frontend_json else report, ensure_ascii=False))
