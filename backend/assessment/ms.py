"""MS 七项贡献与完整评分（BR-M01）。

按 PRD 8.14.3 唯一公式用 Decimal 计算；先比较后展示（不四舍五入比较）；
七项缺少任意一项不产生完整总分；专业变量未知不按 0 计算。
"""
from decimal import Decimal

COEFFICIENTS = {
    "cns": Decimal("2.44"),
    "hemorrhagic_manifestations": Decimal("1.54"),
    "active_arthritis": Decimal("-1.30"),
    "platelet_count": Decimal("-0.003"),
    "ldh": Decimal("0.001"),
    "fibrinogen": Decimal("-0.004"),
    "ferritin": Decimal("0.0001"),
}

THRESHOLD = Decimal("-2.1")

VARIABLE_KEYS = list(COEFFICIENTS.keys())


def compute_ms(inputs: dict) -> dict:
    missing = [key for key in VARIABLE_KEYS if inputs.get(key) is None]
    if missing:
        return {"ms_total": None, "contributions": {}, "threshold_result": None, "missing_items": missing}
    contributions = {}
    for key in VARIABLE_KEYS:
        contributions[key] = COEFFICIENTS[key] * Decimal(str(inputs[key]))
    ms_total = sum(contributions.values(), Decimal("0"))
    threshold_result = ms_total >= THRESHOLD
    return {
        "ms_total": ms_total,
        "contributions": {key: str(value) for key, value in contributions.items()},
        "threshold_result": threshold_result,
        "missing_items": [],
    }
