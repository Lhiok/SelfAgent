"""工具调用空转 / 重复失败检测。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from session.types import AgentStep


@dataclass
class DoomVerdict:
    triggered: bool = False
    # 若设置 inject，则注入纠正消息并继续；否则结束本轮
    inject: str | None = None
    message: str | None = None


class DoomLoopTracker:
    def __init__(
        self,
        *,
        window: int = 6,
        repeat_limit: int = 3,
        fail_limit: int = 3,
        warn_once: bool = True,
    ) -> None:
        self.window = max(2, int(window))
        self.repeat_limit = max(2, int(repeat_limit))
        self.fail_limit = max(2, int(fail_limit))
        self.warn_once = warn_once
        self._signatures: deque[str] = deque(maxlen=self.window)
        self._fail_streak = 0
        self._warned = False

    def reset(self) -> None:
        self._signatures.clear()
        self._fail_streak = 0
        self._warned = False

    def observe(self, step: AgentStep) -> DoomVerdict:
        if not step.calls:
            return DoomVerdict()

        # 连续失败：忽略 ask_user 缓冲成功
        actionable = [c for c in step.calls if c.action != "ask_user"]
        if actionable:
            if all(c.ok is False for c in actionable):
                self._fail_streak += 1
            else:
                self._fail_streak = 0

            for call in actionable:
                sig = f"{call.action}|{(call.action_input or '').strip()}"
                self._signatures.append(sig)

        if self._fail_streak >= self.fail_limit:
            return DoomVerdict(
                triggered=True,
                message=(
                    f"连续 {self._fail_streak} 步工具调用失败，疑似空转，已停止。"
                    "请调整任务后重试。"
                ),
            )

        if len(self._signatures) >= self.repeat_limit:
            recent = list(self._signatures)[-self.repeat_limit :]
            if len(set(recent)) == 1:
                if self.warn_once and not self._warned:
                    self._warned = True
                    return DoomVerdict(
                        triggered=True,
                        inject=(
                            "系统：检测到你在重复相同的 Action/Action Input。"
                            "请换用不同工具、参数或策略；"
                            "若任务已完成请输出非空 Final Answer；"
                            "禁止继续重复同一调用。"
                        ),
                    )
                return DoomVerdict(
                    triggered=True,
                    message=(
                        "检测到重复工具调用空转，已停止本轮任务。"
                        "请换个思路后继续。"
                    ),
                )

        return DoomVerdict()


def doom_tracker_from_config(react_cfg: dict[str, Any] | None) -> DoomLoopTracker:
    cfg = (react_cfg or {}).get("doom_loop") or {}
    if cfg is False or (isinstance(cfg, dict) and cfg.get("enabled") is False):
        # 返回超大阈值，相当于禁用
        return DoomLoopTracker(window=1000, repeat_limit=1000, fail_limit=1000)
    return DoomLoopTracker(
        window=int(cfg.get("window", 6)),
        repeat_limit=int(cfg.get("repeat_limit", 3)),
        fail_limit=int(cfg.get("fail_limit", 3)),
        warn_once=bool(cfg.get("warn_once", True)),
    )
