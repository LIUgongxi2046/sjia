from django.conf import settings
from django.db import transaction

from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from family_content.models import EnvironmentGate
from notifications.errors import NotificationRejected


@transaction.atomic
def notification_unavailability_reason() -> str | None:
    gate = EnvironmentGate.objects.filter(environment=settings.SJIA_ENVIRONMENT).first()
    if gate is None or not gate.enabled or gate.runtime_config_id is None:
        return 'NOTIFICATION_PROVIDER_UNAVAILABLE'
    runtime = ConfigVersion.objects.get(id=gate.runtime_config_id)
    if runtime.kind != 'provider' or runtime.schema_version != 'backend-runtime-1':
        return 'NOTIFICATION_CONFIGURATION_UNAVAILABLE'
    if configuration_qualification_reason(runtime, gate.environment, audience='support') is not None:
        return 'NOTIFICATION_CONFIGURATION_UNAVAILABLE'
    providers = [item for item in runtime.payload['providers']
        if item['kind'] == 'notification' and item['enabled']]
    if len(providers) != 1:
        return 'NOTIFICATION_PROVIDER_UNAVAILABLE'
    provider = ConfigVersion.objects.filter(id=providers[0]['config_id'], kind='provider').first()
    if provider is None or provider.status != 'enabled':
        return 'NOTIFICATION_CONFIGURATION_UNAVAILABLE'
    if provider.schema_version != 'notification-in-app-2':
        return 'NOTIFICATION_PROVIDER_PROTOCOL_UNAVAILABLE'
    if configuration_qualification_reason(provider, gate.environment, audience='support') is not None:
        return 'NOTIFICATION_CONFIGURATION_UNAVAILABLE'
    from notifications.native import native_configuration, signing_key_availability_reason

    reason = signing_key_availability_reason(provider.payload['signing_secret_ref'])
    if reason is not None:
        return reason
    native_configuration(gate, lock=False)
    return None


@transaction.atomic
def require_notification_protocol():
    from notifications.native import native_configuration

    gate = EnvironmentGate.objects.filter(environment=settings.SJIA_ENVIRONMENT).first()
    if gate is None or not gate.enabled or gate.runtime_config_id is None:
        raise NotificationRejected('NOTIFICATION_PROVIDER_UNAVAILABLE')
    gate = EnvironmentGate.objects.select_for_update().get(environment=gate.environment)
    return native_configuration(gate)


def receive_provider_callback(*, provider_id, request):
    require_notification_protocol()
    # 原生站内回执需要本人会话及准确签名票据，不接受供应商回调声明。
    raise NotificationRejected('NOTIFICATION_EXTERNAL_CALLBACK_UNSUPPORTED')
