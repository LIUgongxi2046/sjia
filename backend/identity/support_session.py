from django.conf import settings
from django.utils import timezone

from authorization.actions import SERVICE_ACTIONS
from authorization.models import ServiceRoleGrant
from authorization.policy import Denied, check_principal_session, environment_enabled


def support_capabilities(principal):
    check_principal_session(principal, principal.auth_epoch)
    if not settings.SJIA_ENVIRONMENT:
        raise Denied('ENVIRONMENT_CONFIGURATION_REQUIRED')
    environment_enabled(settings.SJIA_ENVIRONMENT)
    now = timezone.now()
    grants = ServiceRoleGrant.objects.filter(principal=principal, environment=settings.SJIA_ENVIRONMENT,
        action__in=SERVICE_ACTIONS).order_by('action', '-valid_from', '-id').distinct('action')
    return [{'capability_id': grant.action, 'state': 'available', 'reason_code': None, 'config_version': None}
        for grant in grants if grant.evidence_ref.strip() and grant.valid_from <= now and grant.revoked_at is None
        and (grant.valid_until is None or grant.valid_until > now)]
