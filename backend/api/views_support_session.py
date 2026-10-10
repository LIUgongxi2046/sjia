from django.conf import settings
from django.db import transaction
from pydantic import BaseModel, ConfigDict, Field
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, current_principal, handle
from family_content.models import EnvironmentGate
from identity.models import Principal
from identity.service import authenticate_account
from identity.session import bootstrap_csrf, establish_session
from identity.support_session import support_capabilities


class SupportLoginInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    username: str = Field(min_length=1, max_length=128, pattern=r'\S')
    password: str = Field(min_length=1, max_length=4096)


@api_view(['POST'])
@handle
@transaction.atomic
def support_login(request):
    payload = SupportLoginInput.model_validate(request.data)
    principal = authenticate_account(username=payload.username, password=payload.password)
    EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    principal = Principal.objects.select_for_update().get(id=principal.id)
    capabilities = support_capabilities(principal)
    if not capabilities:
        raise ApiError('SUPPORT_ACTION_DENIED', 403)
    establish_session(request, principal_id=principal.id, auth_epoch=principal.auth_epoch, audience='support')
    return Response({'principal_id': str(principal.id), 'account_status': principal.account_status,
        'expires_at': request.session.get_expiry_date().isoformat(), 'audience': 'support',
        'capabilities': capabilities, 'csrf_token': bootstrap_csrf(request)})


@api_view(['GET'])
@handle
def support_session_read(request):
    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'support':
        raise ApiError('SUPPORT_AUDIENCE_REQUIRED', 403)
    return Response({'principal_id': str(principal.id), 'account_status': principal.account_status,
        'expires_at': request.session.get_expiry_date().isoformat(), 'audience': audience,
        'capabilities': support_capabilities(principal), 'csrf_token': bootstrap_csrf(request)})
