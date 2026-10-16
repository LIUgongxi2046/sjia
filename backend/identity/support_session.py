from django.conf import settings
from django.utils import timezone

from authorization.actions import SERVICE_ACTIONS
from authorization.models import AccessGrant, ServiceRoleGrant
from authorization.policy import Denied, check_principal_session, environment_enabled


def support_capabilities(principal):
    check_principal_session(principal, principal.auth_epoch)
    if not settings.SJIA_ENVIRONMENT:
        raise Denied('ENVIRONMENT_CONFIGURATION_REQUIRED')
    environment_enabled(settings.SJIA_ENVIRONMENT)
    now = timezone.now()
    grants = ServiceRoleGrant.objects.filter(principal=principal, environment=settings.SJIA_ENVIRONMENT,
        action__in=SERVICE_ACTIONS).order_by('action', '-valid_from', '-id').distinct('action')
    capabilities = [{'capability_id': grant.action, 'state': 'available', 'reason_code': None, 'config_version': None}
        for grant in grants if grant.evidence_ref.strip() and grant.valid_from <= now and grant.revoked_at is None
        and (grant.valid_until is None or grant.valid_until > now)]

    # A professional's case grant permits support entry without granting any
    # global administrator capability. Every record action rechecks exact scope.
    from authorization.policy import matching_case_grants
    from identity.models import Patient
    grants = AccessGrant.objects.select_related('patient').filter(principal=principal, action='write_professional')
    if any(grant.patient.status == 'active' and grant.evidence_ref.strip() and any(
            row.id == grant.id for row in matching_case_grants(principal, grant.patient, 'write_professional', now,
                author_id=principal.id, record_id=grant.assigned_record_id)) for grant in grants):
        capabilities.append({'capability_id': 'write_professional', 'state': 'available', 'reason_code': None, 'config_version': None})
    return capabilities
