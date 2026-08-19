"""framework.prompt — 提示词系统(可插拔)

Scratch 为默认中文提示词实现(从 modules/prompt 迁移,时间注入)。
"""
from framework.prompt.scratch import Scratch, Result

__all__ = ["Scratch", "Result"]
