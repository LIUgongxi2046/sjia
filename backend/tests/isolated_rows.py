"""Exact fixture cleanup for PostgreSQL schemas Django flush does not discover.

Only for isolated Django test databases. This preserves pre-existing primary keys,
includes unmanaged archive tables, and never disables referential integrity.
"""
from django.db import connection, transaction


def child_first_order(tables, edges):
    """Order immediate FK children before parents; deferred FK cycles need no edge."""
    remaining = set(tables)
    edges = {(child, parent) for child, parent in edges
             if child != parent and child in remaining and parent in remaining}
    ordered = []
    while remaining:
        ready = sorted(remaining - {parent for child, parent in edges if child in remaining})
        if not ready:
            raise RuntimeError('Immediate foreign-key cycle prevents exact test-row cleanup')
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered


def child_first_components(tables, edges):
    """Return strongly connected table groups in child-before-parent order.

    Only tables with new fixture keys participate. Iterative Kosaraju traversal
    avoids recursion limits and keeps independent groups deterministic. Deferrable
    constraints must be omitted by the caller; they remain enforced at commit.
    """
    tables = set(tables)
    edges = {(child, parent) for child, parent in edges if child in tables and parent in tables}
    forward = {table: set() for table in tables}
    reverse = {table: set() for table in tables}
    for child, parent in edges:
        forward[child].add(parent)
        reverse[parent].add(child)
    visited, finished = set(), []
    for root in sorted(tables):
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(sorted(forward[root])))]
        while stack:
            node, neighbors = stack[-1]
            neighbor = next(neighbors, None)
            if neighbor is None:
                finished.append(node)
                stack.pop()
            elif neighbor not in visited:
                visited.add(neighbor)
                stack.append((neighbor, iter(sorted(forward[neighbor]))))
    visited, components = set(), []
    for root in reversed(finished):
        if root in visited:
            continue
        component, stack = [], [root]
        visited.add(root)
        while stack:
            node = stack.pop()
            component.append(node)
            for neighbor in sorted(reverse[node]):
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        components.append(tuple(sorted(component)))
    components.sort()
    owner = {table: index for index, component in enumerate(components) for table in component}
    component_edges = {(owner[child], owner[parent]) for child, parent in edges if owner[child] != owner[parent]}
    return [components[index] for index in child_first_order(range(len(components)), component_edges)]


def fixture_cleanup_groups(tables, foreign_keys):
    """Plan exact-key deletion groups without weakening any FK delete action.

    Each FK is (child_oid, parent_oid, PostgreSQL confdeltype). External RESTRICT,
    CASCADE and SET actions determine the SCC DAG order just like NO ACTION.
    Only an internal edge of a cyclic group must be NO ACTION: its check can run
    after the group's single DELETE/CTE statement. Validate every group before
    the caller issues any deletion, including unsafe self-reference cycles.
    """
    tables = set(tables)
    keys = {(child, parent, action) for child, parent, action in foreign_keys
            if child in tables and parent in tables}
    groups = child_first_components(tables, [(child, parent) for child, parent, _ in keys])
    for group in groups:
        members = set(group)
        internal = {(child, parent, action) for child, parent, action in keys
                    if child in members and parent in members}
        cyclic = len(group) > 1 or any(child == parent for child, parent, _ in internal)
        unsafe = sorted((child, parent, action) for child, parent, action in internal if action != 'a')
        if cyclic and unsafe:
            raise RuntimeError('Exact fixture cycle cleanup requires internal native NO ACTION foreign keys: '
                f'component={group}; unsafe_edges={unsafe}')
    return groups


class IsolatedSchemaRows:
    def __init__(self, *, require_empty=False):
        self._guard()
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT c.oid, n.nspname, c.relname,
                    ARRAY(SELECT a.attname FROM pg_index i
                          CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, ord)
                          JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
                          WHERE i.indrelid=c.oid AND i.indisprimary ORDER BY k.ord)
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='sjia' AND c.relkind IN ('r', 'p')
                ORDER BY c.oid
            """)
            self.tables = {}
            framework_metadata = set()
            for oid, schema, table, columns in cursor.fetchall():
                if not columns:
                    raise RuntimeError(f'Test cleanup requires a primary key: {schema}.{table}')
                name = f'{quote(schema)}.{quote(table)}'
                keys = ', '.join(quote(column) for column in columns)
                self.tables[oid] = (name, keys, len(columns))
                if table in {'django_migrations', 'django_content_type', 'auth_permission'}:
                    framework_metadata.add(oid)
            cursor.execute("""
                SELECT conrelid, confrelid, confdeltype FROM pg_constraint
                WHERE contype='f' AND NOT condeferrable
            """)
            self.foreign_keys = cursor.fetchall()
            self.before = {}
            for oid, (name, keys, _) in self.tables.items():
                cursor.execute(f'SELECT {keys} FROM {name}')
                self.before[oid] = set(cursor.fetchall())
        nonempty_application = {self.tables[oid][0]: len(keys)
            for oid, keys in self.before.items() if keys and oid not in framework_metadata}
        if require_empty and nonempty_application:
            raise RuntimeError(f'Synthetic fixture requires an empty application test schema: {nonempty_application}')

    @staticmethod
    def _guard():
        if connection.vendor != 'postgresql' or not str(connection.settings_dict['NAME']).startswith('test_'):
            raise RuntimeError('Exact fixture cleanup requires an isolated PostgreSQL test database')

    def cleanup(self):
        self._guard()
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL DEFERRED')
            created_by_table = {}
            for oid, (name, keys, _) in self.tables.items():
                cursor.execute(f'SELECT {keys} FROM {name}')
                current = set(cursor.fetchall())
                if not self.before[oid].issubset(current):
                    raise RuntimeError(f'Test removed pre-existing fixture rows in {name}')
                if current - self.before[oid]:
                    created_by_table[oid] = current - self.before[oid]
            # SCC boundaries preserve child-first ordering for external
            # RESTRICT/CASCADE edges. Only the actual internal NO ACTION cycle
            # belongs in a shared statement; unrelated tables are never folded
            # into its CTE merely because the full graph contains a cycle.
            groups = fixture_cleanup_groups(created_by_table, self.foreign_keys)
            for group in groups:
                statements, parameters = [], []
                for oid in group:
                    name, keys, width = self.tables[oid]
                    created = sorted(created_by_table[oid])
                    row_placeholder = '(' + ', '.join(['%s'] * width) + ')'
                    placeholders = ', '.join([row_placeholder] * len(created))
                    statements.append(f'DELETE FROM {name} WHERE ({keys}) IN ({placeholders})')
                    parameters.extend(value for row in created for value in row)
                if len(statements) == 1:
                    # Also covers a safe self-reference: one exact table-wide
                    # statement, still restricted to this fixture's new keys.
                    cursor.execute(statements[0], parameters)
                else:
                    ctes = [f'clean_{index} AS ({statement} RETURNING 1)'
                            for index, statement in enumerate(statements)]
                    cursor.execute('WITH ' + ', '.join(ctes) + ' SELECT 1', parameters)
            for oid, (name, keys, _) in self.tables.items():
                cursor.execute(f'SELECT {keys} FROM {name}')
                if set(cursor.fetchall()) != self.before[oid]:
                    raise RuntimeError(f'Test-row cleanup did not preserve the original set in {name}')
