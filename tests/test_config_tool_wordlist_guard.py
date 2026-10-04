# -*- coding: utf-8 -*-
"""词表留空不许静默抹掉原文件(2026-10-04)。

背景:场景保存是 `build_scenario → save_scenario` 的**整份重写**,而 `build_scenario`
对空文本框走 `if words:` —— 空就不写这个键。8060 表单把四类分支词与四张信号词折叠成
选填后,"面板没把值带回来"会直接变成"实验设定被抹掉 + scenario_sha256 变了"。
这里测拦与放行的边界:不发 HTTP、不碰真目录(旧文件内容用 monkeypatch 供)。
"""
import os
import sys

CONFIG_TOOL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config_tool")
sys.path.insert(0, CONFIG_TOOL_DIR)

import app as cfg            # noqa: E402
import scenario_builder      # noqa: E402

# 一份"原 scenario.yaml 已经带全套分支设定"的形状(与 case01_stock 的段结构同形)
_ORIGINAL = {
    "branch": {
        "default_branch": "C",
        "no_buy": ["不建议买"], "refuse": ["无法判断"],
        "conditional": ["小仓位"], "anti_allin": ["不要全仓"],
        "fallback_map": {"A": {"timeline": "A"}},
        "judge_prompt": "你是 Reflection Router。只处理 Reflection 中已经出现的问题。",
    },
    "consistency": {
        "buy_words": ["recommend buying"], "cond_words": ["small position"],
        "negators": ["not"], "neg_phrases": ["advise against"],
    },
}


def _form(**over):
    form = {
        "engine": "experiment-eval", "case_id": "case_x", "name": "X",
        "roles": [{"id": "ai", "display_name": "AI", "type": "ai_tool",
                   "llm": "local", "system_prompt": "", "max_tokens": 2048,
                   "temperature": 0.5}],
    }
    form.update(over)
    return form


def _as_form_words(data):
    """按表单原生形态给出词表(textarea 一行一个),等同编辑时的自动回填。"""
    out = {}
    for key in ("no_buy", "refuse", "conditional", "anti_allin"):
        out["branch_" + key] = cfg._join_words(_ORIGINAL["branch"][key])
    for key in ("buy_words", "cond_words", "negators", "neg_phrases"):
        out["cons_" + key] = cfg._join_words(_ORIGINAL["consistency"][key])
    out["branch_fallback_map"] = _ORIGINAL["branch"]["fallback_map"]
    out["branch_judge_prompt"] = _ORIGINAL["branch"]["judge_prompt"]
    out["branch_default"] = "C"
    return out


def _lost(text):
    """从报错原文里取出"涉及键"集合 —— 断言点名到键,而不是只匹配到 label。"""
    return set(text.split("涉及键:")[-1].split("、"))


def test_new_case_without_existing_file_is_never_blocked(monkeypatch):
    monkeypatch.setattr(cfg, "_load_scenario_dict", lambda case_id: None)
    form = _form()
    assert cfg._wordlist_wipe_errors("brand_new", scenario_builder.build_scenario(form), form) == []


def test_all_wordlists_emptied_against_existing_file_are_refused(monkeypatch):
    monkeypatch.setattr(cfg, "_load_scenario_dict", lambda case_id: dict(_ORIGINAL))
    form = _form()
    errors = cfg._wordlist_wipe_errors("case_x", scenario_builder.build_scenario(form), form)
    assert len(errors) == 2, errors          # 分支组 + 信号词组各一条
    assert _lost(errors[0]) == {"no_buy", "refuse", "conditional", "anti_allin",
                                "fallback_map", "judge_prompt"}
    assert _lost(errors[1]) == {"buy_words", "cond_words", "negators", "neg_phrases"}


def test_explicit_confirm_checkbox_releases_the_guard(monkeypatch):
    monkeypatch.setattr(cfg, "_load_scenario_dict", lambda case_id: dict(_ORIGINAL))
    form = _form(confirm_clear_branch_words=True, confirm_clear_consistency=True)
    assert cfg._wordlist_wipe_errors("case_x", scenario_builder.build_scenario(form), form) == []


def test_prefilled_words_round_trip_is_not_flagged(monkeypatch):
    """正路:编辑已有场景 → 词表回填 → 原样存回,不该被拦。"""
    monkeypatch.setattr(cfg, "_load_scenario_dict", lambda case_id: dict(_ORIGINAL))
    form = _form(**_as_form_words(_ORIGINAL))
    assert cfg._wordlist_wipe_errors("case_x", scenario_builder.build_scenario(form), form) == []


