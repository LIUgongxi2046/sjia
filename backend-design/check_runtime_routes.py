import ast
import hashlib
import inspect
from pathlib import Path

from django.db import connection
from django.urls import URLPattern, URLResolver, get_resolver
from django.urls.converters import get_converters

from authorization.actions import CASE_ACTIONS, SERVICE_ACTIONS


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def reject_query(execute, statement, params, many, context):
    raise ValueError('接口设计检查禁止执行数据库请求')


def route_template(pattern):
    value = pattern._route
    for name, converter in pattern.converters.items():
        kinds = [kind for kind, instance in get_converters().items() if type(instance) is type(converter)]
        require(len(kinds) == 1, '接口路径转换器需要唯一登记')
        value = value.replace('<' + kinds[0] + ':' + name + '>', '{' + name + '}')
        value = value.replace('<' + name + '>', '{' + name + '}')
    require('<' not in value and '>' not in value, '接口路径存在未接收转换器')
    return value


def runtime_routes(patterns, prefix=''):
    result = []
    for item in patterns:
        require(hasattr(item.pattern, '_route'), '运行接口需要明确的Django路径')
        path = prefix + route_template(item.pattern)
        if isinstance(item, URLResolver):
            result.extend(runtime_routes(item.url_patterns, path))
            continue
        require(isinstance(item, URLPattern), '运行接口类型未接收')
        if not path.startswith('api/v2/'):
            continue
        callback = item.callback
        source = Path(inspect.getsourcefile(callback.cls)).resolve(strict=True)
        require(source.is_relative_to(ROOT / 'backend/api'), '接口处理函数需要项目源码')
        methods = sorted(set(callback.cls.http_method_names) - {'options', 'head'})
        symbol = callback.cls.__name__
        tree = ast.parse(source.read_text(), filename=str(source))
        functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol]
        require(len(functions) == 1, '实际接口处理函数需要准确symbol')
        function = functions[0]
        declared = [decorator.args[0] for decorator in function.decorator_list if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name) and decorator.func.id == 'api_view']
        require(len(declared) == 1 and set(methods) == {value.lower() for value in ast.literal_eval(declared[0])},
            '实际HTTP方法与处理函数声明不一致')
        result.append({'path': '/' + path.removeprefix('api/v2/'), 'methods': methods,
            'view': str(source.relative_to(ROOT)) + '#' + symbol, 'line': function.lineno,
            'route_name': item.name, 'module': callback.__module__})
    return result


def check_routes(catalog):
    with connection.execute_wrapper(reject_query):
        routes = runtime_routes(get_resolver().url_patterns)
    identities = [(method, item['path']) for item in routes for method in item['methods']]
    require(len(identities) == len(set(identities)), '运行接口方法与路径重复')
    registered_actions = CASE_ACTIONS | SERVICE_ACTIONS
    protocol_actions = {'provider_signature', 'anonymous_credentials', 'support_session'}
    operations = {}
    for item in catalog:
        selected = [route for route in routes if route['path'] == item['path'] and item['method'].lower() in route['methods']]
        require(len(selected) == 1, '接口目录需要唯一实际路由：' + item['operation_id'])
        require(set(item['auth'].split(';')) <= registered_actions | protocol_actions, '接口动作没有实际登记')
        operations[item['operation_id']] = selected[0]
    sources = {ROOT / name for name in ('backend/config/urls.py', 'backend/api/urls.py', 'backend/api/urls_v2.py',
        'backend/authorization/actions.py', 'backend/authorization/policy.py', 'backend/api/base.py')}
    sources.add(Path(__file__).resolve())
    sources.update(ROOT / item['view'].partition('#')[0] for item in routes)
    sources.update((ROOT / 'backend/requests').glob('*.py'))
    return {'status': 'PASS', 'scope': 'Django实际路由、HTTP方法、处理函数及准确授权登记',
        'routes': routes, 'catalog_operations': operations, 'route_count': len(routes),
        'method_count': len(identities), 'catalog_operation_count': len(operations),
        'database_queries': 0, 'business_validation': 'NOT_RUN',
        'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)}}
