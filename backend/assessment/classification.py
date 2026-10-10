"""2016 分类条件确定性执行（BR-C00—BR-C07）。

五项比较符（eq/lt/le/gt/ge）与阈值形成三值判定 met/not_met/unknown；
组合判定遵循 PRD 8.14.2 判定表；未核对、未知单位、时间不可用均为资料不足。
"""
from decimal import Decimal


def evaluate_threshold(value, qualifier, operator, threshold):
    """三值比较：返回 met/not_met/unknown。

    value 为 Decimal 或 None；qualifier 为 eq/lt/le/gt/ge/None（None 视作 eq）；
    operator 为 gt/le。
    """
    if value is None:
        return "unknown"
    qualifier = qualifier or "eq"
    if operator == "gt":
        if qualifier == "eq":
            return "met" if value > threshold else "not_met"
        if qualifier == "lt":
            return "not_met" if value <= threshold else "unknown"
        if qualifier == "le":
            return "not_met" if value <= threshold else "unknown"
        if qualifier == "gt":
            return "met" if value >= threshold else "unknown"
        if qualifier == "ge":
            return "met" if value > threshold else "unknown"
    elif operator == "le":
        if qualifier == "eq":
            return "met" if value <= threshold else "not_met"
        if qualifier == "lt":
            return "met" if value <= threshold else "unknown"
        if qualifier == "le":
            return "met" if value <= threshold else "unknown"
        if qualifier == "gt":
            return "not_met" if value > threshold else "unknown"
        if qualifier == "ge":
            return "not_met" if value > threshold else "unknown"
    return "unknown"


def _to_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


class Condition:
    def __init__(self, value, qualifier, operator, threshold):
        self.value = _to_decimal(value)
        self.qualifier = qualifier
        self.operator = operator
        self.threshold = _to_decimal(threshold)

    def evaluate(self) -> str:
        if self.value is None:
            return "unknown"
        return evaluate_threshold(self.value, self.qualifier, self.operator, self.threshold)


def classify_2016(*, premise, ferritin, platelet, ast, triglycerides, fibrinogen):
    """返回 (整体结果, 逐条件结果)。

    输入为 dict：{"value": 数值, "qualifier": 比较符或 None}；
    缺项或未核对将对应 value 置 None 即视为资料不足。
    整体结果：meets/not_meets/insufficient/not_applicable。
    """
    premise_state = premise.get("state", "known") if isinstance(premise, dict) else "known"
    premise_met = premise.get("met") if isinstance(premise, dict) else premise

    conditions = {
        "BR-C00": premise_state,
        "BR-C01": Condition(ferritin.get("value"), ferritin.get("qualifier"), "gt", "684").evaluate(),
        "BR-C02": Condition(platelet.get("value"), platelet.get("qualifier"), "le", "181").evaluate(),
        "BR-C03": Condition(ast.get("value"), ast.get("qualifier"), "gt", "48").evaluate(),
        "BR-C04": Condition(triglycerides.get("value"), triglycerides.get("qualifier"), "gt", "156").evaluate(),
        "BR-C05": Condition(fibrinogen.get("value"), fibrinogen.get("qualifier"), "le", "360").evaluate(),
    }

    premise_result = "met" if premise_met else "not_met"
    if premise_state != "known":
        premise_result = "unknown"
    ferritin_result = conditions["BR-C01"]

    four = [conditions["BR-C02"], conditions["BR-C03"], conditions["BR-C04"], conditions["BR-C05"]]
    satisfied = four.count("met")
    unknown = four.count("unknown")

    # PRD 8.14.2 组合判定表（自上而下）
    if premise_result == "not_met":
        overall = "not_applicable"
    elif premise_result == "unknown":
        overall = "insufficient"
    elif ferritin_result == "not_met":
        overall = "not_meets"
    elif ferritin_result == "met" and satisfied >= 2:
        overall = "meets"
    elif satisfied + unknown < 2:
        overall = "not_meets"
    else:
        overall = "insufficient"

    conditions["BR-C06"] = overall
    return overall, conditions
