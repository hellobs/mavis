# -*- coding: utf-8 -*-
"""protocol 消息协议测试

覆盖 mavisframework.runtime.protocol.validate_message 的契约规则:
- 非 dict / 缺 type → False
- agent 类型需 name + coord
- time / chat_line / snapshot / done / error → True
- 未知类型 → False
"""
import pytest

from mavisframework.runtime.protocol import validate_message


class TestValidateMessage:
    """validate_message 契约校验"""

    def test_non_dict_rejected(self):
        assert validate_message(None) is False
        assert validate_message("time") is False
        assert validate_message(123) is False

    def test_missing_type_rejected(self):
        assert validate_message({"time": "20250213-09:30"}) is False
        assert validate_message({}) is False

    def test_agent_requires_name_and_coord(self):
        # 合法 agent 消息
        assert validate_message(
            {"type": "agent", "name": "老周", "coord": [10, 6]}
        ) is True
        # 缺 name
        assert validate_message({"type": "agent", "coord": [10, 6]}) is False
        # 缺 coord
        assert validate_message({"type": "agent", "name": "老周"}) is False

    def test_time_msg(self):
        assert validate_message({"type": "time", "time": "20250213-09:30"}) is True
        # time 缺字段也通过(简易校验只查 type)
        assert validate_message({"type": "time"}) is True

    def test_chat_line_msg(self):
        assert validate_message(
            {"type": "chat_line", "speaker": "老周", "text": "你好"}
        ) is True

    def test_snapshot_msg(self):
        assert validate_message({"type": "snapshot", "agents": {}, "time": "x"}) is True

    def test_done_and_error(self):
        assert validate_message({"type": "done"}) is True
        assert validate_message({"type": "error", "message": "boom"}) is True

    def test_unknown_type_rejected(self):
        assert validate_message({"type": "unknown"}) is False
        assert validate_message({"type": "foo", "anything": 1}) is False
