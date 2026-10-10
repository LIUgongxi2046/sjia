from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from assessment.qualified_facts import lock_case_permissions
from authorization.policy import check_principal_session, check_service_grant
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from notifications.errors import NotificationRejected
from notifications.material_delivery import TASK_SCHEMA, consume_material, material_configuration
from tasks.models import OutboxEvent


class Command(BaseCommand):
    help = '核验准确运行身份与原资料提醒事件，继续原通知请求。'

    def add_arguments(self, parser):
        parser.add_argument('--event-id', type=UUID, required=True)
        parser.add_argument('--actor-id', type=UUID, required=True)
        parser.add_argument('--auth-epoch', type=int, required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
        configured, reason = material_configuration(gate, lock=True)
        if reason is not None:
            raise NotificationRejected(reason)
        actor = Principal.objects.select_for_update().get(id=options['actor_id'])
        check_principal_session(actor, options['auth_epoch'])
        check_service_grant(actor, 'manage_notifications', gate.environment, timezone.now())
        event = OutboxEvent.objects.get(id=options['event_id'], event_type='notification.material_requested', schema_version=TASK_SCHEMA)
        patient = Patient.objects.select_for_update().get(id=event.task.patient_id)
        lock_case_permissions(actor, patient, 'read_case', configured[5].purpose, timezone.now(), whole_resource=True)
        consume_material(event)
        self.stdout.write(self.style.SUCCESS(f'原资料提醒事件已经处理：{event.id}'))
