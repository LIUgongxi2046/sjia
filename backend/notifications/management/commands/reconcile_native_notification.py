from uuid import UUID

from django.db import transaction
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from alerting.models import AlertEvent
from assessment.qualified_facts import lock_case_permissions
from authorization.policy import check_principal_session, check_service_grant
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from notifications.errors import NotificationRejected
from notifications.native import consume_native, native_configuration
from tasks.models import OutboxEvent


class Command(BaseCommand):
    help = '依据当前真实岗位、病例权限和发布资格处理单项站内通知事件。'

    def add_arguments(self, parser):
        parser.add_argument('--event-id', type=UUID, required=True)
        parser.add_argument('--actor-id', type=UUID, required=True)
        parser.add_argument('--auth-epoch', type=int, required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
        _, _, policy, _ = native_configuration(gate)
        actor = Principal.objects.select_for_update().get(id=options['actor_id'])
        check_principal_session(actor, options['auth_epoch'])
        check_service_grant(actor, 'manage_notifications', gate.environment, timezone.now())
        event = OutboxEvent.objects.select_for_update().get(id=options['event_id'])
        if event.event_type != 'alert.event_created' or event.schema_version != 'dynamic-family-event-2':
            raise NotificationRejected('NOTIFICATION_EVENT_PROOF_MISMATCH')
        alert = AlertEvent.objects.get(id=event.aggregate_id)
        patient = Patient.objects.select_for_update().get(id=alert.patient_id)
        lock_case_permissions(actor, patient, 'read_case', policy.purpose, timezone.now(), whole_resource=True)
        receipt = consume_native(event)
        self.stdout.write(str(receipt.id))
