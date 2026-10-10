"""持续触发周期状态转换（ALERT 4.2）。

unknown 不关闭周期；not_met 仅在批准恢复条件满足时关闭；
closed/invalidated 在 met 时按再次触发规则创建新周期。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class EpisodeDecision:
    action: str  # create_event / continue_episode / close_episode / no_event / blocked
    next_status: str  # open / closed / invalid


def decide_episode(*, current_status: str | None, signal_state: str, reset_met: bool = False, re_trigger: bool = False) -> EpisodeDecision:
    if current_status is None or current_status in ("closed", "invalid"):
        if signal_state == "met":
            return EpisodeDecision(action="create_event", next_status="open")
        return EpisodeDecision(action="no_event", next_status=current_status or "closed")
    if current_status == "open":
        if signal_state == "met":
            if re_trigger:
                return EpisodeDecision(action="create_event", next_status="open")
            return EpisodeDecision(action="continue_episode", next_status="open")
        if signal_state == "unknown":
            return EpisodeDecision(action="no_event", next_status="open")
        if signal_state == "not_met":
            if reset_met:
                return EpisodeDecision(action="close_episode", next_status="closed")
            return EpisodeDecision(action="no_event", next_status="open")
    if current_status == "invalid":
        if signal_state == "met":
            return EpisodeDecision(action="create_event", next_status="open")
        return EpisodeDecision(action="no_event", next_status="invalid")
    raise ValueError(f"未知周期状态：{current_status}")
