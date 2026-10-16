"""Permission-test snapshot adapter must never disable real transaction coverage."""
from django.test import SimpleTestCase, TestCase, TransactionTestCase, LiveServerTestCase
from records import snapshot
from tests.synthetic_fixtures import patch_permission_record_snapshot


class SnapshotFixtureScopeTest(SimpleTestCase):
    def test_only_testcase_receives_one_scoped_patch_with_cleanup(self):
        original = snapshot.begin_record_snapshot
        case = TestCase()
        try:
            self.assertTrue(patch_permission_record_snapshot(case))
            patched = snapshot.begin_record_snapshot
            self.assertIsNot(patched, original)
            self.assertTrue(patch_permission_record_snapshot(case))
            self.assertIs(snapshot.begin_record_snapshot, patched)
            self.assertEqual(len(case._cleanups), 1)
        finally:
            case.doCleanups()
        self.assertIs(snapshot.begin_record_snapshot, original)

    def test_transaction_live_http_and_simple_cases_keep_production_sql(self):
        original = snapshot.begin_record_snapshot
        for case in (TransactionTestCase(), LiveServerTestCase(), SimpleTestCase(), None):
            with self.subTest(kind=type(case).__name__):
                self.assertFalse(patch_permission_record_snapshot(case))
                self.assertIs(snapshot.begin_record_snapshot, original)
