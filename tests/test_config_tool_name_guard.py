# -*- coding: utf-8 -*-
"""config_tool 角色名守卫(2026-09-23 安全体检)。

背景:`save_agent()` / `upgrade_agent()` 原来只去掉制表符与换行,名字里带 `..\\..`
就能在 `AGENTS_ROOT` **之外**建目录、写 `agent.json`(角色名还进 `portrait` 路径)。
`delete_agent` 早就有守卫,这两处漏了。现在三处共用 `_safe_agent_name()`。

这里只测**不出门**的纯函数与写入拦截:不碰真目录、不发 HTTP。
"""
import os
import sys

import pytest

CONFIG_TOOL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config_tool")
sys.path.insert(0, CONFIG_TOOL_DIR)

import app as cfg  # noqa: E402


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
    assert list(target.iterdir()) == [] if target.exists() else True
