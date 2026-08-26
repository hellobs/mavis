# -*- coding: utf-8 -*-
"""Governance 制度约束层测试(IVD 重构)

覆盖 mavisframework.runtime.governance.Governance:
- 加载 governance.json
- get_constraints / all_constraints
- set_constraints 写回并持久化
- 未配置角色返回空
"""
import json

import pytest

from mavisframework.runtime.governance import Governance


class TestGovernance:
    def _write(self, tmp_path, data):
        p = tmp_path / "governance.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(p)

    def test_load_and_query(self, tmp_path):
        p = self._write(tmp_path, {
            "roles": {
                "老周": {"Maximize Returns": 0.7, "Risk Aversion": 0.3},
                "沈砚之": {"Steady Returns": 0.6, "Risk Aversion": 0.4},
            }
        })
        gov = Governance()
        gov.load(p)
        assert gov.get_constraints("老周") == {"Maximize Returns": 0.7, "Risk Aversion": 0.3}
        assert set(gov.all_constraints().keys()) == {"老周", "沈砚之"}

    def test_missing_role_returns_empty(self, tmp_path):
        p = self._write(tmp_path, {"roles": {"老周": {}}})
        gov = Governance()
        gov.load(p)
        assert gov.get_constraints("不存在的人") == {}
        assert gov.has("不存在的人") is False

    def test_set_constraints_persists(self, tmp_path):
        p = self._write(tmp_path, {"roles": {"老周": {"Maximize Returns": 0.7, "Risk Aversion": 0.3}}})
        gov = Governance()
        gov.load(p)
        gov.set_constraints("老周", {"Maximize Returns": 0.5, "Risk Aversion": 0.5})
        # 内存中已更新
        assert gov.get_constraints("老周") == {"Maximize Returns": 0.5, "Risk Aversion": 0.5}
        # 落盘后重新加载一致
        gov2 = Governance()
        gov2.load(p)
        assert gov2.get_constraints("老周") == {"Maximize Returns": 0.5, "Risk Aversion": 0.5}

    def test_set_constraints_adds_new_role(self, tmp_path):
        p = self._write(tmp_path, {"roles": {}})
        gov = Governance()
        gov.load(p)
        gov.set_constraints("陈慕白", {"Research Rigor": 0.7, "Timeliness": 0.3})
        assert gov.has("陈慕白")
        assert gov.get_constraints("陈慕白")["Research Rigor"] == 0.7

    def test_missing_file_defaults_empty(self):
        gov = Governance(path="D:/no/such/governance.json")
        assert gov.all_constraints() == {}
