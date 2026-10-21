import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from markdown_it import MarkdownIt
import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))
if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    raise ValueError('设计检查需要明确的独立数据库配置')

import django

django.setup()

from django.conf import settings

from check_field_ownership import check_fields

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
    arguments = argparse.ArgumentParser()
    arguments.add_argument('--database-name', required=True)
    arguments.add_argument('--expected-role', required=True)
    arguments.add_argument('--expected-port', type=int, required=True)
    arguments.add_argument('--expected-server-start', required=True)
    arguments.add_argument('--metadata-report', type=Path, required=True)
    arguments.add_argument('--source-validation-report', type=Path, required=True)
    arguments.add_argument('--node-modules', type=Path, default=ROOT / 'guardian-h5/node_modules')
    arguments.add_argument('--report-path', type=Path, default=ROOT / '.planning-work/t35-data.json')
    options = arguments.parse_args()
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
    ownership = check_fields(options.metadata_report.resolve(strict=True), options.node_modules)
    status_path = ROOT / 'data-design/clinical-dataset-status.json'
    dataset_status = json.loads(status_path.read_text())
    expected_sources = dataset_status['authorized_originals']
    source_validation = json.loads(options.source_validation_report.read_text())
    require(source_validation['result'] == 'PASS'
        and all(source_validation[key] == expected_sources[key] for key in ('files', 'pages', 'bytes'))
        and source_validation['manifest_sha256'] == expected_sources['source_manifest_sha256']
        and source_validation['sample_sha256'] == hashlib.sha256((ROOT / 'data-design/sample_dataset.json').read_bytes()).hexdigest()
        and source_validation['clinical_approval'] is False and source_validation['new_model_calls'] == 0,
        '当前真实来源与项目文档样本证据不一致')
    linked_sources = {status_path, ROOT / 'data-design/sample_dataset.json', ROOT / 'data-design/dataset.schema.json'}
    pending = {ROOT / 'docs/evidence/CHECK-T35/receipt.json', ROOT / 'docs/evidence/CHECK-T35/output.log'}
    for name in ('sJIA合并MAS预警平台数据LLD.md', 'data-design/v2-data-specification.md', 'docs/development/T35.md'):
        path = ROOT / name
        linked_sources.add(path)
        for token in parser.parse(path.read_text()):
            for child in token.children or []:
                if child.type == 'link_open':
                    address = urlsplit(child.attrGet('href'))
                    if not address.scheme and address.path:
                        target = (path.parent / unquote(address.path)).resolve()
                        require(target.is_relative_to(ROOT) and (target.is_file() or target in pending), '数据设计引用需要准确文件')
                        # 交付状态在检查结束后更新，引用存在性仍需核验。
                        if target not in pending and target != ROOT / 'planning/delivery-ledger.json':
                            linked_sources.add(target)

    config = settings.DATABASES['default']
    require(config['NAME'] == options.database_name and config['USER'] == options.expected_role
        and int(config['PORT']) == options.expected_port and config['HOST'] == '127.0.0.1', '设计目标与明确配置不一致')
    connection_args = dict(host=config['HOST'], port=config['PORT'], user=config['USER'], password=config['PASSWORD'])
    with psycopg.connect(dbname=options.database_name, autocommit=True, **connection_args) as database:
        identity = database.execute('SELECT current_database(),current_user,inet_server_port(),pg_postmaster_start_time()').fetchone()
        require(identity[:3] == (options.database_name, options.expected_role, options.expected_port)
            and identity[3] == datetime.fromisoformat(options.expected_server_start), '设计数据库身份不一致')
        privileges = database.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        require(privileges == (False, False, False, False, False), '设计账号必须使用独立最小权限')
        require(170000 <= database.info.server_version < 180000, '本次设计验收需要明确的PostgreSQL17实例')
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
        domain_rows = database.execute("SELECT t.typname,format_type(t.typbasetype,t.typtypmod),t.typnotnull,c.conname,"
            "pg_get_constraintdef(c.oid,true),c.convalidated FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
            "LEFT JOIN pg_constraint c ON c.contypid=t.oid WHERE n.nspname='sjia' AND t.typtype='d' ORDER BY t.typname,c.conname").fetchall()
        domains = {}
        for name, base_type, not_null, constraint, definition, validated in domain_rows:
            domain = domains.setdefault(name, {'base_type': base_type, 'not_null': not_null, 'constraints': []})
            if constraint is not None:
                domain['constraints'].append({'name': constraint, 'definition': definition, 'validated': validated})
        for name, expected in ownership['required_domains'].items():
            require(domains.get(name) == expected, f'物理domain与当前真实升级副本不一致：{name}')
        engine = database.execute('SHOW server_version').fetchone()[0]
        engine_version = database.info.server_version
        database.execute('ROLLBACK')
        require(database.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'sjia'").fetchone()[0] == 0, '空结构检查未完整结束')

    inputs = ['data-design/schema.sql', 'data-design/case-schema.sql', 'data-design/v2-schema.sql',
              'data-design/v2-data-specification.md', 'data-design/check_v2.py',
              'sJIA合并MAS预警平台数据LLD.md', 'sJIA合并MAS预警平台PRD.md', 'sJIA合并MAS预警平台HLD.md']
    report = {
        'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'capability': 'DESIGN', 'status': 'PASS', 'field_groups': len(fields),
        'database': options.database_name, 'engine': engine, 'engine_version': engine_version,
        'runtime_python': sys.version.split()[0],
        'target_postgresql_17': 'PASS' if 170000 <= engine_version < 180000 else 'NOT_RUN',
        'existing_tables': len(existing), 'new_tables': sorted(TABLES),
        'new_constraints': len(new_constraints), 'clinical_rows_inserted': 0,
        'database_identity': {'name': identity[0], 'role': identity[1], 'port': identity[2], 'postmaster_started_at': identity[3].isoformat()},
        'role_is_minimal': True, 'field_ownership': ownership,
        'real_source_validation': {key: source_validation[key] for key in ('files', 'pages', 'bytes', 'formats',
            'manifest_sha256', 'source_files_changed', 'new_model_calls', 'clinical_approval', 'verified_distinct_patient_count')},
        'production_migrations': 'NOT_RUN', 'actual_migration_readback': 'NOT_RUN',
        'visual_validation': 'NOT_RUN', 'business_validation': 'NOT_RUN',
        'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in sorted(set(inputs) | set(ownership['source_sha256'])
                | {str(path.relative_to(ROOT)) for path in linked_sources})},
    }
    options.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
