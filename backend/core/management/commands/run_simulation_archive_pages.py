import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from archives.page_execution import reconcile_expired_pages
from core.management.commands.seed_simulation import require_simulation
from tasks.models import ConsumerReceipt, OutboxEvent
from tasks.worker import consume


class Command(BaseCommand):
    help = '仅在独立模拟环境执行 archive_page.requested；只读取白名单事件，不消费其他任务'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        require_simulation()
        while True:
            close_old_connections()
            reconcile_expired_pages()
            completed = ConsumerReceipt.objects.filter(consumer='archive_page').values('event_id')
            events = OutboxEvent.objects.filter(event_type='archive_page.requested',
                schema_version='archive-page-1', task__schema_version='archive-page-1',
                task__input_refs__environment='simulation', task__status__in=['accepted', 'queued']).exclude(
                    id__in=completed).order_by('available_at', 'id')
            for event in events:
                result = consume.apply(kwargs={'event_id': str(event.id)}, throw=False)
                self.stdout.write(f'SIMULATION：页面事件 {event.id}；执行结果 {result.state}')
            if options['once']:
                return
            time.sleep(1)
