"""compositions — 「组合(case×engine)」注册表(config_tool 编排层)。

设计原则:
- 场景 = 数据;引擎 = 运行策略;组合 = 二者配对后的一份可重复使用的记录。
  「组合是组合」:它是独立的一等记录,用户显式创建/命名/启停/设为默认,
  与 case 内部的引擎字段**不强联动**(case 保持纯数据)。
- 校验:新建组合时校验 场景存在 + 引擎已注册(引擎接触走 engine_bridge;
  引擎不可用时判不了,放宽为引擎 id 非空),保证记录的是"真能去跑"的组合。
- 唯一键:组合 id = f"{case_id}::{engine_id}",稳定、可读、天然唯一。

数据文件:默认 <config_tool>/compositions.json,可用环境变量 COMPOSITIONS_FILE 覆盖。
"""
import json
import os

import engine_bridge

# 组合 id 分隔符(case_id 与 engine_id 均已限制为字母数字/_/-,无撞字)
KEY_SEP = "::"
DEFAULT_FILE = "compositions.json"


def _file() -> str:
    return os.environ.get("COMPOSITIONS_FILE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), DEFAULT_FILE)


def _cases_roots(platform_dir: str) -> list:
    """候选场景根:平台目录(若显式声明)+ CASE_ENGINE_CASES_ROOT。"""
    roots = []
    if platform_dir:
        roots.append(os.path.join(platform_dir, "cases"))
    extra = os.environ.get("CASE_ENGINE_CASES_ROOT")
    if extra:
        roots.append(extra)
    return roots


def _engine_is_known(engine_id: str, platform_dir: str = "") -> bool:
    """判断引擎是否已注册;引擎不可用(判不了)则放宽(只要求非空)。"""
    names = engine_bridge.engine_names(platform_dir)
    if not names:
        return bool(engine_id)
    return engine_id in names


def _case_exists(case_id: str, platform_dir: str) -> bool:
    for root in _cases_roots(platform_dir):
        if os.path.isfile(os.path.join(root, case_id, "scenario.yaml")):
            return True
    return False


def load(path: str | None = None) -> list:
    """读组合清单;文件不存在或损坏时返回空(不抛)。"""
    p = path or _file()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        comps = data.get("compositions") or []
        return [c for c in comps if isinstance(c, dict)]
    except (OSError, ValueError):
        return []


def save(comps: list, path: str | None = None) -> None:
    p = path or _file()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"compositions": comps}, f, ensure_ascii=False, indent=2)


def find(comps: list, comp_id: str) -> dict | None:
    for c in comps:
        if c.get("id") == comp_id:
            return c
    return None


def _key(case_id: str, engine_id: str) -> str:
    return KEY_SEP.join([case_id, engine_id])


def _scenario_title(case_id: str, platform_dir: str = "") -> str:
    """取 scenario.yaml 的 meta.name 作为可读场景名;取不到回退 case_id。"""
    for root in _cases_roots(platform_dir):
        p = os.path.join(root, case_id, "scenario.yaml")
        if not os.path.isfile(p):
            continue
        try:
            import yaml
            data = yaml.safe_load(open(p, encoding="utf-8"))
            name = (data or {}).get("meta", {}).get("name")
            if name:
                return str(name).strip()
        except Exception:  # noqa: BLE001 —— 解析失败降级为 case_id
            pass
    return case_id


def _engine_title(engine_id: str, platform_dir: str = "") -> str:
    """取引擎注册表的展示名(单源);取不到回退 engine_id。"""
    name = engine_bridge.engine_names(platform_dir).get(engine_id)
    return str(name).strip() if name else engine_id


def default_name(case_id: str, engine_id: str, platform_dir: str = "") -> str:
    """空白名时的默认组合名:可读的「场景名 · 引擎名」。"""
    return "{} · {}".format(
        _scenario_title(case_id, platform_dir),
        _engine_title(engine_id, platform_dir))


def create(comps: list, case_id: str, engine_id: str,
           name: str = "", description: str = "", platform_dir: str = "") -> tuple:
    """新建组合;返回 (comps_new, ok, comp|errors)。

    重复 case×engine 拒绝;场景不存在 / 引擎未注册 时给可读错误。
    """
    case_id = str(case_id or "").strip()
    engine_id = str(engine_id or "").strip()
    if not case_id or not engine_id:
        return comps, False, ["case_id 与 engine_id 均不能为空"]
    key = _key(case_id, engine_id)
    if find(comps, key) is not None:
        return comps, False, [
            "组合已存在: {} = {} × {};请直接选用或删除后重建".format(
                key, case_id, engine_id)]
    if platform_dir and not _case_exists(case_id, platform_dir):
        return comps, False, [
            "场景不存在: {}(需有 cases/{}/scenario.yaml)".format(
                case_id, case_id)]
    if not _engine_is_known(engine_id, platform_dir):
        return comps, False, ["引擎未注册: {!r}".format(engine_id)]
    comp = {
        "id": key,
        "case_id": case_id,
        "engine_id": engine_id,
        "name": str(name).strip() or default_name(case_id, engine_id, platform_dir),
        "description": str(description or "").strip(),
        "enabled": True,
        "is_default": False,
    }
    return comps + [comp], True, comp


def delete(comps: list, comp_id: str) -> tuple:
    keep = [c for c in comps if c.get("id") != comp_id]
    removed = len(keep) != len(comps)
    return keep, removed


def rename(comps: list, comp_id: str, name: str) -> tuple:
    c = find(comps, comp_id)
    if c is None:
        return False, False
    c["name"] = str(name).strip() or c["name"]
    return True, True


def set_enabled(comps: list, comp_id: str, enabled: bool) -> tuple:
    c = find(comps, comp_id)
    if c is None:
        return False, False
    c["enabled"] = bool(enabled)
    return True, True


def set_default(comps: list, comp_id: str) -> tuple:
    """设为默认(全局唯一一个);返回该组合,以及是否被改。"""
    c = find(comps, comp_id)
    if c is None:
        return None, False
    changed = False
    for x in comps:
        want = (x.get("id") == comp_id)
        if bool(x.get("is_default")) != want:
            x["is_default"] = want
            changed = True
    return c, changed


def default_composition(comps: list) -> dict | None:
    for c in comps:
        if c.get("is_default") and c.get("enabled", True):
            return c
    return None