"""config_tool.scenario_builder 单元测试:确定性构建 + 导出 + 引擎校验 + 落盘。"""
import json
import os
import sys

import pytest

CONFIG_TOOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config_tool")
sys.path.insert(0, CONFIG_TOOL_DIR)
import scenario_builder  # noqa: E402

PLATFORM_DIR = os.path.join(os.path.dirname(CONFIG_TOOL_DIR), "..", "provenance", "provenance")
PLATFORM_DIR = os.path.normpath(PLATFORM_DIR)


def _sample_form(**over):
    form = {
        "engine": "experiment-eval",
        "case_id": "case03_test",
        "name": "测试中立场景",
        "description": "单元测试场景",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
        "roles": [
            {"id": "analyst", "display_name": "分析师", "type": "ai_tool",
             "llm": "local", "system_prompt": "给出结论", "max_tokens": 2048, "temperature": 0.4},
            {"id": "client", "display_name": "客户", "type": "user",
             "llm": "local", "system_prompt": "", "max_tokens": 1024, "temperature": 0.5},
        ],
        "state_schema": [{"field": "approved", "initial": False, "type": "bool"},
                         {"field": "budget", "initial": 100000, "type": "float"}],
        "branch_default": "C",
        "branch_no_buy": "不批准,反对\n拒绝",
        "branch_conditional": "分阶段\n小规模",
        "branch_fallback_map": {"A": {"approved": True}},
        "cons_buy_words": "批准\n同意",
        "cons_negators": "反对\n不",
    }
    form.update(over)
    return form


def test_parse_words_normalizes_separators():
    assert scenario_builder.parse_words("a\nb,c，d；e、f") == ["a", "b", "c", "d", "e", "f"]
    assert scenario_builder.parse_words(None) == []
    assert scenario_builder.parse_words(" 已去重\n已去重 ") == ["已去重"]


def test_build_scenario_meta_and_roles():
    cfg = scenario_builder.build_scenario(_sample_form())
    meta = cfg["meta"]
    assert meta["case_id"] == "case03_test"
    assert meta["name"] == "测试中立场景"
    assert meta["engine"] == "experiment-eval"
    assert meta["start_date"] == "2026-09-01"
    assert len(cfg["roles"]) == 2
    assert cfg["roles"][0]["id"] == "analyst"
    assert cfg["roles"][0]["max_tokens"] == 2048
    assert cfg["world"]["state_schema"]["approved"] == {"initial": False, "type": "bool"}


def test_build_scenario_branch_and_consistency():
    cfg = scenario_builder.build_scenario(_sample_form())
    assert cfg["branch"]["default_branch"] == "C"
    assert "不批准" in cfg["branch"]["no_buy"]
    assert cfg["branch"]["fallback_map"] == {"A": {"approved": True}}
    assert "批准" in cfg["consistency"]["buy_words"]


def test_dump_load_roundtrip():
    """导出的 YAML 再 safe_load 应等于原 cfg(确定性、可 diff)。"""
    import yaml
    cfg = scenario_builder.build_scenario(_sample_form())
    text = scenario_builder.dump_scenario_yaml(cfg)
    assert "测试中立场景" in text          # 中文明文,未转义成 \uXXXX
    assert "\\u" not in text
    loaded = yaml.safe_load(text)
    assert loaded == cfg


def test_validate_with_case_engine_schema():
    """生成场景能通过 case_engine 的 Validate 契约(引擎可用时)。"""
    cfg = scenario_builder.build_scenario(_sample_form())
    ok, errors = scenario_builder.validate_scenario(cfg, PLATFORM_DIR)
    assert ok, errors
    assert errors == []


def test_validate_fails_on_bad_case_id():
    cfg = scenario_builder.build_scenario(_sample_form(case_id=" 非法 / 空格"))
    ok, errors = scenario_builder.validate_scenario(cfg, PLATFORM_DIR)
    assert ok is False
    assert any("case_id" in e for e in errors)


def test_save_writes_to_cases_and_loads_back(monkeypatch, tmp_path):
    """保存后落盘 cases/<case_id>/scenario.yaml,且能被 case_engine 加载。"""
    os.environ["CASE_ENGINE_CASES_ROOT"] = str(tmp_path / "_cases")
    from case_engine.config import load_yaml
    cfg = scenario_builder.build_scenario(_sample_form())
    path = scenario_builder.save_scenario(PLATFORM_DIR, cfg)
    assert os.path.isfile(path)
    loaded = load_yaml(path)
    assert loaded.case_id == "case03_test"
    assert loaded.engine == "experiment-eval"
    assert len(loaded.roles) == 2