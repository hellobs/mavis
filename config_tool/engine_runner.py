"""engine_runner — case_engine 运行器(Http 层薄封装)。

config_tool 除"生成场景"外,还提供"运行场景以自检"的能力:
- 发现 cases/<case_id>/scenario.yaml;
- 用 case_engine 的工厂(策略模式)按场景/引擎运行;
- case01(experiment-eval) 真跑 rule-dryrun(分支/状态/一致性/时间线);
- case00(sandbox-value) 只跑装配预检(需 mavis 桥,不越权假跑)。

惰性依赖:case_engine 定位不到时如实报"引擎不可用",config_tool 不因缺框架而崩。
"""
import os


def _case_engine_imports(platform_dir: str):
    """惰性 import case_engine 的运行相关符号;定位失败返回 None。"""
    ce_dir = os.path.join(platform_dir, "case_engine")
    if not os.path.isdir(ce_dir):
        return None
    try:
        import sys
        plat = os.path.abspath(platform_dir)
        if plat not in sys.path:
            sys.path.insert(0, plat)
        from case_engine.config import load_yaml          # noqa: F401
        from case_engine.engines import build_for         # noqa: F401
        return {"load_yaml": load_yaml, "build_for": build_for}
    except Exception:
        return None


def list_cases(platform_dir: str, cases_root: str = "") -> list:
    """扫描已声明场景,返回 [{case_id, path, engine, engine_infer}]."""
    root = cases_root or os.environ.get("CASE_ENGINE_CASES_ROOT") \
        or os.path.join(platform_dir, "cases")
    out = []
    if not os.path.isdir(root):
        return out
    for cid in sorted(os.listdir(root)):
        yp = os.path.join(root, cid, "scenario.yaml")
        if os.path.isfile(yp) and (cid, yp):
            infer = _quick_infer_engine(yp)
            out.append({"case_id": cid, "path": yp, "engine_infer": infer})
    return out


def _quick_infer_engine(path: str) -> str:
    """不加载,只从 yaml 文本推断 engine(供运行器展示默认选线)。"""
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                s = ln.strip()
                if s.startswith("engine:") and not s.startswith("#"):
                    return s.split(":", 1)[1].strip().strip("'\"")
                if not s.startswith("#") and s and not s.startswith("engine"):
                    pass
    except OSError:
        pass
    return ""


def run_case(platform_dir: str, case_id: str, engine_id: str = "",
             input_text: str = "", cases_root: str = ""):
    """用 case_engine 运行一份场景。返回 (ok: bool, summary: dict, errors: list)。

    - ok=False:引擎不可用 / 场景缺失 / 运行失败,errors 给可读原因;
    - ok=True:summary = run() 的 dict(含 run_type/branch/etc)。
    """
    api = _case_engine_imports(platform_dir)
    if api is None:
        return False, {}, ["未定位 case_engine(找到 {} 下应有 case_engine/)"
                           .format(os.path.join(platform_dir, "case_engine"))]
    root = cases_root or os.environ.get("CASE_ENGINE_CASES_ROOT") \
        or os.path.join(platform_dir, "cases")
    path = os.path.join(root, case_id, "scenario.yaml")
    if not os.path.isfile(path):
        return False, {}, ["场景不存在: {}".format(path)]
    try:
        cfg = api["load_yaml"](path)
        strat = api["build_for"](cfg, requested=engine_id)
    except Exception as exc:  # noqa: BLE001 —— 装配失败给可读错误
        return False, {}, ["加载/建引擎失败: {}".format(exc)]
    try:
        summary = strat.run(cfg, input_text=input_text)
        summary["_engine_id"] = strat.engine_id
        summary["_uses_default_engine"] = not engine_id
        return True, summary, []
    except Exception as exc:  # noqa: BLE001 —— 运行失败不把框架崩穿
        return False, {"engine": strat.engine_id}, ["运行失败: {}".format(exc)]