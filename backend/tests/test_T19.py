"""T19 MS 七项贡献与完整评分逻辑验证。

真实七项与独立答案由 T32 依据 D03/D04/D07/D08 验证；本文件验证公式与缺项处理。
运行：backend/.venv/bin/python backend/manage.py test tests.test_T19 --keepdb
"""
from decimal import Decimal

from django.test import SimpleTestCase

from assessment.ms import compute_ms


class MsComputeTest(SimpleTestCase):
    def test_full_seven_contributions(self):
        result = compute_ms({
            "cns": 1, "hemorrhagic_manifestations": 0, "active_arthritis": 0,
            "platelet_count": "100", "ldh": "500", "fibrinogen": "200", "ferritin": "800",
        })
        self.assertIsNotNone(result["ms_total"])
        self.assertEqual(result["ms_total"], Decimal("1.92"))
        self.assertTrue(result["threshold_result"])
        self.assertEqual(len(result["contributions"]), 7)

    def test_threshold_boundary(self):
        # 构造 MS 恰为 -2.1 的情况，验证 >= 判定
        result = compute_ms({
            "cns": 0, "hemorrhagic_manifestations": 0, "active_arthritis": 0,
            "platelet_count": "700", "ldh": "0", "fibrinogen": "0", "ferritin": "0",
        })
        # -0.003 × 700 = -2.1
        self.assertEqual(result["ms_total"], Decimal("-2.1"))
        self.assertTrue(result["threshold_result"])

    def test_missing_item_no_total(self):
        result = compute_ms({
            "cns": None, "hemorrhagic_manifestations": 0, "active_arthritis": 0,
            "platelet_count": "100", "ldh": "500", "fibrinogen": "200", "ferritin": "800",
        })
        self.assertIsNone(result["ms_total"])
        self.assertEqual(result["missing_items"], ["cns"])

    def test_unknown_not_zero(self):
        # 专业变量未知不按 0 计算：缺 fibrinogen 即不产生总分
        result = compute_ms({
            "cns": 1, "hemorrhagic_manifestations": 0, "active_arthritis": 0,
            "platelet_count": "100", "ldh": "500", "fibrinogen": None, "ferritin": "800",
        })
        self.assertIsNone(result["ms_total"])
        self.assertIn("fibrinogen", result["missing_items"])
