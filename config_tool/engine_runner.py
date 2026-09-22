"""engine_runner — 引擎运行器(Http 层薄封装)。

config_tool 除"生成场景"外,还提供"运行场景以自检"的能力:
- 发现 cases/<case_id>/scenario.yaml;
- 用引擎的工厂(策略模式)按场景/引擎运行;
- case01(experiment-eval) 真跑 rule-dryrun(分支/状态/一致性/时间线);
- case00(sandbox-value) 只跑装配预检(需 mavis 桥,不越权假跑)。

引擎位置与 import 全部委托 `engine_bridge`(引擎目录只认 `CASE_ENGINE_DIR`,不探测兄弟
目录、也不拿平台根顶替);引擎不可用时**如实报"引擎不可用 + 原因"**,config_tool 不因
缺框架而崩、也不静默降级。
"""
import os

import engine_bridge


def list_cases(platform_dir: str, cases_root: str = "") -> list:
    """扫描已声明场景,返回 [{case_id, path, engine_infer}]."""
    root = cases_root or os.environ.get("CASE_ENGINE_CASES_ROOT") \
        or (os.path.join(platform_dir, "cases") if platform_dir else "")
    out = []
    if not root or not os.path.isdir(root):
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
    """用引擎运行一份场景。返回 (ok: bool, summary: dict, errors: list)。

    - ok=False:引擎不可用 / 场景缺失 / 运行失败,errors 给可读原因;
    - ok=True:summary = run() 的 dict(含 run_type/branch/etc)。
    """
    if engine_bridge.api() is None:
        return False, {}, ["引擎不可用: {}".format(engine_bridge.reason())]
    root = cases_root or os.environ.get("CASE_ENGINE_CASES_ROOT") \
        or (os.path.join(platform_dir, "cases") if platform_dir else "")
    if not root:
        return False, {}, ["场景根目录未声明(平台目录为空且未设 CASE_ENGINE_CASES_ROOT)"]
    path = os.path.join(root, case_id, "scenario.yaml")
    if not os.path.isfile(path):
        return False, {}, ["场景不存在: {}".format(path)]
    try:
        cfg = engine_bridge.load_yaml(path)
        strat = engine_bridge.build_for(cfg, requested=engine_id)
    except Exception as exc:  # noqa: BLE001 —— 装配失败给可读错误
        return False, {}, ["加载/建引擎失败: {}".format(exc)]
    try:
        summary = strat.run(cfg, input_text=input_text)
        summary["_engine_id"] = strat.engine_id
        summary["_uses_default_engine"] = not engine_id
        return True, summary, []
    except Exception as exc:  # noqa: BLE001 —— 运行失败不把框架崩穿
        return False, {"engine": strat.engine_id}, ["运行失败: {}".format(exc)]
