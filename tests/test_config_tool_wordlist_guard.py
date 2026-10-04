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

# 一份"原 scenario.yaml 已经带全套词表"的形状(与 case01_stock 的段结构同形)
_ORIGINAL = {
    "branch": {
        "default_branch": "C",
        "no_buy": ["不建议买"], "refuse": ["无法判断"],
        "conditional": ["小仓位"], "anti_allin": ["不要全仓"],
        "fallback_map": {"A": {"timeline": "A"}},
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
                                "fallback_map"}
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


def test_http_save_refuses_empty_wordlists_over_existing_file(tmp_path, monkeypatch):
    """真打端点(不发服务,TestClient 在进程内):第一遍带词表存进去,第二遍清空必须被拦,
    而且拦在写盘之前 —— 原文件逐字节不变。
    """
    monkeypatch.setattr(cfg, "_PLATFORM_DIR", str(tmp_path))
    from fastapi.testclient import TestClient
    client = TestClient(cfg.app)

    first = client.post("/api/scenario/save", json=_form(**_as_form_words(_ORIGINAL)))
    assert first.status_code == 200, first.text
    assert first.json().get("ok"), first.text
    path = first.json()["path"]
    before = open(path, encoding="utf-8").read()
    assert "no_buy" in before and "buy_words" in before

    blocked = client.post("/api/scenario/save", json=_form())   # 词表全空 + 未勾确认
    body = blocked.json()
    assert body.get("ok") is False, body
    assert "scenario_sha256" in body["errors"][0]
    assert open(path, encoding="utf-8").read() == before         # 拦下发生在落盘之前

    cleared = client.post("/api/scenario/save", json=_form(
        confirm_clear_branch_words=True, confirm_clear_consistency=True))
    assert cleared.json().get("ok"), cleared.text
    after = open(path, encoding="utf-8").read()
    assert "no_buy" not in after and "buy_words" not in after    # 勾了才真清得掉
