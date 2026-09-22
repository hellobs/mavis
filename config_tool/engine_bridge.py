# -*- coding: utf-8 -*-
"""engine_bridge — config_tool 与「引擎包」之间的**唯一接触面**(反向依赖收口点)。

背景:config_tool 是**引擎侧的配套工具**,需要读引擎的注册表 / schema 来做校验与运行。
这套接触以前散在 app / scenario_builder / engine_runner / compositions 四处,而且靠
**探测兄弟目录**自动找引擎包:位置靠猜、找不到还静默降级。2026-09-22 收口为:

1. **显式声明**:引擎包所在目录只认环境变量 ``CASE_ENGINE_DIR``(或调用方显式传入),
   不再探测 ``../provenance`` 之类的兄弟目录;不设就当作"没有引擎"。
2. **不静默**:未声明 / 目录不存在 / 导入失败,一律给出可读原因(见 :func:`status`),
   调用方据此把功能置灰,并把原因显示在页面上,绝不假装可用。
3. **单一接触面**:引擎包名与 ``sys.path`` 注入只出现在本文件;其余模块只调本模块的
   函数 —— 反向依赖的引用点因此从 4 个文件收敛到 1 个。

约定:``CASE_ENGINE_DIR`` 指**引擎包所在目录**(即 ``case_engine/`` 的父目录),
在平台仓的当前布局里它就是 ``<repo>/provenance``。
"""
import importlib
import os
import sys

# ↓↓↓ 引擎包名 / 声明变量名在本仓的**唯一**出现处 ↓↓↓
PKG = "case_engine"
ENV_DIR = "CASE_ENGINE_DIR"
# ↑↑↑ 其余模块一律不要写这两个字面量 ↑↑↑

_SENTINEL = object()
_API_CACHE = {}          # abs_dir -> (api | None, reason);进程内缓存(改环境变量需重启)


class EngineUnavailable(RuntimeError):
    """引擎不可用(未设置 CASE_ENGINE_DIR / 目录不存在 / 导入失败);message 即可读原因。"""


# ---------------------------------------------------------------------------
# 定位与状态
# ---------------------------------------------------------------------------
def engine_dir(explicit: str = "") -> str:
    """解析引擎包所在目录:显式传入优先,其次环境变量;都没有返回空串。

    只做**声明解析**——不做任何"猜兄弟目录"的探测。
    """
    val = str(explicit or "").strip() or str(os.environ.get(ENV_DIR) or "").strip()
    return os.path.abspath(val) if val else ""


def _load(directory: str):
    """把 directory 注入 sys.path 并 import 引擎包;返回 (api_dict | None, reason)。

    缓存进程内首次结果:环境变量改了要重启进程才生效(启动时会打印实际解析到的路径)。
    同一进程里切换两个不同的引擎目录时,Python 的 sys.modules 会让后一个复用先前的
    模块 —— 本工具只需一个引擎目录,如确有需要请重启。
    """
    cached = _API_CACHE.get(directory, _SENTINEL)
    if cached is not _SENTINEL:
        return cached
    try:
        if directory not in sys.path:
            sys.path.insert(0, directory)
        result = ({"engines": importlib.import_module(PKG + ".engines"),
                   "config": importlib.import_module(PKG + ".config"),
                   "scenarios": importlib.import_module(PKG + ".scenarios")}, "")
    except Exception as exc:  # noqa: BLE001 —— 失败原因如实回传,由调用方显示
        result = (None, "导入引擎包失败({}): {}: {}".format(
            directory, type(exc).__name__, exc))
    _API_CACHE[directory] = result
    return result


def status(explicit: str = "") -> dict:
    """引擎可用性状态(供页面置灰 + 启动打印用)。

    返回 ``{"configured": bool, "dir": str, "source": str,
             "available": bool, "reason": str}``;
    ``reason`` 在不可用时**一定**是可读原因(空串仅出现在可用时)。
    """
    directory = engine_dir(explicit)
    source = "参数" if str(explicit or "").strip() else ENV_DIR
    if not directory:
        return {"configured": False, "dir": "", "source": source, "available": False,
                "reason": "未设置环境变量 {}(引擎相关功能不可用)".format(ENV_DIR)}
    if not os.path.isdir(directory):
        return {"configured": True, "dir": directory, "source": source,
                "available": False,
                "reason": "{} 指向的目录不存在: {}".format(ENV_DIR, directory)}
    pkg_dir = os.path.join(directory, PKG)
    if not os.path.isdir(pkg_dir):
        return {"configured": True, "dir": directory, "source": source,
                "available": False,
                "reason": "{}={} 下找不到引擎包目录: {}".format(ENV_DIR, directory, pkg_dir)}
    api, reason = _load(directory)
    return {"configured": True, "dir": directory, "source": source,
            "available": api is not None, "reason": reason}


def _resolve(explicit: str = ""):
    """返回 (api | None, status_dict);内部便捷函数。"""
    st = status(explicit)
    if not st["available"]:
        return None, st
    api, _ = _load(st["dir"])
    return api, st


