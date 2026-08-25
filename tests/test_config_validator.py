# -*- coding: utf-8 -*-
"""config validator 配置校验测试

覆盖 mavisframework.config.validator:
- validate_agents:必填字段、坐标类型、地图范围、spatial 地址存在
- validate_relationships:agents 数量、引用角色存在、frequency 枚举
- validate_story:必填字段、time 格式、importance 范围、targets 存在
"""
import pytest

from mavisframework.config.validator import (
    validate_agents,
    validate_relationships,
    validate_story,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _maze(width=30, height=20):
    """最小 maze 配置(tile 地址含 world 前缀对齐 validator)"""
    return {
        "world": "the Ville",
        "size": [width, height],
        "tiles": [
            {"coord": [4, 4], "address": ["投资咨询中心"], "collision": False},
            {"coord": [5, 5], "address": ["投资咨询中心", "资料室"], "collision": False},
            {"coord": [6, 6], "address": ["投资咨询中心", "资料室", "办公桌"], "collision": False},
        ],
    }


def _agent(name="老周", coord=(10, 6), living_area=None, tree=None):
    """构造一个完整 agent 配置(可覆盖)"""
    living_area = living_area or ["the Ville", "投资咨询中心", "资料室"]
    tree = tree or {"the Ville": {"投资咨询中心": {"资料室": ["办公桌"]}}}
    return {
        "name": name,
        "coord": list(coord),
        "currently": "空闲",
        "scratch": {
            "age": 35,
            "innate": "",
            "learned": "",
            "lifestyle": "",
            "daily_plan": "",
        },
        "spatial": {"address": {"living_area": living_area}, "tree": tree},
    }


# ---------------------------------------------------------------------------
# validate_agents
# ---------------------------------------------------------------------------

class TestValidateAgents:
    def test_valid_agent(self):
        assert validate_agents({"老周": _agent()}, _maze()) == []

    def test_missing_required_field(self):
        agent = _agent()
        del agent["currently"]
        errs = validate_agents({"老周": agent}, _maze())
        assert any("currently" in e for e in errs)

    def test_coord_out_of_map(self):
        agent = _agent(coord=(999, 999))
        errs = validate_agents({"老周": agent}, _maze())
        assert any("coord" in e and "超出" in e for e in errs)

    def test_coord_must_be_list(self):
        agent = _agent()
        agent["coord"] = "10,6"
        errs = validate_agents({"老周": agent}, _maze())
        assert any("coord" in e for e in errs)

    def test_spatial_address_exists_in_maze(self):
        # living_area 的树地址应在 maze 中存在
        agent = _agent(tree={"the Ville": {"不存在区域": ["某处"]}})
        errs = validate_agents({"老周": agent}, _maze())
        assert any("spatial" in e for e in errs)

    def test_multiple_agents(self):
        agents = {"老周": _agent(), "沈砚之": _agent(name="沈砚之", coord=(5, 5))}
        assert validate_agents(agents, _maze()) == []

    def test_goals_sum_to_one(self):
        agent = _agent()
        agent["goals"] = {"收益最大化": 0.7, "风险规避": 0.3}
        assert validate_agents({"老周": agent}, _maze()) == []

    def test_goals_sum_not_one(self):
        agent = _agent()
        agent["goals"] = {"收益最大化": 0.7, "风险规避": 0.4}  # 总和 1.1
        errs = validate_agents({"老周": agent}, _maze())
        assert any("goals" in e and "总和" in e for e in errs)

    def test_goals_empty(self):
        agent = _agent()
        agent["goals"] = {}
        errs = validate_agents({"老周": agent}, _maze())
        assert any("goals" in e for e in errs)

    def test_goals_non_numeric(self):
        agent = _agent()
        agent["goals"] = {"收益最大化": "high"}
        errs = validate_agents({"老周": agent}, _maze())
        assert any("goals" in e for e in errs)


# ---------------------------------------------------------------------------
# validate_relationships
# ---------------------------------------------------------------------------

class TestValidateRelationships:
    AGENTS = {"老周": {}, "沈砚之": {}}

    def test_valid_relationship(self):
        rels = [{"agents": ["老周", "沈砚之"], "type": "客户-顾问",
                 "frequency": "medium"}]
        assert validate_relationships(rels, set(self.AGENTS)) == []

    def test_missing_required(self):
        rels = [{"agents": ["老周"]}]  # 缺 type
        errs = validate_relationships(rels, set(self.AGENTS))
        assert any("type" in e for e in errs)

    def test_referenced_agent_not_exist(self):
        rels = [{"agents": ["老周", "不存在的人"], "type": "x"}]
        errs = validate_relationships(rels, set(self.AGENTS))
        assert any("不存在的人" in e for e in errs)

    def test_invalid_frequency(self):
        rels = [{"agents": ["老周", "沈砚之"], "type": "x", "frequency": "always"}]
        errs = validate_relationships(rels, set(self.AGENTS))
        assert any("frequency" in e for e in errs)

    def test_empty_relations_ok(self):
        assert validate_relationships([], set(self.AGENTS)) == []


# ---------------------------------------------------------------------------
# validate_story
# ---------------------------------------------------------------------------

class TestValidateStory:
    AGENTS = {"老周": {}, "沈砚之": {}}

    def _ev(self, **over):
        ev = {"id": "s-001", "time": "10:00", "event_type": "市场波动",
              "content": "新能源板块波动", "importance": 8,
              "targets": ["all"]}
        ev.update(over)
        return ev

    def test_valid_story(self):
        assert validate_story([self._ev()], set(self.AGENTS)) == []

    def test_missing_required(self):
        ev = self._ev()
        del ev["content"]
        errs = validate_story([ev], set(self.AGENTS))
        assert any("content" in e for e in errs)

    def test_time_format(self):
        ev = self._ev(time="25:99")
        errs = validate_story([ev], set(self.AGENTS))
        assert any("time" in e for e in errs)

    def test_importance_range(self):
        ev = self._ev(importance=99)
        errs = validate_story([ev], set(self.AGENTS))
        assert any("importance" in e for e in errs)

    def test_targets_exist(self):
        ev = self._ev(targets=["不存在的人"])
        errs = validate_story([ev], set(self.AGENTS))
        assert any("不存在的人" in e for e in errs)

    def test_empty_story_ok(self):
        assert validate_story([], set(self.AGENTS)) == []
