"""T18 2016 分类确定性执行逻辑验证。

真实边界资料与独立 Gold 由 T32 依据 D03/D04/D07/D08 验证；本文件验证算法三值与组合。
运行：backend/.venv/bin/python backend/manage.py test tests.test_T18 --keepdb
"""
from decimal import Decimal

from django.test import SimpleTestCase

from assessment.classification import classify_2016, evaluate_threshold


class ThresholdTest(SimpleTestCase):
    def test_eq_gt(self):
        self.assertEqual(evaluate_threshold(Decimal("800"), "eq", "gt", Decimal("684")), "met")
        self.assertEqual(evaluate_threshold(Decimal("684"), "eq", "gt", Decimal("684")), "not_met")

    def test_lt_gt(self):
        self.assertEqual(evaluate_threshold(Decimal("100"), "lt", "gt", Decimal("684")), "not_met")
        self.assertEqual(evaluate_threshold(Decimal("700"), "lt", "gt", Decimal("684")), "unknown")

    def test_gt_gt(self):
        self.assertEqual(evaluate_threshold(Decimal("700"), "gt", "gt", Decimal("684")), "met")
        self.assertEqual(evaluate_threshold(Decimal("600"), "gt", "gt", Decimal("684")), "unknown")

    def test_ge_gt(self):
        self.assertEqual(evaluate_threshold(Decimal("685"), "ge", "gt", Decimal("684")), "met")
        self.assertEqual(evaluate_threshold(Decimal("684"), "ge", "gt", Decimal("684")), "unknown")

    def test_le_le(self):
        self.assertEqual(evaluate_threshold(Decimal("181"), "le", "le", Decimal("181")), "met")
        self.assertEqual(evaluate_threshold(Decimal("182"), "le", "le", Decimal("181")), "unknown")

    def test_gt_le(self):
        self.assertEqual(evaluate_threshold(Decimal("200"), "gt", "le", Decimal("181")), "not_met")
        self.assertEqual(evaluate_threshold(Decimal("100"), "gt", "le", Decimal("181")), "unknown")


class ClassificationTest(SimpleTestCase):
    def _input(self, value, qualifier=None):
        return {"value": value, "qualifier": qualifier}

    def test_meets(self):
        overall, conditions = classify_2016(
            premise={"state": "known", "met": True},
            ferritin=self._input("700"),
            platelet=self._input("100"),
            ast=self._input("50"),
            triglycerides=self._input("200"),
            fibrinogen=self._input("300"),
        )
        self.assertEqual(overall, "meets")
        self.assertEqual(conditions["BR-C01"], "met")

    def test_not_meets_ferritin_low(self):
        overall, _ = classify_2016(
            premise={"state": "known", "met": True},
            ferritin=self._input("100"),
            platelet=self._input("100"),
            ast=self._input("50"),
            triglycerides=self._input("200"),
            fibrinogen=self._input("300"),
        )
        self.assertEqual(overall, "not_meets")

    def test_not_applicable_premise_false(self):
        overall, _ = classify_2016(
            premise={"state": "known", "met": False},
            ferritin=self._input("700"),
            platelet=self._input("100"),
            ast=self._input("50"),
            triglycerides=self._input("200"),
            fibrinogen=self._input("300"),
        )
        self.assertEqual(overall, "not_applicable")

    def test_insufficient_premise_unknown(self):
        overall, _ = classify_2016(
            premise={"state": "unknown"},
            ferritin=self._input("700"),
            platelet=self._input("100"),
            ast=self._input("50"),
            triglycerides=self._input("200"),
            fibrinogen=self._input("300"),
        )
        self.assertEqual(overall, "insufficient")

    def test_insufficient_ferritin_unknown(self):
        overall, _ = classify_2016(
            premise={"state": "known", "met": True},
            ferritin=self._input(None),
            platelet=self._input("100"),
            ast=self._input(None),
            triglycerides=self._input("300"),
            fibrinogen=self._input("400"),
        )
        self.assertEqual(overall, "insufficient")

    def test_boundary_both_sides(self):
        # 五项阈值边界两侧
        self.assertEqual(evaluate_threshold(Decimal("684"), "eq", "gt", Decimal("684")), "not_met")
        self.assertEqual(evaluate_threshold(Decimal("685"), "eq", "gt", Decimal("684")), "met")
        self.assertEqual(evaluate_threshold(Decimal("181"), "eq", "le", Decimal("181")), "met")
        self.assertEqual(evaluate_threshold(Decimal("182"), "eq", "le", Decimal("181")), "not_met")
        self.assertEqual(evaluate_threshold(Decimal("48"), "eq", "gt", Decimal("48")), "not_met")
        self.assertEqual(evaluate_threshold(Decimal("49"), "eq", "gt", Decimal("48")), "met")
        self.assertEqual(evaluate_threshold(Decimal("156"), "eq", "gt", Decimal("156")), "not_met")
        self.assertEqual(evaluate_threshold(Decimal("157"), "eq", "gt", Decimal("156")), "met")
        self.assertEqual(evaluate_threshold(Decimal("360"), "eq", "le", Decimal("360")), "met")
        self.assertEqual(evaluate_threshold(Decimal("361"), "eq", "le", Decimal("360")), "not_met")
