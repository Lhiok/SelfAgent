"""Plan Mode 系统提示（自拟，非 CC 专有文案）。"""

from __future__ import annotations

PLAN_MODE_SECTION = """## Plan Mode
你处于规划模式：只读调研后提交计划，不要执行写入。

规则：
1. 仅用只读工具（list/read/search 等）与 ask_user 澄清需求。
2. 调研完成后调用 submit_plan（Exit 门禁），参数含 summary/thought/steps。
3. steps 里才写具体写入类 skill；当前回合禁止真正执行写入。
4. 不要用纯文本问「是否同意计划」——提交后由用户通过 /confirm 或 /reject 决定。
5. 若用户刚拒绝计划，请根据反馈修订后再 submit_plan。
"""

PLAN_MODE_EXIT_SECTION = """## 计划已批准
用户已确认计划，你现在可以按步骤执行写入与变更。
"""


def plan_mode_prompt(*, reject_feedback: str = "") -> str:
    text = PLAN_MODE_SECTION.strip()
    fb = (reject_feedback or "").strip()
    if fb:
        text += f"\n\n### 上次拒绝反馈\n{fb}\n请据此修订计划后再次 submit_plan。"
    return text


def plan_mode_exit_prompt() -> str:
    return PLAN_MODE_EXIT_SECTION.strip()
