"""API 公共助手：会话主体、授权与稳定错误映射。"""
from rest_framework import status
from rest_framework.exceptions import ParseError
from rest_framework.response import Response
from django.middleware.csrf import CsrfViewMiddleware
from django.core.signing import BadSignature
from functools import wraps
from pydantic import ValidationError as PydanticValidationError
from av import FFmpegError
from pymupdf import FileDataError
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse
from django.conf import settings
from files.storage import ObjectNotFound, RangeNotSatisfiable

from authorization.actions import CASE_ACTIONS, SERVICE_ACTIONS
from authorization.policy import Denied, evaluate
from identity.models import Patient, Principal
from identity.session import read_session_principal
from identity.service import IdentityRejected


class ApiError(Exception):
    def __init__(self, code, http_status=400, message=None):
        super().__init__(message or code)
        self.code = code
        self.reason_code = code
        self.http_status = http_status
        self.message = message or code


def current_principal(request):
    principal_id, auth_epoch, audience = read_session_principal(request)
    if principal_id is None:
        return None, None, None
    principal = Principal.objects.filter(id=principal_id).first()
    if principal is None or principal.account_status != 'active' or auth_epoch != principal.auth_epoch:
        raise ApiError('AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    return principal, auth_epoch, audience


def get_patient(patient_id):
    patient = Patient.objects.filter(id=patient_id).first()
    if patient is None:
        raise ApiError("PATIENT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    return patient


def authorize_case(request, principal, patient, action, field_codes=None, record_id=None, author_id=None, require_relation=False, whole_resource=False, lock=True):
    _, auth_epoch, _ = read_session_principal(request)
    try:
        return evaluate(
            principal=principal, patient=patient, action=action, purpose="family_observation",
            environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=auth_epoch, field_codes=field_codes,
            record_id=record_id, author_id=author_id, require_relation=require_relation,
            whole_resource=whole_resource,
            read_only=request.method in ('GET', 'HEAD'),
            lock=lock,
        )
    except Denied as exc:
        raise ApiError(exc.reason_code, status.HTTP_403_FORBIDDEN)


def authorize_service(request, principal, action):
    _, auth_epoch, audience = read_session_principal(request)
    if audience != 'support':
        raise ApiError('SUPPORT_AUDIENCE_REQUIRED', 403)
    if not settings.SJIA_ENVIRONMENT:
        raise ApiError('ENVIRONMENT_CONFIGURATION_REQUIRED', 503)
    try:
        return evaluate(principal=principal, patient=None, action=action, purpose="clinical",
            environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=auth_epoch)
    except Denied as exc:
        raise ApiError(exc.reason_code, status.HTTP_403_FORBIDDEN)


def handle(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            request = args[0]
            if request.method not in ('GET', 'HEAD', 'OPTIONS') and (request.session.get('_sjia_principal_id') or view.__name__ in ('auth_login', 'auth_register', 'support_login')):
                rejection = CsrfViewMiddleware(lambda request: None).process_view(request._request, lambda request: None, (), {})
                if rejection is not None:
                    raise ApiError('CSRF_FAILED', status.HTTP_403_FORBIDDEN, '请求校验失败，请刷新页面后重试。')
            if view.__name__.startswith('content_disposition_'):
                response = view(*args, **kwargs)
            else:
                from retention.content_fences import content_reader
                from configurations.evidence import artifact_verification_scope
                with content_reader(), artifact_verification_scope():
                    response = view(*args, **kwargs)
            response['Cache-Control'] = 'no-store, private'
            return response
        except ApiError as exc:
            return Response({"code": exc.code, "message": exc.message, "retryable": False, "recovery": "none", "field_errors": []}, status=exc.http_status, headers={'Cache-Control': 'no-store, private'})
        except Exception as exc:  # noqa: BLE001
            import logging

            from retention.erasure_models import ContentDisposed
            if isinstance(exc, ContentDisposed):
                return Response({'code': 'CONTENT_DISPOSED', 'message': '这份资料的内容已按批准范围处置。',
                    'retryable': False, 'recovery': 'none', 'field_errors': []}, status=410,
                    headers={'Cache-Control': 'no-store, private'})
            if isinstance(exc, (ObjectDoesNotExist, Http404)):
                return Response({'code': 'RESOURCE_NOT_FOUND', 'message': '资料不存在或当前无法读取。',
                    'retryable': False, 'recovery': 'none', 'field_errors': []}, status=404,
                    headers={'Cache-Control': 'no-store, private'})
            if isinstance(exc, IdentityRejected):
                return Response({'code': exc.reason_code, 'message': '登录凭证无效或账号当前无法使用。',
                    'retryable': False, 'recovery': 'reauthenticate', 'field_errors': []}, status=401,
                    headers={'Cache-Control': 'no-store, private'})
            if isinstance(exc, RangeNotSatisfiable):
                return HttpResponse(status=416, headers={'Accept-Ranges': 'bytes',
                    'Content-Range': f'bytes */{exc.size}', 'Cache-Control': 'no-store, private'})
            if isinstance(exc, ObjectNotFound):
                return Response({'code': 'FILE_OBJECT_UNAVAILABLE', 'message': '当前原件内容无法读取，请核查保存状态。',
                    'retryable': False, 'recovery': 'reload', 'field_errors': []}, status=409,
                    headers={'Cache-Control': 'no-store, private'})

            if isinstance(exc, (FileDataError, UnicodeDecodeError)):
                return Response({'code': 'FILE_CONTENT_INVALID', 'message': '文件内容无法按登记格式读取，请检查完整原件和 UTF-8 文本编码。',
                    'retryable': False, 'recovery': 'review_input', 'field_errors': []}, status=422,
                    headers={'Cache-Control': 'no-store, private'})

            if isinstance(exc, TimeoutError):
                return Response({'code': 'UPSTREAM_TIMEOUT', 'message': '识别服务等待超时，原始资料已经保留，请稍后重新识别。',
                    'retryable': True, 'recovery': 'retry_after', 'field_errors': []}, status=504, headers={'Cache-Control': 'no-store, private'})

            if isinstance(exc, FFmpegError):
                return Response({'code': 'AUDIO_INVALID', 'message': '录音文件无法解码，请重新录音或选择完整音频。',
                    'retryable': False, 'recovery': 'review_input', 'field_errors': []}, status=422, headers={'Cache-Control': 'no-store, private'})

            if isinstance(exc, ParseError):
                return Response({
                    'code': 'REQUEST_BODY_INVALID', 'message': '请求内容格式无效，请检查后重新提交。',
                    'retryable': False, 'recovery': 'review_input', 'field_errors': [],
                }, status=400, headers={'Cache-Control': 'no-store, private'})
            if isinstance(exc, PydanticValidationError):
                if exc.title.startswith('Content') or exc.title in ('PlanRequest', 'ApprovalRequest', 'ExecuteRequest'):
                    return Response({'code': 'CONTENT_INPUT_INVALID', 'message': '处置范围或证明格式无效，请检查准确版本和必填信息。',
                        'retryable': False, 'recovery': 'review_input', 'field_errors': []}, status=422,
                        headers={'Cache-Control': 'no-store, private'})
                if exc.title.startswith('Verification'):
                    return Response({'code': 'VERIFICATION_INPUT_INVALID', 'message': '核对范围或提交标识无效，请检查内容。',
                        'retryable': False, 'recovery': 'review_input',
                        'field_errors': [{'path': '.'.join(str(part) for part in error['loc']), 'code': error['type']}
                            for error in exc.errors(include_input=False, include_url=False, include_context=False)]},
                        status=422, headers={'Cache-Control': 'no-store, private'})
                if exc.title.startswith(('Assessment', 'Dynamic')):
                    return Response({'code': 'DYNAMIC_INPUT_INVALID' if exc.title.startswith('Dynamic') else 'ASSESSMENT_INPUT_INVALID', 'message': '评估范围或提交标识无效，请检查内容。',
                        'retryable': False, 'recovery': 'review_input',
                        'field_errors': [{'path': '.'.join(str(part) for part in error['loc']), 'code': error['type']}
                            for error in exc.errors(include_input=False, include_url=False, include_context=False)]},
                        status=422, headers={'Cache-Control': 'no-store, private'})
                if exc.title in ('StaticRulePolicy', 'StaticContextPolicy', 'QualificationPolicy', 'MappingPolicy', 'LifecyclePolicy'):
                    return Response({'code': 'CONFIG_PAYLOAD_INVALID', 'message': '配置正文无效，请核查完整定义及准确引用。',
                        'retryable': False, 'recovery': 'review_input',
                        'field_errors': [{'path': '.'.join(str(part) for part in error['loc']), 'code': error['type']}
                            for error in exc.errors(include_input=False, include_url=False, include_context=False)]},
                        status=422, headers={'Cache-Control': 'no-store, private'})
                if exc.title.startswith('Evaluation') or exc.title.startswith('Dataset'):
                    return Response({'code': 'DATASET_INPUT_INVALID', 'message': '评价资料或提交标识无效，请检查内容。',
                        'retryable': False, 'recovery': 'review_input',
                        'field_errors': [{'path': '.'.join(str(part) for part in error['loc']), 'code': error['type']}
                            for error in exc.errors(include_input=False, include_url=False, include_context=False)]},
                        status=422, headers={'Cache-Control': 'no-store, private'})
                if exc.title == 'BatchQuery':
                    return Response({'code': 'ARCHIVE_BATCH_QUERY_INVALID', 'message': '批次查询条件无效，请检查内容。',
                        'retryable': False, 'recovery': 'review_input',
                        'field_errors': [{'path': '.'.join(str(part) for part in error['loc']), 'code': error['type']}
                            for error in exc.errors(include_input=False, include_url=False, include_context=False)]},
                        status=422, headers={'Cache-Control': 'no-store, private'})
                if exc.title in ('BatchIntentInput', 'BatchRetryInput', 'BatchRequestLookup', 'BatchWriteHeaders'):
                    return Response({'code': 'ARCHIVE_BATCH_INPUT_INVALID', 'message': '批次内容或提交标识无效，请检查资料。',
                        'retryable': False, 'recovery': 'review_input',
                        'field_errors': [{'path': '.'.join(str(part) for part in error['loc']), 'code': error['type']}
                            for error in exc.errors(include_input=False, include_url=False, include_context=False)]},
                        status=422, headers={'Cache-Control': 'no-store, private'})
                if exc.title.startswith('Reminder'):
                    return Response({'code': 'REMINDER_INPUT_INVALID', 'message': '提醒设置或查询条件无效，请检查内容。',
                        'retryable': False, 'recovery': 'review_input',
                        'field_errors': [{'path': '.'.join(str(part) for part in error['loc']), 'code': error['type']}
                            for error in exc.errors(include_input=False, include_url=False, include_context=False)]},
                        status=422, headers={'Cache-Control': 'no-store, private'})
                if exc.title in ('DocumentIntentInput', 'DocumentRetryInput', 'DocumentRequestLookup'):
                    return Response({'code': 'DOCUMENT_INPUT_INVALID', 'message': '文书内容或提交标识无效，请检查资料。',
                        'retryable': False, 'recovery': 'review_input',
                        'field_errors': [{'path': '.'.join(str(part) for part in error['loc']), 'code': error['type']}
                            for error in exc.errors(include_input=False, include_url=False, include_context=False)]},
                        status=422, headers={'Cache-Control': 'no-store, private'})
                if exc.title.startswith('Summary'):
                    return Response({'code': 'SUMMARY_INPUT_INVALID', 'message': '摘要范围或提交标识无效，请检查后重新提交。',
                        'retryable': False, 'recovery': 'review_input',
                        'field_errors': [{'path': '.'.join(str(part) for part in error['loc']), 'code': error['type']}
                            for error in exc.errors(include_input=False, include_url=False, include_context=False)]},
                        status=422, headers={'Cache-Control': 'no-store, private'})
                if exc.title.startswith('Application'):
                    return Response({'code': 'APPLICATION_INPUT_INVALID', 'message': '申请内容或提交标识无效，请检查后重新提交。',
                        'retryable': False, 'recovery': 'review_input',
                        'field_errors': [{'path': '.'.join(str(part) for part in error['loc']), 'code': error['type']}
                            for error in exc.errors(include_input=False, include_url=False, include_context=False)]},
                        status=422, headers={'Cache-Control': 'no-store, private'})
                if exc.title == 'RecordSearchInput':
                    return Response({'code': 'RECORD_SEARCH_INPUT_INVALID', 'message': '查询条件无效，请检查日期及筛选内容。',
                        'retryable': False, 'recovery': 'review_input',
                        'field_errors': [{'path': '.'.join(str(part) for part in error['loc']), 'code': error['type']}
                            for error in exc.errors(include_input=False, include_url=False, include_context=False)]},
                        status=422, headers={'Cache-Control': 'no-store, private'})
                request_error = exc.title in ('VoiceRequest', 'VoiceInputCreate', 'VoiceCreateRequest',
                    'VoiceInputLookup', 'VoiceInputProcess', 'VoiceInputAdoption', 'VoiceCancelRequest',
                    'PatientSelectionRequest', 'TranscriptionAuthorizationInput', 'ReportRequest', 'RecognitionRequest', 'SupportLoginInput',
                    'UploadGroupInput', 'DispositionIntentInput') or exc.title.startswith('Purpose')
                return Response({
                    'code': ('PURPOSE_INPUT_INVALID' if exc.title.startswith('Purpose') else 'RECOGNITION_INPUT_INVALID') if request_error else 'ARK_RESPONSE_INVALID',
                    'message': '请求内容无效，请检查资料及必填信息。' if request_error else '识别结果格式无效，本次结果未被采用。',
                    'retryable': False, 'recovery': 'review_input' if request_error else 'none',
                    'field_errors': [{'path': '.'.join(str(part) for part in error['loc']), 'code': error['type']} for error in exc.errors(include_input=False, include_url=False, include_context=False)] if request_error else [],
                }, status=422 if request_error else 502, headers={'Cache-Control': 'no-store, private'})
            if isinstance(exc, BadSignature):
                return Response({'code': 'CURSOR_INVALID', 'message': '分页标识无效，请重新查询。', 'retryable': False, 'recovery': 'reload', 'field_errors': []}, status=422, headers={'Cache-Control': 'no-store, private'})
            from django.db import IntegrityError
            if isinstance(exc, IntegrityError) and getattr(getattr(exc.__cause__, 'diag', None), 'message_primary', None) == 'CONTENT_DISPOSED':
                return Response({'code': 'CONTENT_DISPOSED', 'message': '这份资料已处置，旧操作不能恢复内容。',
                    'retryable': False, 'recovery': 'none', 'field_errors': []}, status=410,
                    headers={'Cache-Control': 'no-store, private'})
            reason = getattr(exc, 'reason_code', None)
            if reason:
                code = 409 if reason in ('VERSION_CONFLICT', 'DRAFT_NOT_EDITING', 'DRAFT_EXPIRED', 'CURSOR_STALE', 'FILE_GROUP_NOT_AVAILABLE', 'FILE_NOT_AVAILABLE', 'FILE_GROUP_INCOMPLETE', 'UPLOAD_REQUEST_MISMATCH') else 422
                if reason == 'CONTENT_EXECUTION_DISABLED':
                    code = 503
                elif reason == 'CONTENT_ACCESS_REVOKED':
                    code = 410
                elif reason.endswith('_DENIED') or reason in ('ACCOUNT_NOT_ACTIVE', 'AUTH_EPOCH_MISMATCH', 'PURPOSE_AUTHORIZATION_UNAVAILABLE'):
                    code = 403
                elif reason == 'REQUEST_RETENTION_UNAVAILABLE':
                    code = 503
                elif reason == 'REQUEST_RETENTION_CHANGED':
                    code = 409
                elif reason == 'FILE_VALIDATION_POLICY_REQUIRED':
                    code = 503
                elif reason.startswith('FILE_VALIDATION_') and reason.endswith((
                        '_MISMATCH', '_CHANGED', '_INTENT_EXPIRED', '_RETRY_UNAVAILABLE', '_RECEIPT_UNCERTAIN')):
                    code = 409
                elif reason in ('DATASET_RUNTIME_UNAVAILABLE', 'DATASET_RUN_RUNTIME_UNAVAILABLE',
                        'ASSESSMENT_RUNTIME_UNAVAILABLE', 'ASSESSMENT_CONFIGURATION_UNAVAILABLE'):
                    code = 503
                elif reason.startswith(('DATASET_', 'UNIT_')):
                    if reason.endswith('_NOT_FOUND'):
                        code = 404
                    elif reason.endswith('_AUTHORIZATION_CHANGED'):
                        code = 403
                    elif reason.endswith(('_CHANGED', '_MISMATCH', '_FROZEN', '_INCOMPLETE')) or reason in (
                            'DATASET_UNAVAILABLE', 'DATASET_INTENT_EXPIRED', 'DATASET_RETRY_NOT_AVAILABLE'):
                        code = 409
                elif reason.startswith(('ASSESSMENT_', 'VERIFICATION_', 'DYNAMIC_')):
                    if reason.endswith('_NOT_FOUND'):
                        code = 404
                    elif reason.endswith(('_UNAVAILABLE', '_PROOF_REQUIRED')):
                        code = 503
                    elif reason.endswith(('_CHANGED', '_INVALIDATED', '_MISMATCH')):
                        code = 409
                elif reason.startswith('PURPOSE_') and (reason.endswith('_UNAVAILABLE') or reason.endswith('_CHANGED') or reason.endswith('_PENDING')):
                    code = 409
                elif reason in ('APPLICATION_NOT_FOUND', 'APPLICATION_INTENT_NOT_FOUND'):
                    code = 404
                elif reason in ('FAMILY_AUDIENCE_REQUIRED', 'APPLICATION_AUTHORIZATION_CHANGED'):
                    code = 403
                elif reason in ('IDEMPOTENCY_CONFLICT', 'APPLICATION_INTENT_MISMATCH', 'APPLICATION_INTENT_EXPIRED', 'APPLICATION_RETRY_NOT_AVAILABLE', 'APPLICATION_NOT_PENDING'):
                    code = 409
                elif reason == 'SUMMARY_CONFIGURATION_PENDING':
                    code = 503
                elif reason in ('SUMMARY_INTENT_NOT_FOUND', 'SUMMARY_NOT_FOUND'):
                    code = 404
                elif reason in ('SUMMARY_AUTHORIZATION_CHANGED', 'SUMMARY_SCOPE_NOT_AVAILABLE'):
                    code = 403
                elif reason in ('SUMMARY_INTENT_MISMATCH', 'SUMMARY_INTENT_EXPIRED', 'SUMMARY_RETRY_NOT_AVAILABLE'):
                    code = 409
                elif reason in ('CONFIG_SCHEMA_UNAVAILABLE', 'CONFIG_EVIDENCE_UNAVAILABLE'):
                    code = 503
                elif reason.startswith('DICTIONARY_'):
                    if reason.endswith('_UNAVAILABLE'):
                        code = 503
                    elif reason.endswith(('_CHANGED', '_MISMATCH')):
                        code = 409
                elif reason in ('NOTIFICATION_PROVIDER_UNAVAILABLE', 'NOTIFICATION_CONFIGURATION_UNAVAILABLE',
                        'NOTIFICATION_PROVIDER_PROTOCOL_UNAVAILABLE'):
                    code = 503
                elif reason.startswith('NOTIFICATION_'):
                    if reason.endswith('_NOT_FOUND'):
                        code = 404
                    elif reason.endswith('_UNAVAILABLE'):
                        code = 503
                    elif reason.endswith(('_CHANGED', '_MISMATCH', '_INVALIDATED', '_UNCERTAIN')):
                        code = 409
                elif reason.startswith('VOICE_'):
                    if reason.endswith(('_REVOKED', '_AUTHORIZATION_CHANGED')):
                        code = 403
                    elif reason.endswith(('_REQUIRED', '_UNQUALIFIED')) and 'CONFIGURATION' in reason:
                        code = 503
                    elif reason.endswith(('_CONFLICT', '_CHANGED', '_NOT_AVAILABLE', '_UNKNOWN', '_LIMIT')):
                        code = 409
                elif reason in ('ATTEMPT_NOT_IN_FLIGHT', 'ATTEMPT_NOT_CANCELLABLE'):
                    code = 409
                elif reason == 'REMINDER_NOT_FOUND':
                    code = 404
                elif reason == 'REMINDER_SCOPE_DENIED':
                    code = 403
                elif reason in ('REMINDER_ENVIRONMENT_UNAVAILABLE', 'REMINDER_TARGET_TYPE_UNAVAILABLE'):
                    code = 503
                elif reason in ('REMINDER_TARGET_UNAVAILABLE', 'REMINDER_TARGET_NOT_PENDING',
                        'REMINDER_ALREADY_EXISTS', 'REMINDER_STATE_CONFLICT', 'REMINDER_NOTICE_STATE_INVALID'):
                    code = 409
                elif reason in ('CONFIG_TARGET_CHANGED', 'CONFIG_CHECKSUM_MISMATCH', 'CONFIG_ENVIRONMENT_MISMATCH',
                        'CONFIG_APPROVAL_UNAVAILABLE', 'CONFIG_VALIDATION_UNAVAILABLE', 'CONFIG_VALIDATION_REPORT_MISMATCH'):
                    code = 409
                elif reason in ('DOCUMENT_BATCH_UNAVAILABLE', 'DOCUMENT_PAGE_UNAVAILABLE', 'DOCUMENT_BATCH_CHANGED',
                        'DOCUMENT_COPY_CONFIRMATION_REQUIRED', 'DOCUMENT_FACT_PAGE_REQUIRED', 'DOCUMENT_FACT_EPISODE_REQUIRED'):
                    code = 409
                return Response({'code': reason, 'message': reason, 'retryable': False, 'recovery': 'none', 'field_errors': []}, status=code, headers={'Cache-Control': 'no-store, private'})
            logging.getLogger("sjia.api").error("api error", extra={'error_type': type(exc).__name__})
            return Response({"code": "INTERNAL_ERROR", "message": "服务内部错误", "retryable": False, "recovery": "none", "field_errors": []}, status=status.HTTP_500_INTERNAL_SERVER_ERROR, headers={'Cache-Control': 'no-store, private'})
    from audit.api import audited_operation

    return audited_operation(view, wrapper)
