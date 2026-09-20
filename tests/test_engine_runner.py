"""config_tool.engine_runner 单元测试:界面运行器可定位 case_engine 并切场景运行。"""
import os
import sys

CONFIG_TOOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config_tool")
sys.path.insert(0, CONFIG_TOOL_DIR)
import engine_runner  # noqa: E402

PLATFORM_DIR = os.path.join(os.path.dirname(CONFIG_TOOL_DIR), "..", "provenance", "provenance")
PLATFORM_DIR = os.path.normpath(PLATFORM_DIR)
CASES_ROOT = os.path.join(PLATFORM_DIR, "cases")


def _cases_env(root, monkeypatch):
    os.environ["CASE_ENGINE_CASES_ROOT"] = root or CASES_ROOT


def test_case_engine_is_located():
    assert engine_runner._case_engine_imports(PLATFORM_DIR) is not None


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