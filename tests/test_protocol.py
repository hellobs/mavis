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


class TestValidateDecisionEvent:
    """`DecisionEvent` 没有 `type` 字段,校验器曾因此把它判为不合规(2026-10-03 修)。

    契约里决策事件是协议六种消息之一、也是治理平台的主消费对象,校验器却不认它 ——
    接入方拿校验器当契约守门人时,会把自己框架产出的合法数据挡在门外。
    """

    def _event(self, **kw):
        ev = {"id": "e-0001", "step": 3, "time": "20250213-09:30",
              "agent": "老周", "role": "投资顾问",
              "action": "与沈砚之讨论 HCM 仓位", "poignancy": 7}
        ev.update(kw)
        return ev

    def test_real_decision_event_accepted(self):
        assert validate_message(self._event()) is True

    def test_decision_event_from_module_shape_accepted(self):
        """按 `output.decisions` 真实产出的字段集(含 IVD 字段)也要认。"""
        assert validate_message(self._event(
            goal_score=0.42, goal_alignment={"Risk Control": 0.5},
            value_tendency={"Risk Control": 0.4},
            involves=["沈砚之"], has_conversation=True, tags=[])) is True

    def test_explicit_type_decision_accepted(self):
        assert validate_message(self._event(type="decision")) is True

    def test_incomplete_decision_event_rejected(self):
        """只有一半字段的不该蒙混过关。"""
        assert validate_message({"id": "e-1", "agent": "老周"}) is False   # 缺 action
        assert validate_message({"id": "e-1", "action": "x"}) is False     # 缺 agent
        assert validate_message({"agent": "老周", "action": "x"}) is False  # 缺 id

    def test_broken_stream_message_still_rejected(self):
        """放开门禁不等于放水:缺 type 的流式消息仍然非法。"""
        assert validate_message({"time": "20250213-09:30"}) is False
        assert validate_message({}) is False
        assert validate_message({"name": "老周", "coord": [1, 2]}) is False
        assert validate_message({"speaker": "老周", "text": "hi"}) is False
