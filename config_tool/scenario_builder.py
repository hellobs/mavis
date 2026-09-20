"""scenario_builder — 场景声明(scenario.yaml)的确定性构建与导出。

业务方用结构化表单填写场景(meta/roles/world/branch/consistency),
本模块做**确定性映射**(纯表单 → YAML,不做 AI 解析),生成可直接被
case_engine 加载的 scenario.yaml。沙盒场景(如 sandbox-value)的资产路径、
价值权重、沙盒参数统一落在 `world` 标准段(assets/value_tendency/params),
不产出 custom 逃生舱。

设计原则(与 config_tool 其余模块一致):
- 单一来源:导出的 YAML 用 case_engine 的 schema 校验(引擎 import 可用时),
  保证"你能创建的就是引擎能跑的",避免表单与 schema 漂移。
- 惰性依赖:引擎校验走惰性 import,定位不到 case_engine 时退回基础校验,
  保证 config_tool 独立可跑(不把框架仓库锁死进运行依赖)。
- 稳定导出:safe_dump + allow_unicode + 不排序,输出稳定可 diff。
"""
import json
import os


# 引擎 id → 展示名(与 case_engine/engines.py 描述一致;仅表单选项)
SUPPORTED_ENGINES = {
    "experiment-eval": "受控实验 / 评估(分支/反思/一致性)",
    "sandbox-value": "生成式价值权重沙盒(需 mavis)",
}

# meta 内部字段稳定顺序(便于人读/审)
_META_KEYS = ("case_id", "name", "description", "engine", "start_date", "end_date")
# 场景资产段(可选;sandbox-value 场景用,相对平台根)——写入 world.assets
_WORLD_SCENARIO_ASSETS = ("story", "relationships", "maze", "agents", "governance")


def parse_words(text) -> list:
    """把文本框拆成去重关键词列表(兼容换行/半角/全角逗号/分号)。"""
    if text is None:
        return []
    for sep in ("\n", ",", "，", ";", "；", "、"):
        text = str(text).replace(sep, "\n")
    out = []
    for w in (x.strip() for x in str(text).split("\n")):
        if w and w not in out:
            out.append(w)
    return out


def _num(value, default):
    try:
        return int(value) if isinstance(value, (int, float)) else int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _strip(value) -> str:
    return str(value or "").strip()


# ---------------------------------------------------------------------------
# 表单 → scenario dict(确定性,不做 AI 解析)
# ---------------------------------------------------------------------------
def build_scenario(form: dict) -> dict:
    """把结构化表单转成 scenario dict。form 为字典(数组字段已是 list of dict)。"""
    meta = {
        "case_id": _strip(form.get("case_id") or form.get("name")) or "scenario",
        "name": _strip(form.get("name")) or _strip(form.get("case_id")) or "未命名场景",
        "description": _strip(form.get("description")),
        "engine": _strip(form.get("engine")) or "experiment-eval",
    }
    for k in ("start_date", "end_date"):
        v = _strip(form.get(k))
        if v:
            meta[k] = v

    roles = []
    for r in form.get("roles") or []:
        rid = _strip(r.get("id"))
        if not rid:
            continue
        roles.append({
            "id": rid,
            "display_name": _strip(r.get("display_name")) or rid,
            "type": _strip(r.get("type")) or "ai_tool",
            "llm": _strip(r.get("llm")) or "local",
            "system_prompt": _strip(r.get("system_prompt")),
            "max_tokens": _num(r.get("max_tokens"), 2048),
            "temperature": _float(r.get("temperature"), 0.5),
        })
        cr = r.get("conflict_rules")
        if isinstance(cr, list) and cr:
            roles[-1]["conflict_rules"] = [dict(x) for x in cr if isinstance(x, dict)]

    state_schema = {}
    for f in form.get("state_schema") or []:
        key = _strip(f.get("field"))
        if not key:
            continue
        initial = f.get("initial")
        state_schema[key] = {
            "initial": initial if initial not in (None, "") else None,
            "type": _strip(f.get("type")) or "str",
        }

    branch = {"default_branch": _strip(form.get("branch_default")) or ""}
    for k in ("no_buy", "refuse", "conditional", "anti_allin"):
        words = parse_words(form.get("branch_" + k))
        if words:
            branch[k] = words
    fm = form.get("branch_fallback_map")
    if isinstance(fm, dict) and fm:
        branch["fallback_map"] = fm
    judge_prompt = _strip(form.get("branch_judge_prompt"))
    if judge_prompt:
        branch["judge_prompt"] = judge_prompt

    consistency = {}
    for k in ("buy_words", "cond_words", "negators", "neg_phrases"):
        words = parse_words(form.get("cons_" + k))
        if words:
            consistency[k] = words

    world = {"state_schema": state_schema}
    # 沙盒标准段(sandbox-value 场景):资产路径/价值权重/沙盒参数统一落 world.
    #   (不再用 custom 逃生舱 —— 与 case_engine 的 world 标准段契约对齐)
    assets = {k: _strip(form.get("asset_" + k))
              for k in _WORLD_SCENARIO_ASSETS if _strip(form.get("asset_" + k))}
    if assets:
        world["assets"] = assets
    vt = _parse_value_json(form.get("custom_value_tendency"))
    if vt:
        world["value_tendency"] = vt
    sp = _parse_value_json(form.get("custom_sandbox_params"))
    if sp:
        world["params"] = sp

    scenario = {
        "meta": {k: meta[k] for k in _META_KEYS if k in meta},
        "roles": roles,
        "world": world,
        "branch": branch,
        "consistency": consistency,
    }

    # 空段删掉,保持 YAML 干净(引擎默认值兜底)
    for sec in list(scenario):
        if sec in ("roles", "branch", "consistency"):
            continue
    if not scenario["roles"]:
        scenario["roles"] = [{
            "id": "assistant", "display_name": "助手", "type": "ai_tool",
            "llm": "local", "system_prompt": "", "max_tokens": 2048, "temperature": 0.5,
        }]
    return scenario


