import json
from uuid import UUID

from django.core.management.base import BaseCommand

from notifications.reminder_lifecycle import reconcile_reminders


class Command(BaseCommand):
    help = '检查本人提醒目标与当前权限，保存失效计划的状态及准确审计。'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, required=True)
        parser.add_argument('--after', type=UUID)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        result = reconcile_reminders(limit=options['limit'], after=options['after'], dry_run=options['dry_run'])
        self.stdout.write(json.dumps(result, ensure_ascii=False))