def api(explicit: str = ""):
    """引擎包对外符号 dict;不可用返回 None(调用方须自行给出可读错误)。"""
    return _resolve(explicit)[0]


def available(explicit: str = "") -> bool:
    return status(explicit)["available"]


def reason(explicit: str = "") -> str:
    """不可用原因(可用时为空串)。"""
    return status(explicit)["reason"]


def banner(explicit: str = "") -> str:
    """启动横幅:把**实际解析到的路径**打印出来,成功与失败都可见。"""
    st = status(explicit)
    if st["available"]:
        return "[engine] 引擎可用:{} = {}(来源:{})".format(ENV_DIR, st["dir"], st["source"])
    return "[engine] 引擎不可用:{}(引擎相关功能置灰;要启用请设置 {} 指向引擎包所在目录)" \
        .format(st["reason"], ENV_DIR)


# ---------------------------------------------------------------------------
# 引擎能力(全部在不可用时返回"空 + 原因可见"的形态,不抛异常)
# ---------------------------------------------------------------------------
def engine_names(explicit: str = "") -> dict:
    """引擎 id → 展示名;不可用返回 {}。"""
    a, _ = _resolve(explicit)
    if a is None:
        return {}
    out = {}
    for eid, meta in (getattr(a["engines"], "ENGINES", {}) or {}).items():
        out[eid] = (meta or {}).get("name") or eid
    return out


def engine_ids(explicit: str = "") -> list:
    """全部已注册引擎 id(公开 API all_ids);不可用返回 []。"""
    a, _ = _resolve(explicit)
    if a is None:
        return []
    try:
        return list(a["engines"].all_ids())
    except Exception:  # noqa: BLE001 —— 注册表读不到就当没有
        return []


def describe(engine_id: str, explicit: str = "") -> dict:
    a, _ = _resolve(explicit)
    if a is None:
        return {}
    try:
        return a["engines"].describe(engine_id) or {}
    except Exception:  # noqa: BLE001
        return {}


def components(engine_id: str, explicit: str = "") -> list:
    a, _ = _resolve(explicit)
    if a is None:
        return []
    try:
        return list(a["engines"].components(engine_id) or [])
    except Exception:  # noqa: BLE001
        return []


def known_engine(engine_id: str, explicit: str = ""):
    """引擎是否已注册;引擎不可用时返回 None(表示"判不了",由调用方放宽)。"""
    a, _ = _resolve(explicit)
    if a is None:
        return None
    try:
        return bool(a["engines"].known(engine_id))
    except Exception:  # noqa: BLE001
        return None


def supported_by(cfg, explicit: str = "") -> list:
    a, _ = _resolve(explicit)
    if a is None:
        return []
    try:
        return list(a["engines"].supported_by(cfg))
    except Exception:  # noqa: BLE001
        return []


def load_yaml(path: str, explicit: str = ""):
    a, _ = _resolve(explicit)
    if a is None:
        raise EngineUnavailable(reason(explicit))
    return a["config"].load_yaml(path)


def config_load(data, explicit: str = ""):
    a, _ = _resolve(explicit)
    if a is None:
        raise EngineUnavailable(reason(explicit))
    return a["config"].load(data)


def validate(cfg, explicit: str = ""):
    """用引擎 schema 校验;返回错误清单(list)。**引擎不可用时返回 None**(判不了)。

    ``cfg`` 是 scenario dict(内部先 load 成引擎的 Config 再 validate)。
    与"校验通过(返回 [])"严格区分,调用方据此决定是否退回基础校验。
    """
    a, _ = _resolve(explicit)
    if a is None:
        return None
    try:
        return list(a["config"].validate(a["config"].load(dict(cfg))) or [])
    except Exception as exc:  # noqa: BLE001 —— 校验异常归一为可读错误,不外抛
        return ["引擎校验异常: {}: {}".format(type(exc).__name__, exc)]


def value_tendency_plan(scenario: dict, explicit: str = ""):
    """声明 → 资产的唯一映射(引擎侧);不可用时抛 EngineUnavailable(原因可读)。"""
    a, _ = _resolve(explicit)
    if a is None:
        raise EngineUnavailable(reason(explicit))
    return a["config"].value_tendency_plan(a["config"].load(scenario))


def build_for(cfg, requested: str = "", explicit: str = ""):
    a, _ = _resolve(explicit)
    if a is None:
        raise EngineUnavailable(reason(explicit))
    return a["engines"].build_for(cfg, requested=requested)


def discover(cases_root: str = "", explicit: str = "") -> list:
    """发现场景清单;不可用返回 []。"""
    a, _ = _resolve(explicit)
    if a is None:
        return []
    root = cases_root or default_cases_root(explicit)
    try:
        return list(a["scenarios"].discover(root))
    except Exception:  # noqa: BLE001
        return []


def default_cases_root(explicit: str = "") -> str:
    """引擎默认场景根;不可用返回空串(调用方退回自己的 cases 目录)。"""
    a, _ = _resolve(explicit)
    if a is None:
        return ""
    try:
        return a["scenarios"].default_cases_root()
    except Exception:  # noqa: BLE001
        return ""
