"""Bounded, read-only internal health probes; no provider calls or secret output."""
import redis
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from configurations.service import loaded_config_version
from family_content.models import EnvironmentGate


def check_database() -> bool:
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        return cursor.fetchone() == (1,)


def check_schema() -> bool:
    executor = MigrationExecutor(connection)
    return not executor.migration_plan(executor.loader.graph.leaf_nodes())


def check_broker() -> bool:
    client = redis.Redis.from_url(settings.REDIS_URL,
        socket_connect_timeout=2, socket_timeout=2, retry_on_timeout=False)
    try:
        return bool(client.ping())
    finally:
        client.close()


def readiness(environment: str | None = None) -> dict:
    dependencies, errors = {}, {}
    for name, probe in (('database', check_database), ('schema', check_schema), ('broker', check_broker)):
        if name == 'schema' and not dependencies['database']:
            dependencies[name], errors[name] = False, 'DATABASE_UNAVAILABLE'
            continue
        try:
            dependencies[name] = bool(probe())
            if not dependencies[name]:
                errors[name] = 'DEPENDENCY_NOT_READY'
        except Exception:
            dependencies[name], errors[name] = False, 'DEPENDENCY_UNAVAILABLE'
    from config.runtime_admission import admission_status
    runtime = {'loaded_config_version': loaded_config_version(), 'desired_config_version': None,
        'capabilities': {}, 'state': 'not_ready', 'reason_code': 'DATABASE_UNAVAILABLE'}
    dependencies['configuration'] = False
    if dependencies['database'] and dependencies['schema']:
        runtime = admission_status(environment or settings.SJIA_ENVIRONMENT)
        dependencies['configuration'] = runtime['state'] == 'admitted_current'
    if not dependencies['configuration']:
        errors['configuration'] = runtime['reason_code']
    return {'status': 'ready' if all(dependencies.values()) else 'not_ready',
        'dependencies': dependencies, 'errors': errors,
        'loaded_config_version': runtime['loaded_config_version'],
        'desired_config_version': runtime['desired_config_version'],
        'runtime_admission': runtime['state'], 'capabilities': runtime['capabilities'],
        'scope': 'process_runtime_dependencies_and_qualified_configuration',
        'clinical_qualification': 'NOT_VERIFIED_BY_HEALTH_PROBE'}
