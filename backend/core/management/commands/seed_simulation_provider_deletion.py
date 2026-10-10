from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from authorization.models import ServiceRoleGrant
from core.management.commands.seed_simulation import require_simulation
from family_content.models import EnvironmentGate
from identity.models import PrincipalCredential


class Command(BaseCommand):
    help = '为模拟支持账号提供用途副本管理权限'

    @transaction.atomic
    def handle(self, *args, **options):
        require_simulation()
        if settings.SJIA_ENVIRONMENT != 'simulation':
            raise RuntimeError('此命令仅允许在模拟环境执行')
        actor = PrincipalCredential.objects.select_for_update().get(username='simulation-support').principal
        gate = EnvironmentGate.objects.select_for_update().get(environment='simulation', enabled=True)
        grant = ServiceRoleGrant.objects.filter(principal=actor, environment=gate,
            action='manage_data_use', evidence_ref='SIMULATION:T40模拟副本管理', revoked_at=None).first()
        created = grant is None
        if grant is None:
            grant = ServiceRoleGrant.objects.create(principal=actor, environment=gate,
                action='manage_data_use', granted_by=actor,
                evidence_ref='SIMULATION:T40模拟副本管理', valid_from=timezone.now())
        self.stdout.write(f'SIMULATION manage_data_use created={created} grant={grant.id}')
