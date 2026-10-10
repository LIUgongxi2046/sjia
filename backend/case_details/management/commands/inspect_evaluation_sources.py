import json

from django.core.management.base import BaseCommand

from case_details.evaluation_inventory import inspect_evaluation_sources


class Command(BaseCommand):
    help = '核验现有来源文件清单，分别返回已知数量与未登记的评价统计单位。'

    def add_arguments(self, parser):
        parser.add_argument('manifest_path')
        parser.add_argument('--unit-manifest')

    def handle(self, *args, **options):
        result = inspect_evaluation_sources(options['manifest_path'], unit_manifest_path=options['unit_manifest'])
        self.stdout.write(json.dumps(result, ensure_ascii=False))
