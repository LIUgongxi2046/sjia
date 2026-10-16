import json

from django.core.management.base import BaseCommand

from core.demo_experience import seed_family_experience


class Command(BaseCommand):
    help = '为已存在且明确匹配的家庭体验账号预置限定示例资料；不创建、读取或修改密码。'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True, help='已由本人正常注册的准确账号名')
        parser.add_argument('--principal-id', required=True, help='与上述账号匹配的准确非秘密主体标识')
        parser.add_argument('--acknowledge-test-only', action='store_true',
            help='仅允许 DEBUG、明确的软件测试环境和 test_* 数据库中的示例资料')

    def handle(self, *args, **options):
        result = seed_family_experience(username=options['username'], principal_id=options['principal_id'],
            acknowledge_test_only=options['acknowledge_test_only'])
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
