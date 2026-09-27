# -*- coding: utf-8 -*-
"""config_tool 目录解析:环境变量 → 本地设置文件 → 自动发现(2026-09-27)。

背景:2026-09-22 为收口反向依赖把"探测兄弟目录"整个删掉、只认 CASE_ENGINE_DIR,
结果是"开箱即废"(用户不会设环境变量)。现在解析顺序为:
显式参数 → 环境变量 → 本地设置文件 → 自动发现(须含 marker 目录),且**写明来源**。
本文件把这条链路的优先级钉住。
"""
import os
import sys

_this = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_this)                       # <mavis>
_TOOL = os.path.join(_REPO, "config_tool")
if _TOOL not in sys.path:
    sys.path.insert(0, _TOOL)

import engine_bridge as eb  # noqa: E402


def _fake_dir_with_marker(tmp_path, name, marker):
    d = tmp_path / name
    (d / marker).mkdir(parents=True)
    return str(d)


def test_explicit_beats_everything(monkeypatch):
    monkeypatch.setenv(eb.ENV_DIR, str("C:/from-env"))
    r = eb.resolve_dir(explicit="C:/from-explicit")
    assert r["source"] == "参数" and r["dir"].replace("\\", "/").endswith("from-explicit")


def test_env_beats_setting_and_autodiscover(tmp_path, monkeypatch):
    monkeypatch.setenv(eb.ENV_DIR, "C:/from-env")
    monkeypatch.setattr(eb, "SETTING_FILE", str(tmp_path / ".cfg.json"))
    eb.write_settings(engine_dir="C:/from-setting")
    monkeypatch.setattr(eb, "auto_candidates", lambda: ["C:/from-auto"])
    r = eb.resolve_dir()
    assert r["source"] == eb.ENV_DIR, r


def test_setting_file_used_when_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv(eb.ENV_DIR, raising=False)
    monkeypatch.setattr(eb, "SETTING_FILE", str(tmp_path / ".cfg.json"))
    monkeypatch.setattr(eb, "auto_candidates", lambda: [])
    eb.write_settings(engine_dir="C:/from-setting")
    r = eb.resolve_dir()
    assert r["source"] == "设置文件" and r["dir"].replace("\\", "/").endswith("from-setting")


def test_autodiscover_picks_only_dir_with_marker(tmp_path, monkeypatch):
    monkeypatch.delenv(eb.ENV_DIR, raising=False)
    monkeypatch.setattr(eb, "SETTING_FILE", str(tmp_path / ".cfg.json"))
    good = _fake_dir_with_marker(tmp_path, "prov", eb.PKG)
    monkeypatch.setattr(eb, "auto_candidates",
                        lambda: [str(tmp_path / "no-marker"), good])
    r = eb.resolve_dir()
    assert r["source"] == "自动发现" and r["dir"] == os.path.abspath(good)


def test_not_found_is_reported_not_guessed(tmp_path, monkeypatch):
    monkeypatch.delenv(eb.ENV_DIR, raising=False)
    monkeypatch.setattr(eb, "SETTING_FILE", str(tmp_path / ".cfg.json"))
    monkeypatch.setattr(eb, "auto_candidates", lambda: [])
    r = eb.resolve_dir()
    assert r["configured"] is False and r["dir"] == ""
    assert eb.status()["available"] is False
    assert eb.reason(), "不可用时必须给出可读原因(不静默)"


def test_platform_marker_needs_frontend_and_engine(tmp_path, monkeypatch):
    """平台根的 marker 是两个:只含 frontend 的"别的仓"不能被误认(2026-09-27 踩过)。"""
    monkeypatch.delenv(eb.ENV_DIR, raising=False)
    monkeypatch.delenv("MAVIS_PLATFORM_DIR", raising=False)
    monkeypatch.setattr(eb, "SETTING_FILE", str(tmp_path / ".cfg.json"))
    only_front = tmp_path / "other-repo"
    (only_front / "frontend").mkdir(parents=True)          # 只有 frontend → 不算
    real = tmp_path / "plat"
    (real / "frontend").mkdir(parents=True)
    (real / eb.PKG).mkdir(parents=True)                     # frontend + 引擎包 → 算
    monkeypatch.setattr(eb, "auto_candidates",
                        lambda: [str(only_front), str(real)])
    r = eb.resolve_dir(env_keys=("MAVIS_PLATFORM_DIR", eb.ENV_DIR),
                       setting_key="platform_dir", marker=("frontend", eb.PKG))
    assert r["source"] == "自动发现" and r["dir"] == os.path.abspath(str(real))


def test_settings_roundtrip_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "SETTING_FILE", str(tmp_path / ".cfg.json"))
    eb.write_settings(engine_dir="C:/e", platform_dir="C:/p")
    assert eb._read_settings() == {"engine_dir": "C:/e", "platform_dir": "C:/p"}
    eb.write_settings(engine_dir="")            # 空串 = 清除该项
    assert eb._read_settings() == {"platform_dir": "C:/p"}
