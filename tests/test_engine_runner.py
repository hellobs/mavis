"""config_tool.engine_runner 单元测试:界面运行器可定位引擎并切场景运行。

引擎目录自 2026-09-22 起**只认显式声明**:测试里显式设 CASE_ENGINE_DIR(不再靠探测兄弟目录)。
"""
import os
import sys

import pytest

CONFIG_TOOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config_tool")
sys.path.insert(0, CONFIG_TOOL_DIR)

PLATFORM_DIR = os.path.join(os.path.dirname(CONFIG_TOOL_DIR), "..", "provenance", "provenance")
PLATFORM_DIR = os.path.normpath(PLATFORM_DIR)
CASES_ROOT = os.path.join(PLATFORM_DIR, "cases")

# 测试也走"显式声明"这条唯一路径(缺了它本文件的用例本就该失败,不是静默降级)
os.environ.setdefault("CASE_ENGINE_DIR", PLATFORM_DIR)

# 但 mavis 是**独立框架仓**:单独 clone 时没有兄弟仓 ../provenance,这些用例在
# mavis CI 里必然失败。此处**显式跳过**并写明原因(与 test_engine_bridge.py 同口径),
# 不让"单独 clone mavis 跑 pytest"一片红;跨仓集成验证归 provenance CI(它有两侧)。
pytestmark = pytest.mark.skipif(
    not os.path.isdir(os.path.join(PLATFORM_DIR, "case_engine")),
    reason="provenance 仓不在预期位置(../provenance/provenance);跨仓集成验证归 provenance CI")

import engine_bridge  # noqa: E402
import engine_runner  # noqa: E402


def _cases_env(root, monkeypatch):
    os.environ["CASE_ENGINE_CASES_ROOT"] = root or CASES_ROOT


def test_case_engine_is_located():
    st = engine_bridge.status()
    assert st["available"] is True, st["reason"]
    assert os.path.normcase(st["dir"]) == os.path.normcase(PLATFORM_DIR)


def test_list_cases_finds_case00_and_case01(monkeypatch):
    _cases_env(None, monkeypatch)
    cases = engine_runner.list_cases(PLATFORM_DIR)
    ids = {c["case_id"] for c in cases}
    assert "case00_village" in ids
    assert "case01_stock" in ids


def test_run_case01_experiment_eval(monkeypatch):
    """case01 默认引擎 experiment-eval:真跑返回分支/一致性/时间线。"""
    _cases_env(None, monkeypatch)
    ok, summary, errors = engine_runner.run_case(
        PLATFORM_DIR, "case01_stock",
        input_text="我建议分阶段先小规模试点")   # → C → Timeline A
    assert ok, errors
    assert summary["_engine_id"] == "experiment-eval"
    assert summary["run_type"] == "rule-dryrun"
    assert summary["branch"] == "C"
    assert summary["consistency"]["verdict"] in ("consistent", "unknown")
    assert summary["timeline"], "case01 应产出时间线"
    assert summary["timeline"][0]["date"] == "2026-08-27"   # T0 咨询日


def test_run_case00_sandbox_value_assembly_check(monkeypatch):
    """case00 默认引擎 sandbox-value:跑装配预检(不越权假跑)。"""
    _cases_env(None, monkeypatch)
    ok, summary, errors = engine_runner.run_case(PLATFORM_DIR, "case00_village")
    assert ok, errors
    assert summary["_engine_id"] == "sandbox-value"
    assert summary["run_type"] == "assembly-check"
    assert "all_ok" in summary
    assert "checks" in summary and "assets" in summary["checks"]
    assert summary["all_ok"] is True


def test_run_missing_case_reports_error(monkeypatch):
    _cases_env(None, monkeypatch)
    ok, _, errors = engine_runner.run_case(PLATFORM_DIR, "does_not_exist")
    assert ok is False
    assert any("场景不存在" in e for e in errors)