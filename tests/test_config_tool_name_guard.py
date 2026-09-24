# -*- coding: utf-8 -*-
"""config_tool 路径段守卫(2026-09-23 安全体检,第三轮补齐)。

背景:
- `save_agent()` / `upgrade_agent()` 原来只去掉制表符与换行,角色名里带 `..\\..`
  就能在 `AGENTS_ROOT` **之外**建目录、写 `agent.json`(`delete_agent` 早有守卫);
- 第三轮又发现**场景 id 同样没校验**:`case_id` 直接来自表单,`save_scenario()` 却
  直接 `os.path.join(root, case_id)` + `makedirs` —— 写法与上面一模一样。

现在角色名 / 场景 id 共用一处策略(`_safe_path_segment` / `safe_case_id`)。
这里只测**不出门**的纯函数与写入拦截:不碰真目录、不发 HTTP。
"""
import os
import sys

import pytest

CONFIG_TOOL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config_tool")
sys.path.insert(0, CONFIG_TOOL_DIR)

import app as cfg  # noqa: E402
import scenario_builder  # noqa: E402


def test_normal_names_pass_and_are_cleaned():
    assert cfg._safe_agent_name("  林薇 ") == "林薇"
    assert cfg._safe_agent_name("AI Advisor") == "AI Advisor"
    assert cfg._safe_agent_name("a\tb") == "ab"      # 制表符/换行是路径不允许的字符


@pytest.mark.parametrize("bad", [
    "", "   ", ".", "..", "../x", "..\\x", "a/b", "a\\b", "C:evil", "/etc/passwd",
])
def test_traversal_and_separators_are_refused(bad):
    with pytest.raises(ValueError):
        cfg._safe_agent_name(bad)


def test_save_agent_refuses_traversal_before_touching_disk(tmp_path):
    """穿越名必须在**建目录之前**就被拒:否则已经写出去了。"""
    target = tmp_path / "agents"
    with pytest.raises(ValueError):
        cfg.save_agent("biz", {"name": "../../escaped", "role_type": "user"},
                       agents_root=str(target))
    assert not (tmp_path / "escaped").exists(), "拒绝之前不许落盘"
    assert not (tmp_path.parent / "escaped").exists()


@pytest.mark.parametrize("bad", ["", "  ", "..", "../x", "a/b", "a\\b", "D:x", "/etc"])
def test_scenario_id_uses_the_same_policy(bad):
    """场景 id 与角色名同一套口径(第三轮发现它原来完全没校验)。"""
    with pytest.raises(ValueError):
        scenario_builder.safe_case_id(bad)
    with pytest.raises(ValueError):
        cfg._safe_path_segment(bad, "场景 id")
    assert scenario_builder.safe_case_id("case01_stock") == "case01_stock"


def test_build_scenario_does_not_raise_but_validate_reports():
    """表单来的 case_id 由 `validate_scenario` 报错(界面可读),不是在构造时抛。

    2026-09-23:我第一版把守卫放进 `build_scenario`,把既有的
    `test_validate_fails_on_bad_case_id` 弄红了 —— 那是**既有设计**:
    表单问题要作为可读错误回到界面,抛异常会变成"构建失败"。
    真正的拦截点是落盘(`save_scenario`)与资源目录(`scenario_assets_dir`)。
    """
    cfg = scenario_builder.build_scenario({"case_id": "../../escaped", "name": "x"})
    assert cfg["meta"]["case_id"] == "../../escaped"     # 构造不改写
    ok, errors = scenario_builder.validate_scenario(cfg, "")
    assert ok is False
    assert any("case_id" in e for e in errors), errors


def test_save_scenario_refuses_before_writing(tmp_path):
    """场景落盘前必须拦:拒绝时 `cases/<穿越>` 一个字节都不许写。"""
    with pytest.raises(ValueError):
        scenario_builder.save_scenario(str(tmp_path),
                                       {"meta": {"case_id": "../../../escaped"}})
    assert not (tmp_path / "cases").exists() or not any((tmp_path / "cases").rglob("*"))


def test_scenario_assets_dir_refuses_traversal():
    with pytest.raises(ValueError):
        cfg.scenario_assets_dir("../../escaped")
