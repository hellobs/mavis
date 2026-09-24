"""scenario_builder — 场景声明(scenario.yaml)的确定性构建与导出。

业务方用结构化表单填写场景(meta/roles/world/branch/consistency),
本模块做**确定性映射**(纯表单 → YAML,不做 AI 解析),生成可直接被
引擎加载的 scenario.yaml。沙盒场景(如 sandbox-value)的资产路径、
价值权重、沙盒参数统一落在 `world` 标准段(assets/value_tendency/params),
不产出 custom 逃生舱。

设计原则(与 config_tool 其余模块一致):
- 单一来源:导出的 YAML 用引擎的 schema 校验(引擎可用时),
  保证"你能创建的就是引擎能跑的",避免表单与 schema 漂移。
- 显式依赖:引擎位置只由 `CASE_ENGINE_DIR` 声明,全部接触走 `engine_bridge`
  (不再探测兄弟目录、不再各处 sys.path.insert);引擎不可用时退回基础校验,
  并在返回值里**如实标出**(不静默换口径)。
- 稳定导出:safe_dump + allow_unicode + 不排序,输出稳定可 diff。
"""
import json
import os

import engine_bridge


def _load_engine_names() -> dict:
    """引擎展示名(单源 = 引擎注册表);引擎不可用时回退字面表。"""
    fallback = {
        "experiment-eval": "受控实验 / 评估",
        "sandbox-value": "生成式价值权重沙盒",
    }
    return engine_bridge.engine_names() or fallback


# 引擎 id → 展示名(唯一事实源在引擎注册表;此处仅为运行期选项派生)
SUPPORTED_ENGINES = _load_engine_names()

# meta 内部字段稳定顺序(便于人读/审)
_META_KEYS = ("case_id", "name", "description", "engine", "start_date", "end_date")
# 场景资产段(可选;sandbox-value 场景用,相对平台根)——写入 world.assets
_WORLD_SCENARIO_ASSETS = ("story", "relationships", "maze", "agents", "governance")



def governance_payload(scenario: dict, explicit: str = "") -> dict:
    """由 scenario 的 world.value_tendency.governance 生成 governance.json 的内容。

    **单一来源**:优先用引擎的 `value_tendency_plan()["materialize"]`(声明→资产的唯一映射),
    保证"工具生成的 governance.json"与"引擎落盘的 governance.json"逐字一致;
    引擎不可用(未设置 CASE_ENGINE_DIR / 独立部署)时退回本地口径,并在返回值里标出
    used_engine=False + engine_note,调用方可据此提示——不静默换口径。
    """
    vt = ((scenario or {}).get("world") or {}).get("value_tendency") or {}
    gov = dict(vt.get("governance") or {})
    try:
        plan = engine_bridge.value_tendency_plan(scenario, explicit)
        return {"roles": plan["materialize"]["governance.json"], "used_engine": True}
    except Exception as exc:  # noqa: BLE001 —— 拿不到计划就退回本地口径,并如实标记
        return {"roles": gov, "used_engine": False,
                "engine_note": "{}: {}".format(type(exc).__name__, exc)}


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
def safe_case_id(case_id: str) -> str:
    """场景 id 守卫:它会被拼成 `cases/<case_id>/...`,不允许含路径分隔符。

    2026-09-23 安全体检:`case_id` 直接来自表单,`save_scenario()` 却直接
    `os.path.join(root, case_id)` + `makedirs` —— `../../..` 能在 cases 目录之外写
    scenario.yaml。与 `app._safe_path_segment()` 同一口径。
    """
    s = "".join(c for c in str(case_id or "") if c not in "\t\r\n").strip()
    if not s:
        raise ValueError("场景 id 不能为空")
    if "/" in s or "\\" in s or ":" in s or s in (".", ".."):
        raise ValueError("非法的场景 id(不能含路径分隔符): {!r}".format(s))
    return s


def build_scenario(form: dict) -> dict:
    """把结构化表单转成 scenario dict。form 为字典(数组字段已是 list of list/dict)。

    注意:`case_id` 这里**不抛异常** —— 表单来的东西一律先构造成 cfg,由
    `validate_scenario()` 把问题**作为可读错误报回界面**(既有行为,有测试钉住);
    真正拦住穿越的是落盘那一步 `save_scenario()` 与 `app.scenario_assets_dir()`。
    """
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
    #   (不再用 custom 逃生舱 —— 与引擎的 world 标准段契约对齐)
    assets = {k: _strip(form.get("asset_" + k))
              for k in _WORLD_SCENARIO_ASSETS if _strip(form.get("asset_" + k))}
    if assets:
        world["assets"] = assets
    vt = _parse_value_json(form.get("value_tendency"))
    if vt:
        world["value_tendency"] = vt
    sp = _parse_value_json(form.get("sandbox_params"))
    if sp:
        world["params"] = sp

    scenario = {
        "meta": {k: meta[k] for k in _META_KEYS if k in meta},
        "roles": roles,
        "world": world,
        "branch": branch,
        "consistency": consistency,
    }

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
    """场景根目录:平台 <platform_dir>/cases。

    平台目录(其来源见 app.py 的显式声明)未提供时返回空串 —— 调用方必须据此
    **明确报错**,而不是把场景写进当前工作目录。
    """
    return (os.environ.get("CASE_ENGINE_CASES_ROOT")
            or (os.path.join(platform_dir, "cases") if platform_dir else ""))


def save_scenario(platform_dir: str, cfg: dict) -> str:
    """把场景写进 cases/<case_id>/scenario.yaml;返回落盘绝对路径。"""
    root = cases_dir(platform_dir)
    if not root:
        raise ValueError("平台目录未声明,无法落盘场景(见 engine_bridge.banner() 的原因)")
    case_id = safe_case_id((cfg.get("meta") or {}).get("case_id") or "scenario")
    case_dir = os.path.join(root, case_id)
    os.makedirs(case_dir, exist_ok=True)
    path = os.path.join(case_dir, "scenario.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump_scenario_yaml(cfg))
    return path


# ---------------------------------------------------------------------------
# 校验(尽力用引擎的 schema;不可用时只做基础校验)
# ---------------------------------------------------------------------------
def validate_scenario(cfg: dict, platform_dir: str = ""):
    """返回 (ok: bool, errors: list)。ok=False 时 errors 给出可读原因。

    `platform_dir` 是**调用方显式声明**的引擎包所在目录(不传则用环境变量);
    优先用引擎的 Validate 契约;不可用则退回字段级基础校验(并在调用方提示)。
    """
    meta = cfg.get("meta") or {}
    errors = []
    if not meta.get("case_id"):
        errors.append("meta.case_id 必填")
    else:
        # 本地也要拦一次(2026-09-23 安全体检):引擎不在时下面会走兜底分支,
        # 那时 case_id 的形状就没人看了 —— 而它是要拼进 `cases/<case_id>/` 的。
        try:
            safe_case_id(meta.get("case_id"))
        except ValueError as exc:
            errors.append("meta.case_id 非法: {}".format(exc))
    if not cfg.get("roles"):
        errors.append("roles 至少一个")
    engine_errors = engine_bridge.validate(cfg, platform_dir)
    if engine_errors is None:
        # 引擎不可用:基础校验兜底(仍可保存,但不保证能被引擎加载)
        return (not errors), errors
    errors.extend(engine_errors)
    return (not errors), errors