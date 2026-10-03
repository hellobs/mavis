# -*- coding: utf-8 -*-
"""Scratch 的模板目录可按实例指定(2026-10-03)。

`MAVIS_PROMPT_DIR` 是**环境变量 = 进程级唯一**:同一进程里两个不同场景无法
各用各的模板目录。这在"一个进程挂多个场景"时是硬墙 —— 此前只能靠进程隔离
绕开,而 provenance 的 case00/case01 正是同端口互斥切换的。

`template_dir` 参数是**纯新增**:为 None 时行为与之前逐字节一致(读环境变量
再退回包内 `prompts/`)。
"""
import os

import pytest

from mavisframework.prompt.scratch import Scratch, _DEFAULT_TEMPLATE_DIR


def _scratch(config=None, **kw):
    """`config` 是 Scratch 的第三个位置参数;`template_dir` 是第四/第五个关键字。"""
    return Scratch("老周", "书房", config or {}, **kw)


class TestTemplateDir:
    def test_default_unchanged(self):
        """不传 = 原行为:环境变量优先,否则包内 prompts/。"""
        s = _scratch()
        if os.environ.get("MAVIS_PROMPT_DIR"):
            assert s.template_path == os.environ["MAVIS_PROMPT_DIR"]
        else:
            assert s.template_path == _DEFAULT_TEMPLATE_DIR

    def test_explicit_template_dir_wins(self):
        s = _scratch(template_dir="/tmp/scenario-A")
        assert s.template_path == "/tmp/scenario-A"

    def test_explicit_wins_over_env(self):
        """显式参数优先于环境变量 —— 这样一个进程里能同时有多个目录。"""
        os.environ["MAVIS_PROMPT_DIR"] = "/tmp/from-env"
        try:
            assert _scratch().template_path == "/tmp/from-env"
            assert _scratch(template_dir="/tmp/explicit").template_path == "/tmp/explicit"
        finally:
            del os.environ["MAVIS_PROMPT_DIR"]

    def test_two_instances_can_differ(self):
        """核心价值:同进程两个实例各用各的模板目录。"""
        a = _scratch(template_dir="/tmp/scene-A")
        b = _scratch(template_dir="/tmp/scene-B")
        assert a.template_path != b.template_path

    def test_empty_string_falls_back_to_default(self):
        """空串视为"没指定",不要造出一个指向 CWD 的路径。"""
        s = _scratch(template_dir="")
        assert s.template_path == os.environ.get("MAVIS_PROMPT_DIR", _DEFAULT_TEMPLATE_DIR)

    def test_timer_injection_still_works(self):
        """顺带确认 timer 注入没被新参数挤掉。"""
        from mavisframework.core.timer import Timer
        t = Timer()
        s = Scratch("老周", "书房", {}, timer=t, template_dir="/tmp/x")
        assert s._timer is t and s._timer_injected is True
        assert s.template_path == "/tmp/x"


class TestAgentTemplateDirPassthrough:
    def test_agent_config_can_pass_template_dir(self):
        """Agent 配置能透传 template_dir(多场景同进程的入口)。"""
        import inspect
        from mavisframework.core.agent_core import Agent
        src = inspect.getsource(Agent.__init__)
        assert "template_dir" in src, "Agent 构造应当把 template_dir 透传给 Scratch"
        # Agent 从 config 顶层读,而不是从 scratch 子字典读
        assert 'config.get("template_dir")' in src
