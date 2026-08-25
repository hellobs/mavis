# -*- coding: utf-8 -*-
"""config loader 配置加载测试

覆盖 mavisframework.config.loader:
- load_config:生成新模拟配置(config_path/assets_root 注入)
- load_config_from_log:从存档恢复(时间推进、config_path 重写)
- _resolve_assets_root:环境变量/参数解析
"""
import json
import os

import pytest

from mavisframework.config.loader import (
    load_config,
    load_config_from_log,
    _resolve_assets_root,
)


# ---------------------------------------------------------------------------
# _resolve_assets_root
# ---------------------------------------------------------------------------

class TestResolveAssetsRoot:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("MAVIS_ASSETS_ROOT", raising=False)
        assert _resolve_assets_root(None) == os.path.join("assets", "village")

    def test_explicit_arg_wins(self, monkeypatch):
        monkeypatch.setenv("MAVIS_ASSETS_ROOT", "from/env")
        assert _resolve_assets_root("explicit") == "explicit"

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("MAVIS_ASSETS_ROOT", "assets/custom")
        assert _resolve_assets_root(None) == os.path.join("assets", "custom")

    def test_empty_returns_empty(self):
        assert _resolve_assets_root("") == ""


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_load_config_basic(self, tmp_path, monkeypatch):
        # 写一个最小 data/config.json
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "agent": {"percept": {"vision_r": 8}, "think": {"llm": {"provider": "ollama"}}}
        }), encoding="utf-8")
        monkeypatch.setenv("MAVIS_CONFIG_PATH", str(cfg_file))

        cfg = load_config("20250213-09:30", 15, ["沈砚之", "老周"])
        assert cfg["stride"] == 15
        assert cfg["time"]["start"] == "20250213-09:30"
        assert "沈砚之" in cfg["agents"] and "老周" in cfg["agents"]
        # agent_base 来自 config.json 的 agent 字段
        assert cfg["agent_base"]["percept"]["vision_r"] == 8
        # maze/config_path 拼 assets_root
        assert cfg["maze"]["path"].endswith("maze.json")
        assert cfg["agents"]["沈砚之"]["config_path"].endswith(
            os.path.join("沈砚之", "agent.json")
        )

    def test_load_config_assets_root_override(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"agent": {}}), encoding="utf-8")
        monkeypatch.setenv("MAVIS_CONFIG_PATH", str(cfg_file))
        cfg = load_config(assets_root="assets/v2")
        assert cfg["maze"]["path"].startswith(os.path.join("assets", "v2"))

    def test_load_config_no_agents(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"agent": {}}), encoding="utf-8")
        monkeypatch.setenv("MAVIS_CONFIG_PATH", str(cfg_file))
        cfg = load_config()
        assert cfg["agents"] == {}


# ---------------------------------------------------------------------------
# load_config_from_log
# ---------------------------------------------------------------------------

class TestLoadConfigFromLog:
    def _write_log(self, folder):
        """写一个最小模拟存档(时间/stride/agents)"""
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "simulate-202502130930.json"), "w", encoding="utf-8") as f:
            json.dump({
                "time": "20250213-09:30",
                "stride": 15,
                "agents": {"沈砚之": {"coord": [10, 6]}},
            }, f)
        return folder

    def test_load_from_log_advances_time(self, tmp_path):
        folder = self._write_log(str(tmp_path))
        cfg = load_config_from_log(folder)
        assert cfg is not None
        # 时间推进 stride:09:30 + 15min = 09:45
        assert cfg["time"]["start"] == "20250213-09:45"
        assert "沈砚之" in cfg["agents"]
        # config_path 被重写为 assets_root 下
        assert cfg["agents"]["沈砚之"]["config_path"].endswith(
            os.path.join("沈砚之", "agent.json")
        )

    def test_load_from_log_empty_folder(self, tmp_path):
        assert load_config_from_log(str(tmp_path)) is None

    def test_load_from_log_ignores_conversation(self, tmp_path):
        folder = self._write_log(str(tmp_path))
        with open(os.path.join(folder, "conversation.json"), "w", encoding="utf-8") as f:
            json.dump({"20250213-09:30": []}, f)
        cfg = load_config_from_log(folder)
        assert cfg is not None
