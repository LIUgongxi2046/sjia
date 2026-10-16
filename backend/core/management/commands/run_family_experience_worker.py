import json
import math
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from core.demo_worker import (admit_family_worker, consume_family_events_once,
    require_family_worker_admission)


class Command(BaseCommand):
    help = ('仅消费已绑定家庭体验账号两位示例患儿的本地文件校验和摘要事件；'
        '不使用通用队列，不执行 OCR、通知、处置或 support reconciliation。')
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True, help='既有普通家庭账号的准确账号名')
        parser.add_argument('--principal-id', required=True, help='与该账号一致的非秘密主体标识')
        parser.add_argument('--acknowledge-test-only', action='store_true')
        parser.add_argument('--once', action='store_true', help='处理至多 --limit 条当前可领事件后退出')
        parser.add_argument('--limit', type=int, default=25)
        parser.add_argument('--poll-seconds', type=float, default=1.0)

    def handle(self, *args, **options):
        interval = options['poll_seconds']
        if not math.isfinite(interval) or not 0.1 <= interval <= 60:
            raise CommandError('DEMO_WORKER_POLL_SECONDS_MUST_BE_0_1_TO_60')
        if not 1 <= options['limit'] <= 100:
            raise CommandError('DEMO_WORKER_LIMIT_MUST_BE_1_TO_100')
        account = {'username': options['username'], 'principal_id': options['principal_id'],
            'acknowledge_test_only': options['acknowledge_test_only']}
        admit_family_worker(**account)
        try:
            while True:
                close_old_connections()
                require_family_worker_admission()
                result = consume_family_events_once(**account, limit=options['limit'])
                if result['claimed'] or options['once']:
                    self.stdout.write(json.dumps(result, sort_keys=True))
                    self.stdout.flush()
                if options['once']:
                    return
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write('Family experience local worker stopped.')
