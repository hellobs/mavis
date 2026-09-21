# -*- coding: utf-8 -*-
"""全天在线角色(ai_tool / no_sleep)的"空闲"文案 —— **单一来源 + 可被调用方覆盖**。

为什么单独成模块(2026-09-21,IVD 纯洁度整顿):
- 这段文案原来在 `core/agent_core.py` 与 `prompt/scratch.py` 里各写一份,而且
  `agent_core` 里写的是**中文**"空闲待命,保持在线,无用户咨询"、`prompt` 里写的是英文,
  两边不一致 → 只能再补一段"中文→英文"的兼容清洗兜底。本质是**案例措辞长在框架里**。
- 现在:默认值只是"常驻在线、暂无用户请求"的**通用英文**,场景/壳用
  `config["idle_text"]` 覆盖(要中文、要案例自己的说法都行),内核不再出现任何案例词汇。

约定:任何要写"空闲/待命"文案的地方都走 `resolve_idle_text()`,不要再写字面量。
"""

DEFAULT_IDLE_TEXT = "Idle standby, staying online, no user inquiries"


def resolve_idle_text(agent=None, config=None) -> str:
    """优先级:agent.idle_text > config["idle_text"] > DEFAULT_IDLE_TEXT。"""
    if agent is not None:
        v = getattr(agent, "idle_text", "")
        if v:
            return str(v)
    if isinstance(config, dict):
        v = config.get("idle_text")
        if v:
            return str(v)
    return DEFAULT_IDLE_TEXT
