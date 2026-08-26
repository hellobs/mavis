# -*- coding: utf-8 -*-
"""决策导出测试(IVD 重构)

覆盖 mavisframework.output.decisions:
- generate_decision_events:从 checkpoints 生成事件
- id 全局唯一(不随文件序号重复)
- goal_score / goal_alignment / value_tendency 字段导出
- location 使用英文逗号
- decisions.json / interventions.json 不被当作 checkpoint 读取
"""
import json
import os

import pytest

from mavisframework.output.decisions import generate_decision_events, export_decision_stream


def _write_checkpoint(folder, name, time, agents):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        json.dump({"time": time, "step": 1, "agents": agents}, f, ensure_ascii=False)


def _agent(agent_name, alignment=None, tendency=None):
    return {
        "coord": [10, 6],
        "action": {"event": {"describe": "做研究", "address": ["the Ville", "资料室"], "predicate": "此时"}},
        "status": {
            "poignancy": 5,
            "goal_alignment": alignment or {},
            "value_tendency": tendency or {},
        },
    }


class TestDecisionExport:
    def test_ids_unique_across_files(self, tmp_path):
        # 两个存档文件 × 两个角色 → 4 条事件,id 全部唯一
        _write_checkpoint(str(tmp_path), "simulate-1.json", "20250213-09:30", {
            "老周": _agent("老周"), "沈砚之": _agent("沈砚之"),
        })
        _write_checkpoint(str(tmp_path), "simulate-2.json", "20250213-09:32", {
            "老周": _agent("老周"), "沈砚之": _agent("沈砚之"),
        })
        events = generate_decision_events(str(tmp_path))
        ids = [e["id"] for e in events]
        assert len(ids) == 4
        assert len(set(ids)) == 4  # 全局唯一(修复:不再按文件序号重复)

    def test_ivd_fields_exported(self, tmp_path):
        _write_checkpoint(str(tmp_path), "simulate-1.json", "20250213-09:30", {
            "老周": _agent("老周",
                          alignment={"Maximize Returns": 0.7, "Risk Aversion": 0.3},
                          tendency={"Maximize Returns": 0.8, "Risk Aversion": 0.2}),
        })
        events = generate_decision_events(str(tmp_path))
        ev = events[0]
        assert ev["goal_score"] == pytest.approx(0.5)  # (0.7+0.3)/2
        assert ev["goal_alignment"] == {"Maximize Returns": 0.7, "Risk Aversion": 0.3}
        assert ev["value_tendency"] == {"Maximize Returns": 0.8, "Risk Aversion": 0.2}

    def test_location_uses_ascii_comma(self, tmp_path):
        _write_checkpoint(str(tmp_path), "simulate-1.json", "20250213-09:30", {
            "老周": _agent("老周"),
        })
        events = generate_decision_events(str(tmp_path))
        assert events[0]["location"] == "the Ville,资料室"
        assert "，" not in events[0]["location"]

    def test_decisions_json_not_treated_as_checkpoint(self, tmp_path):
        _write_checkpoint(str(tmp_path), "simulate-1.json", "20250213-09:30", {
            "老周": _agent("老周"),
        })
        # 目录里放一个已有的 decisions.json(模拟重复导出场景)
        with open(os.path.join(str(tmp_path), "decisions.json"), "w", encoding="utf-8") as f:
            json.dump({"not": "a checkpoint"}, f)
        with open(os.path.join(str(tmp_path), "interventions.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        events = generate_decision_events(str(tmp_path))
        assert len(events) == 1  # 只有 simulate-1.json 被读取

    def test_export_decision_stream(self, tmp_path):
        _write_checkpoint(str(tmp_path), "simulate-1.json", "20250213-09:30", {
            "老周": _agent("老周"),
        })
        out = os.path.join(str(tmp_path), "decisions.json")
        export_decision_stream(str(tmp_path), out, simulation="test-sim", roles={"老周": "投资人"})
        with open(out, "r", encoding="utf-8") as f:
            stream = json.load(f)
        assert stream["simulation"] == "test-sim"
        assert stream["total_steps"] == 1
        assert stream["events"][0]["role"] == "投资人"
