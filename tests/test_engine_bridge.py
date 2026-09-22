# -*- coding: utf-8 -*-
"""config_tool.engine_bridge 单元测试(2026-09-22 反向依赖收口)。

钉住三件事:
1. 引擎位置**只认显式声明** `CASE_ENGINE_DIR`(或调用方显式传入),不探测兄弟目录;
2. 未声明 / 目录不存在 / 目录里没有引擎包 —— 一律 available=False + **可读原因**,
   绝不静默降级(这是产线铁律"不允许静默"的可执行断言);
3. **引用面棘轮**:config_tool 里除 engine_bridge(及一处兼容别名)外,不得再出现
   引擎包名/兄弟仓名 —— 防止接触点重新散落。
"""
import os
import re
import sys

import pytest

CONFIG_TOOL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config_tool")
sys.path.insert(0, CONFIG_TOOL_DIR)

import engine_bridge  # noqa: E402

ENGINE_DIR = os.path.normpath(os.path.join(
    os.path.dirname(CONFIG_TOOL_DIR), "..", "provenance", "provenance"))


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv("CASE_ENGINE_DIR", raising=False)


def test_unset_env_is_not_available_with_readable_reason(no_env):
    st = engine_bridge.status()
    assert st["configured"] is False
    assert st["available"] is False
    assert engine_bridge.ENV_DIR in st["reason"]
    assert engine_bridge.api() is None
    assert engine_bridge.engine_names() == {}
    assert engine_bridge.validate({"meta": {"case_id": "x"}}) is None  # 判不了 ≠ 通过


def test_env_var_declares_engine_dir(monkeypatch):
    monkeypatch.setenv("CASE_ENGINE_DIR", ENGINE_DIR)
    st = engine_bridge.status()
    if not os.path.isdir(os.path.join(ENGINE_DIR, engine_bridge.PKG)):
        pytest.skip("引擎包不在预期位置")
    assert st["available"] is True, st["reason"]
    assert os.path.normcase(st["dir"]) == os.path.normcase(ENGINE_DIR)
    assert st["reason"] == ""
    assert "experiment-eval" in engine_bridge.engine_names()


def test_missing_dir_reports_reason(no_env):
    st = engine_bridge.status(os.path.join(CONFIG_TOOL_DIR, "no_such_dir_xyz"))
    assert st["available"] is False
    assert "不存在" in st["reason"]


def test_dir_without_engine_package_reports_reason(tmp_path, no_env):
    st = engine_bridge.status(str(tmp_path))
    assert st["available"] is False
    assert engine_bridge.PKG in st["reason"]


def test_banner_prints_resolved_path(monkeypatch):
    monkeypatch.setenv("CASE_ENGINE_DIR", ENGINE_DIR)
    text = engine_bridge.banner()
    if engine_bridge.available():
        assert ENGINE_DIR in text
    else:
        assert engine_bridge.ENV_DIR in text      # 失败也要说清缺什么


def test_broken_engine_package_reports_import_error(tmp_path, no_env, monkeypatch):
    """目录里有个同名包但导入会炸 —— 必须报原因,而不是当作可用。

    (用假包名,避免真引擎已在 sys.modules 里把它遮住。)
    """
    fake = "zzr_fake_engine_pkg_for_test"
    monkeypatch.setattr(engine_bridge, "PKG", fake)
    pkg = tmp_path / fake
    pkg.mkdir()
    (pkg / "__init__.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    st = engine_bridge.status(str(tmp_path))
    assert st["available"] is False
    assert "boom" in st["reason"] or "导入" in st["reason"]


def test_reverse_dependency_surface_does_not_scatter():
    """棘轮:引擎包名/兄弟仓名只准出现在 engine_bridge 一个文件里。"""
    pat = re.compile(r"case_engine|provenance")
    offenders, hits = {}, 0
    for dirpath, dirs, files in os.walk(CONFIG_TOOL_DIR):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "build")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            src = open(path, encoding="utf-8", errors="replace").read()
            n = len(pat.findall(src))
            if n:
                rel = os.path.relpath(path, CONFIG_TOOL_DIR).replace("\\", "/")
                offenders[rel] = n
                hits += n
    assert set(offenders) <= {"engine_bridge.py"}, \
        "反向依赖又散开了: {}".format(sorted(offenders))
    assert hits <= 4, "反向依赖引用点涨了: {} > 4(只准降)。当前分布: {}".format(
        hits, offenders)
