import hashlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from markdown_it import MarkdownIt
import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django

django.setup()

from django.conf import settings


DATABASE_NAME = 'sjia_design_v2_20261007'
TABLES = {
    'archive', 'archive_batch', 'archive_document', 'archive_document_version',
    'archive_page', 'archive_document_page', 'archive_page_attempt', 'archive_fact',
    'archive_fact_version', 'archive_document_episode', 'archive_relation_version',
    'purpose_copy', 'purpose_copy_review', 'observation_period_version',
    'delta_summary', 'delta_summary_item', 'evaluation_dataset_version',
    'evaluation_patient_partition', 'evaluation_label', 'evaluation_run',
    'material_reminder', 'material_reminder_notice',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = MarkdownIt('commonmark').enable('table')
    fields = set()
    for name in ('sJIA合并MAS预警平台数据LLD.md', 'data-design/v2-data-specification.md'):
        tokens = parser.parse((ROOT / name).read_text())
        in_cell = False
        for token in tokens:
            if token.type in ('td_open', 'th_open'):
                in_cell = True
            elif token.type in ('td_close', 'th_close'):
                in_cell = False
            elif token.type == 'inline' and in_cell:
                fields.update(re.findall(r'\bFD-\d{2}\b', token.content))
    require(fields == {f'FD-{number:02}' for number in range(1, 72)}, '71 个字段组的表格映射不完整')
    require('DATA V2.0' in (ROOT / 'sJIA合并MAS预警平台数据LLD.md').read_text(), '数据版本不一致')

    config = settings.DATABASES['default']
    connection_args = dict(host=config['HOST'], port=config['PORT'], user=config['USER'], password=config['PASSWORD'])
    with psycopg.connect(dbname='postgres', autocommit=True, **connection_args) as administrative:
        exists = administrative.execute('SELECT 1 FROM pg_database WHERE datname = %s', (DATABASE_NAME,)).fetchone()
        if exists is None:
            administrative.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(DATABASE_NAME)))
    with psycopg.connect(dbname=DATABASE_NAME, autocommit=True, **connection_args) as database:
        require(database.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'sjia'").fetchone()[0] == 0, '设计检查实例必须没有业务表')
        database.execute('BEGIN')
        database.execute((ROOT / 'data-design/schema.sql').read_text(), prepare=False)
        database.execute((ROOT / 'data-design/case-schema.sql').read_text(), prepare=False)
        existing = {row[0] for row in database.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'sjia'")}
        database.execute((ROOT / 'data-design/v2-schema.sql').read_text(), prepare=False)
        current = {row[0] for row in database.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'sjia'")}
        require(current - existing == TABLES, '新增物理表与数据设计不一致')
        columns = database.execute("SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema = 'sjia'").fetchall()
        column_map = {(table, field): (type_name, nullable) for table, field, type_name, nullable in columns}
        for key, shape in {
            ('archive_fact_version', 'available_at'): ('timestamp with time zone', 'YES'),
            ('archive_fact_version', 'medical_time_id'): ('uuid', 'YES'),
            ('archive_fact_version', 'context_type'): ('text', 'NO'),
            ('archive_page_attempt', 'cost'): ('numeric', 'YES'),
            ('material_reminder', 'schedule_at'): ('timestamp with time zone', 'YES'),
            ('evaluation_label', 'reference_answer_ref'): ('text', 'YES'),
        }.items():
            require(column_map[key] == shape, f'字段类型或未知语义不符：{key}')
        constraints = database.execute("SELECT c.conname, c.contype, c.confdeltype, c.condeferrable, c.condeferred, t.relname FROM pg_constraint c JOIN pg_class t ON c.conrelid = t.oid JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = 'sjia'").fetchall()
        new_constraints = [row for row in constraints if row[5] in TABLES]
        require(not any(row[1] == 'f' and row[2] == 'c' for row in new_constraints), '新增结构包含级联删除')
        for name in ('archive_document_current_fk', 'archive_fact_current_fk'):
            require(any(row[0] == name and row[3:5] == (True, True) for row in constraints), '当前指针缺少延迟约束')
        for table in sorted(TABLES):
            statement = sql.SQL('SELECT count(*) FROM {}.{}').format(sql.Identifier('sjia'), sql.Identifier(table))
            require(database.execute(statement).fetchone()[0] == 0, '设计检查不能包含病例或模拟资料')
        engine = database.execute('SHOW server_version').fetchone()[0]
        database.execute('ROLLBACK')
        require(database.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'sjia'").fetchone()[0] == 0, '空结构检查未完整结束')

    inputs = ['data-design/schema.sql', 'data-design/case-schema.sql', 'data-design/v2-schema.sql',
              'data-design/v2-data-specification.md', 'data-design/check_v2.py',
              'sJIA合并MAS预警平台数据LLD.md', 'sJIA合并MAS预警平台PRD.md', 'sJIA合并MAS预警平台HLD.md']
    report = {
        'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'capability': 'DESIGN', 'status': 'PASS', 'field_groups': len(fields),
        'database': DATABASE_NAME, 'engine': engine, 'target_postgresql_17': 'NOT_RUN',
        'existing_tables': len(existing), 'new_tables': sorted(TABLES),
        'new_constraints': len(new_constraints), 'clinical_rows_inserted': 0,
        'production_migrations': 'NOT_RUN', 'actual_migration_readback': 'NOT_RUN',
        'visual_validation': 'NOT_RUN', 'business_validation': 'NOT_RUN',
        'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in inputs},
    }
    (ROOT / '.planning-work/t35-data.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
