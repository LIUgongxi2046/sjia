"""Pure graph regressions; importing/running this module opens no database."""
from unittest import TestCase

from tests.isolated_rows import child_first_order, child_first_components, fixture_cleanup_groups


class ExactFixtureOrderingTests(TestCase):
    def test_unmanaged_children_precede_managed_parents(self):
        self.assertEqual(child_first_order([1, 2, 3], [(3, 2), (2, 1)]), [3, 2, 1])

    def test_self_reference_deleted_in_one_statement(self):
        self.assertEqual(child_first_order([1, 2], [(2, 2), (2, 1)]), [2, 1])

    def test_irrelevant_schema_edges_do_not_block_cleanup(self):
        self.assertEqual(child_first_order([1], [(8, 9)]), [1])

    def test_immediate_cycle_fails_closed_in_legacy_flat_order(self):
        with self.assertRaisesRegex(RuntimeError, 'cycle'):
            child_first_order([1, 2], [(1, 2), (2, 1)])

    def test_empty_cleanup_has_no_groups(self):
        self.assertEqual(fixture_cleanup_groups([], [(1, 2, 'r')]), [])

    def test_no_action_cycle_is_one_group(self):
        self.assertEqual(fixture_cleanup_groups([1, 2], [(1, 2, 'a'), (2, 1, 'a')]), [(1, 2)])

    def test_external_restrict_and_cascade_do_not_poison_no_action_cycle(self):
        # 4 -> {2,3} -> 1; only {2,3} must share a statement.
        keys = [(4, 2, 'r'), (2, 3, 'a'), (3, 2, 'a'), (3, 1, 'c')]
        self.assertEqual(fixture_cleanup_groups([1, 2, 3, 4], keys), [(4,), (2, 3), (1,)])

    def test_external_set_actions_still_follow_child_first_boundaries(self):
        keys = [(4, 2, 'n'), (2, 3, 'a'), (3, 2, 'a'), (3, 1, 'd')]
        self.assertEqual(fixture_cleanup_groups([1, 2, 3, 4], keys), [(4,), (2, 3), (1,)])

    def test_two_cycles_are_separate_child_first_statements(self):
        keys = [(1, 2, 'a'), (2, 1, 'a'), (3, 4, 'a'), (4, 3, 'a'), (3, 2, 'r')]
        self.assertEqual(fixture_cleanup_groups([1, 2, 3, 4], keys), [(3, 4), (1, 2)])

    def test_internal_restrict_cascade_set_or_unknown_action_fails_closed(self):
        for action in ('r', 'c', 'n', 'd', '?'):
            with self.subTest(action=action), self.assertRaisesRegex(RuntimeError, 'internal native NO ACTION'):
                fixture_cleanup_groups([1, 2], [(1, 2, 'a'), (2, 1, action)])

    def test_parallel_foreign_key_cannot_hide_unsafe_internal_action(self):
        with self.assertRaisesRegex(RuntimeError, 'unsafe_edges'):
            fixture_cleanup_groups([1, 2], [(1, 2, 'a'), (2, 1, 'a'), (2, 1, 'r')])

    def test_safe_self_reference_remains_single_table_statement(self):
        self.assertEqual(fixture_cleanup_groups([1, 2], [(2, 2, 'a'), (2, 1, 'r')]), [(2,), (1,)])

    def test_unsafe_self_reference_is_not_treated_as_external_edge(self):
        with self.assertRaisesRegex(RuntimeError, 'component='):
            fixture_cleanup_groups([1], [(1, 1, 'c')])

    def test_absent_fixture_tables_and_their_cycles_are_ignored(self):
        keys = [(1, 2, 'r'), (2, 1, 'r'), (8, 9, 'r'), (9, 8, 'r')]
        self.assertEqual(fixture_cleanup_groups([1], keys), [(1,)])

    def test_independent_groups_and_duplicates_are_deterministic(self):
        keys = [(3, 2, 'a'), (2, 3, 'a'), (3, 2, 'a')]
        expected = [(1,), (2, 3), (4,)]
        self.assertEqual(fixture_cleanup_groups([4, 3, 2, 1, 1], keys), expected)
        self.assertEqual(fixture_cleanup_groups([1, 2, 3, 4], reversed(keys)), expected)

    def test_one_way_bridge_does_not_merge_unrelated_components(self):
        edges = [(1, 2), (2, 1), (2, 3), (3, 4), (4, 3), (5, 3)]
        self.assertEqual(child_first_components([1, 2, 3, 4, 5], edges), [(1, 2), (5,), (3, 4)])

    def test_large_cycle_uses_iterative_traversal(self):
        tables = list(range(1500))
        edges = [(node, (node + 1) % len(tables), 'a') for node in tables]
        self.assertEqual(fixture_cleanup_groups(tables, edges), [tuple(tables)])