def test_clearing_the_judge_prompt_alone_is_flagged(monkeypatch):
    """判定稿和词表同属一段设定,抹掉它同样等于换实验 —— 一起拦。"""
    monkeypatch.setattr(cfg, "_load_scenario_dict", lambda case_id: dict(_ORIGINAL))
    words = _as_form_words(_ORIGINAL)
    words["branch_judge_prompt"] = ""
    form = _form(**words)
    errors = cfg._wordlist_wipe_errors("case_x", scenario_builder.build_scenario(form), form)
    assert len(errors) == 1
    assert _lost(errors[0]) == {"judge_prompt"}


def test_endpoint_round_trip_keeps_every_branch_setting(tmp_path, monkeypatch):
    """回归钉(2026-10-04 的 Bug):`branch.judge_prompt` 原先既不回读也不回写,
    经本工具存一次就整段抹掉。现在 YAML → 表单 → 落盘必须一段设定都不丢。
    """
    monkeypatch.setattr(cfg, "_PLATFORM_DIR", str(tmp_path))
    data = {
        "meta": {"case_id": "case_x", "name": "X", "engine": "experiment-eval"},
        "roles": [{"id": "ai", "display_name": "AI", "type": "ai_tool", "llm": "local",
                   "system_prompt": "", "max_tokens": 2048, "temperature": 0.5}],
        "world": {"state_schema": {"cash_rmb": {"initial": 200000, "type": "float"}}},
        "branch": dict(_ORIGINAL["branch"]),
        "consistency": dict(_ORIGINAL["consistency"]),
    }
    form = cfg._form_from_scenario("case_x", data)
    rebuilt = scenario_builder.build_scenario(form)
    for key in ("default_branch", "no_buy", "refuse", "conditional", "anti_allin",
                "fallback_map", "judge_prompt"):
        assert rebuilt["branch"].get(key) == data["branch"][key], key
    for key in ("buy_words", "cond_words", "negators", "neg_phrases"):
        assert rebuilt["consistency"].get(key) == data["consistency"][key], key
    # 回填出来的表单不该触发守卫
    assert cfg._wordlist_wipe_errors("case_x", rebuilt, form) == []


def test_clearing_a_single_class_is_also_flagged(monkeypatch):
    """按**键**比而不是按组:只抹掉其中两类同样是静默改设定。"""
    monkeypatch.setattr(cfg, "_load_scenario_dict", lambda case_id: dict(_ORIGINAL))
    words = _as_form_words(_ORIGINAL)
    words["branch_refuse"] = ""                       # 只清 refuse
    del words["cons_negators"]                        # 只漏 negators(收起面板没带值)
    form = _form(**words)
    errors = cfg._wordlist_wipe_errors("case_x", scenario_builder.build_scenario(form), form)
    assert len(errors) == 2
    assert _lost(errors[0]) == {"refuse"}           # 只报真丢的那一类,不牵连其它
    assert _lost(errors[1]) == {"negators"}


def test_adding_words_never_trips_the_guard(monkeypatch):
    monkeypatch.setattr(cfg, "_load_scenario_dict", lambda case_id: {
        "branch": {"default_branch": "C", "no_buy": ["不建议买"]}, "consistency": {}})
    form = _form(branch_no_buy="不建议买\n回避", cons_buy_words="buy now")
    assert cfg._wordlist_wipe_errors("case_x", scenario_builder.build_scenario(form), form) == []


def test_save_endpoint_refuses_empty_wordlists_over_existing_file(tmp_path, monkeypatch):
    """真走端点函数(含 `_json_body` 与守卫的**先后次序**):第一遍带词表存进去,
    第二遍清空必须被拒,而且拒在写盘之前 —— 原文件逐字节不变。

    用 stub request 直接驱动协程,不引 `TestClient`:CI 装的 extras 里没有 httpx,
    `starlette.testclient` 会在 import 时抛 RuntimeError。
    """
    import asyncio
    import json as _json

    monkeypatch.setattr(cfg, "_PLATFORM_DIR", str(tmp_path))

    class _Request:
        def __init__(self, payload):
            self._payload = payload

        async def json(self):
            return self._payload

    def post(form):
        return _json.loads(asyncio.run(cfg.scenario_save(_Request(form))).body)

    first = post(_form(**_as_form_words(_ORIGINAL)))
    assert first.get("ok"), first
    path = first["path"]
    before = open(path, encoding="utf-8").read()
    assert "no_buy" in before and "buy_words" in before

    blocked = post(_form())                 # 词表全空 + 未勾确认
    assert blocked.get("ok") is False, blocked
    assert "scenario_sha256" in blocked["errors"][0]
    assert open(path, encoding="utf-8").read() == before   # 拒了就必须没落盘

    cleared = post(_form(confirm_clear_branch_words=True, confirm_clear_consistency=True))
    assert cleared.get("ok"), cleared
    after = open(path, encoding="utf-8").read()
    assert "no_buy" not in after and "buy_words" not in after   # 勾了才真清得掉