def _parse_value_json(value_json) -> dict:
    raw = _strip(value_json)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# 导出 / 落盘
# ---------------------------------------------------------------------------
def dump_scenario_yaml(cfg: dict) -> str:
    """确定性导出 scenario.yaml(allow_unicode + 不排序 + 缩进 2)。"""
    import yaml
    return yaml.safe_dump(
        cfg, allow_unicode=True, sort_keys=False,
        default_flow_style=False, indent=2,
    )


def cases_dir(platform_dir: str) -> str:
    """场景根目录:平台 <platform>/cases(与 case_engine 默认一致)。"""
    return (os.environ.get("CASE_ENGINE_CASES_ROOT")
            or os.path.join(platform_dir, "cases"))


def save_scenario(platform_dir: str, cfg: dict) -> str:
    """把场景写进 cases/<case_id>/scenario.yaml;返回落盘绝对路径。"""
    case_id = (cfg.get("meta") or {}).get("case_id") or "scenario"
    case_dir = os.path.join(cases_dir(platform_dir), case_id)
    os.makedirs(case_dir, exist_ok=True)
    path = os.path.join(case_dir, "scenario.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump_scenario_yaml(cfg))
    return path


# ---------------------------------------------------------------------------
# 校验(尽力用 case_engine 的 schema;不可用时只做基础校验)
# ---------------------------------------------------------------------------
def _try_import_case_engine(platform_dir: str):
    """惰性 import case_engine.config;定位不到返回 None(不把 config_tool 锁死)。"""
    ce_dir = os.path.join(platform_dir, "case_engine")
    if not os.path.isdir(ce_dir):
        return None
    try:
        import sys
        plat = os.path.abspath(platform_dir)
        if plat not in sys.path:
            sys.path.insert(0, plat)
        from case_engine.config import load, validate  # noqa: F401
        return (load, validate)
    except Exception:
        return None


def validate_scenario(cfg: dict, platform_dir: str):
    """返回 (ok: bool, errors: list)。ok=False 时 errors 给出可读原因。

    优先用 case_engine 的 Validate 契约;不可用则退回字段级基础校验。
    """
    meta = cfg.get("meta") or {}
    errors = []
    if not meta.get("case_id"):
        errors.append("meta.case_id 必填")
    if not cfg.get("roles"):
        errors.append("roles 至少一个")
    loaded = _try_import_case_engine(platform_dir)
    if loaded is None:
        # 基础校验兜底(未定位 case_engine,提示仍可保存但不保证能被引擎加载)
        if errors:
            return False, errors
        return True, []
    load_fn, validate_fn = loaded
    try:
        inst = load_fn(dict(cfg))
        errs = validate_fn(inst)
        if errs:
            errors.extend(errs)
    except Exception as exc:  # noqa: BLE001 —— 校验失败归一为可读错误,不外抛
        errors.append("引擎校验异常: {}".format(exc))
    return (not errors), errors