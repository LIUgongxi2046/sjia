"""信号组合求值：all / any / at_least(k) 的三值组合。

每个原子条件返回 met/not_met/unknown；组合按登记规则求值，逐项保留缺项证据。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Combination:
    operator: str
    operands: list[str]

    def evaluate(self) -> str:
        return evaluate_combination(self.operator, self.operands)


def evaluate_combination(operator: str, operands: list[str]) -> str:
    met = operands.count("met")
    not_met = operands.count("not_met")
    unknown = operands.count("unknown")
    if operator == "all":
        if not_met > 0:
            return "not_met"
        if unknown > 0:
            return "unknown"
        return "met"
    if operator == "any":
        if met > 0:
            return "met"
        if unknown > 0:
            return "unknown"
        return "not_met"
    if operator.startswith("at_least:"):
        k = int(operator.split(":", 1)[1])
        if met >= k:
            return "met"
        if met + unknown < k:
            return "not_met"
        return "unknown"
    raise ValueError(f"未知组合算子：{operator}")
