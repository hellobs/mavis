"""config_tool.app — MAVIS 角色配置生成工具(独立服务,端口 8060)

业务方通过网页表单填写角色/职责/权限/目标/关系/剧情,
工具按 MAVIS 的 Schema 生成 agent.json / relationships.json / story.json,
并经 MAVIS validator 校验后写入 scenarios/ 目录。

设计原则:
- 独立于仿真服务(live_fastapi):本工具只做配置生成,不跑模拟
- Schema 与 validator 单一来源:复用 MAVIS framework,避免双份维护
- 学术严谨:纯表单 + 确定性映射,不做 AI 解析(呼应"JSON 可靠"要求)
"""
import json
import os
import sys
import shutil
import subprocess

# MAVIS 框架包所在目录(= 本仓库根,config_tool 与 mavisframework/ 同级)。
# 注意:要 import 的是包 mavisframework,所以 sys.path 上要放它的**父目录**;
# 以前放的是 mavisframework/ 自身,只有 "pip 装过 mavisframework" 的环境才碰巧能跑,
# 源码直跑会 ModuleNotFoundError(2026-09-22 修)。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, REPO_DIR)   # 允许 import mavisframework.*
sys.path.insert(0, BASE_DIR)   # 允许 import scenario_builder(同目录)

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

# 复用 MAVIS 的 validator(Schema 单一来源)
from mavisframework.config.validator import (
    validate_agents, validate_relationships, validate_story,
)
# 场景声明构建器(scenario.yaml 生成;确定性,复用引擎 schema 校验)
import scenario_builder
# 组合(case×engine)注册表:场景=数据,组合=可选择的运行记录(config_tool 编排层)
import compositions
# 引擎运行器(运行场景自检;引擎位置与 import 统一走 engine_bridge)
import engine_runner
# 与引擎包之间的**唯一接触面**(显式声明 + 可用性状态;见该模块文档)
import engine_bridge

app = FastAPI(title="MAVIS 角色配置工具")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ---------------------------------------------------------------------------
# 路径声明(**全部显式,不探测兄弟目录**)
#
#   CASE_ENGINE_DIR    : 引擎包所在目录(引擎包目录的父目录)—— engine_bridge 解析
#   MAVIS_PLATFORM_DIR : 平台仓根(产物落盘、地图、实时入口联动);未设时沿用
#                        CASE_ENGINE_DIR(平台仓当前布局下二者同为同一目录)
#   MAVIS_ASSETS_ROOT / MAVIS_SCENARIOS_DIR / MAVIS_MAZE_PATH : 逐项覆盖
#
# 未声明时:本工具**照常启动**,引擎/平台相关功能置灰,并在启动日志与页面上
# 给出可读原因(绝不静默降级、绝不把产物写进当前工作目录)。
# ---------------------------------------------------------------------------
def _platform_dir() -> str:
    """平台根目录:只认显式声明(MAVIS_PLATFORM_DIR → CASE_ENGINE_DIR),不做目录探测。"""
    for key in ("MAVIS_PLATFORM_DIR", "CASE_ENGINE_DIR"):
        val = str(os.environ.get(key) or "").strip()
        if val:
            return os.path.abspath(val)
    return ""


_PLATFORM_DIR = _platform_dir()
# 引擎目录**只认 CASE_ENGINE_DIR**(不拿平台根顶替:两者概念不同,可分别部署)
_ENGINE_DIR = engine_bridge.engine_dir()
VILLAGE_ROOT = os.environ.get("MAVIS_ASSETS_ROOT") or (
    os.path.join(_PLATFORM_DIR, "frontend", "static", "assets", "village")
    if _PLATFORM_DIR else "")
SCENARIOS_DIR = os.environ.get("MAVIS_SCENARIOS_DIR") or (
    os.path.join(_PLATFORM_DIR, "scenarios") if _PLATFORM_DIR else "")
# 地图默认指向 case00 实际使用的新地图(地址树与运行时一致,替代旧的 village 小图);
# 旧 village 图仅作兜底,缺省时优先取 case00/scenario/maze.json。
_CASE00_MAZE = (os.path.join(_PLATFORM_DIR, "case00", "scenario", "maze.json")
                if _PLATFORM_DIR else "")
_FALLBACK_MAZE = os.path.join(VILLAGE_ROOT, "maze.json") if VILLAGE_ROOT else ""
MAZE_PATH = os.environ.get("MAVIS_MAZE_PATH") or (
    _CASE00_MAZE if _CASE00_MAZE and os.path.isfile(_CASE00_MAZE) else _FALLBACK_MAZE)


def runtime_status() -> dict:
    """引擎 + 平台目录的**实际解析结果**(启动横幅与页面横幅共用,便于人看清现状)。"""
    st = engine_bridge.status(_ENGINE_DIR)
    st["env_var"] = engine_bridge.ENV_DIR
    st["platform"] = _PLATFORM_DIR
    st["platform_reason"] = "" if _PLATFORM_DIR else (
        "未声明平台目录:设置 {} 或 MAVIS_PLATFORM_DIR 指向平台仓根;"
        "在此之前产物无法落盘、地图与实时入口联动不可用".format(engine_bridge.ENV_DIR))
    return st


def _print_startup_banner() -> None:
    """启动即打印解析结果(成功与失败都可见)。"""
    print(engine_bridge.banner(_ENGINE_DIR), flush=True)
    print("[platform] 平台根 = {}".format(_PLATFORM_DIR or "(未声明)"), flush=True)
    print("[platform] 资源根 = {} ; 场景目录 = {} ; 地图 = {}".format(
        VILLAGE_ROOT or "(未声明)", SCENARIOS_DIR or "(未声明)",
        MAZE_PATH or "(未声明)"), flush=True)


templates.env.globals["runtime_status"] = runtime_status
# 启动即打印(任何启动方式都可见:python app.py / uvicorn app:app)
_print_startup_banner()

# ---------------------------------------------------------------------------
# 5010 实时入口联动(统入口:config_tool 运行组合时把 5010 切到该 case 的实时面)
# 映射:config_tool 的 case_id(场景) → 平台 live_switch 的 case 键(live 实现)。
# ---------------------------------------------------------------------------
LIVE_ENTRY = {"case00_village": "case00", "case01_stock": "case01"}
LIVE_ENTRY_TITLE = {"case00": "权重/治理面板(live_fastapi)",
                    "case01": "注入器推演(vizkit)"}
# 实时服务解释器:live_fastapi 依赖 uvicorn+mavisframework,config_tool 自身常跑在
# generative_agents_cn(取不到 mavisframework),故切换一律走平台 venv-live
# (实测 venv-live 能整条链路起 live_fastapi);缺失时兜底用当前解释器。
_LIVE_PY = (os.path.join(_PLATFORM_DIR, ".venv-live", "Scripts", "python.exe")
            if _PLATFORM_DIR else "")
if not _LIVE_PY or not os.path.isfile(_LIVE_PY):
    _LIVE_PY = sys.executable


def _launch_live(case_id: str) -> dict:
    """运行组合时,把 5010 切到该 case 的实时面。

    **非阻塞**:live_switch --start 要等端口绑上(最长约 36s),这里 Popen 即返回,
    不让 /api/run/execute 挂起。切换到 5010 由 live_switch 子进程负责(先停另一 case)。
    """
    live_case = LIVE_ENTRY.get(case_id)
    if not live_case:
        return {"switched": False,
                "notice": "场景 {} 无实时入口,5010 不切换".format(case_id)}
    if not _PLATFORM_DIR:
        return {"switched": False,
                "notice": "平台目录未声明,5010 不切换(见启动日志 / 页面横幅)"}
    switch = os.path.join(_PLATFORM_DIR, "live_switch.py")
    if not os.path.isfile(switch):
        return {"switched": False, "notice": "未找到 live_switch.py,5010 未切换"}
    out = os.path.join(os.environ.get("TEMP", _PLATFORM_DIR), "cfg_live_switch.out")
    err = out + ".err"
    try:
        with open(out, "ab") as fo, open(err, "ab") as fe:
            subprocess.Popen(
                [_LIVE_PY, switch, "--start", live_case],
                cwd=_PLATFORM_DIR, stdout=fo, stderr=fe,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    except Exception as exc:  # noqa: BLE001 —— 起切换失败只提示,不阻断本次运行
        return {"switched": False, "notice": "启动 5010 切换失败: {}".format(exc)}
    return {"switched": True,
            "comment": "redirect_url = 5010 实时面根路径(async 切换,此字段由前端在 health 就绪后跳转)",
            "redirect_url": "http://localhost:5010/",
            "notice": "5010 已切到 {}: http://localhost:5010/".format(
                LIVE_ENTRY_TITLE[live_case])}


def _load_maze():
    """加载默认地图;平台目录/地图未声明时**明确报错**(不静默退回空地图)。"""
    if not MAZE_PATH:
        raise RuntimeError(
            "地图不可用:未声明平台目录(设置 {} 或 MAVIS_PLATFORM_DIR,"
            "或用 MAVIS_MAZE_PATH 直接指向地图文件)".format(engine_bridge.ENV_DIR))
    if not os.path.isfile(MAZE_PATH):
        raise RuntimeError("地图文件缺失: {}".format(MAZE_PATH))
    with open(MAZE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_maze_rel(maze_rel: str):
    """按相对平台根的地图路径加载;缺声明/文件缺失回退全局地图(回退亦不静默)。"""
    decl = (maze_rel or "").replace("\\", "/").strip()
    if decl and _PLATFORM_DIR:
        cand = os.path.join(_PLATFORM_DIR, decl)
        if os.path.isfile(cand):
            try:
                with open(cand, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:  # noqa: BLE001 —— 单图解析失败回退,但不静默
                print("[maze] 解析失败,回退默认地图: {}: {}".format(
                    cand, exc), flush=True)
    return _load_maze()


def _maze_addresses(maze) -> list:
    """从某张地图抽取排序地址树供角色居住区下拉使用(>=2 级且去重)。

    地址**带 world 前缀**(如 "the Ville:Trading Center:Trading Floor"),
    与 agent.json 的 living_area 及 validator(spatial 前缀校验)一致,
    避免下拉选出的地址与运行时/校验错位。
    """
    world = (maze or {}).get("world", "")
    out = []
    for t in (maze or {}).get("tiles", []):
        a = t.get("address", [])
        if len(a) < 2:
            continue
        full = ([world] + list(a)) if world and a[0] != world else list(a)
        s = ":".join(full)
        if s not in out:
            out.append(s)
    return sorted(out)


# ---------------------------------------------------------------------------
# 表单 → agent.json 的确定性映射(字段一一对应,不做 AI 解析)
# ---------------------------------------------------------------------------
def _auto_coord(living_area: list, maze=None) -> list:
    """从地图自动分配该地址下的一个可达坐标(业务方不用填坐标)

    maze 缺省取全局默认地图;场景所选地图不同时传入以免坐标错配。
    匹配规则(按优先级):
    1) 精确:tile.address == living_area(如 living_area 本身就是可达区域)
    2) 包含:tile.address 以 living_area 开头(区域内更深一级 tile,如 资料室:办公桌)
    3) 前缀兜底:living_area 以 tile.address 开头(区域级 tile,如 投资咨询中心)
    world 前缀(如 "the Ville")与 tile address 不对齐,先剥掉再匹配。
    """
    maze = maze or _load_maze()
    world = maze.get("world", "")
    la = list(living_area)
    if la and la[0] == world:
        la = la[1:]
    addr = ":".join(la)

    def _walkable(t):
        return not t.get("collision", False)

    for t in maze.get("tiles", []):
        a = t.get("address", [])
        if a and addr == ":".join(a) and _walkable(t):
            return t["coord"]
    for t in maze.get("tiles", []):
        a = t.get("address", [])
        if a and ":".join(a).startswith(addr + ":") and _walkable(t):
            return t["coord"]
    for t in maze.get("tiles", []):
        a = t.get("address", [])
        if a and addr.startswith(":".join(a) + ":") and _walkable(t):
            return t["coord"]
    return [0, 0]


def build_agent_json(form: dict, maze=None) -> dict:
    """把表单数据映射成 agent.json(按 Schema);maze 用于坐标分配,缺省取全局默认。"""
    scratch = {
        "age": form.get("age", 35),
        "innate": form.get("innate", ""),
        "learned": form.get("learned", ""),
        "lifestyle": form.get("lifestyle", ""),
        "daily_plan": form.get("daily_plan", ""),
    }
    # 空间:表单选区域,映射成 living_area 地址
    # 注意:地址下拉可能含叶子(如"休息区:床"),只取到区域级,避免 床:床
    living_area = form.get("living_area", "the Ville:投资咨询中心:休息区").split(":")
    # 去掉末级可能是"床"等叶子(表单下拉含完整地址时)
    if living_area and living_area[-1] in ("床", "资料桌", "文件柜", "白板", "会议讲台", "会议座位", "休息沙发"):
        living_area = living_area[:-1]
    # 空间树:living_area 的父级路径
    tree = {}
    cur = tree
    for i, seg in enumerate(living_area[:-1]):
        cur[seg] = {}
        cur = cur[seg]
    # 仅当末级是"休息区"才加"床"(睡觉需要);其他区域不加叶子,避免地图校验失败
    if living_area and living_area[-1] == "休息区":
        cur[living_area[-1]] = ["床"]
    else:
        cur[living_area[-1]] = []

    agent = {
        "name": form.get("name", ""),
        "role_type": form.get("role_type", "user"),
        "coord": _auto_coord(living_area, maze=maze),  # 自动分配可达坐标,业务方不填
        "currently": form.get("currently", ""),
        "organization": form.get("organization", ""),
        "duty": {
            "position": form.get("position", ""),
            "responsibility": _split_lines(form.get("responsibility", "")),
            "authority": _split_lines(form.get("authority", "")),
            "rules": _split_lines(form.get("rules", "")),
        },
        # IVD:goals 已外部化到 governance.json(制度层);agent.json 只写
        # initial_tendency(人物初始底色,可选)——人设起点,由体验调制
        "initial_tendency": _parse_goals(form.get("initial_tendency", "")),
        "scratch": scratch,
        "spatial": {
            "address": {"living_area": living_area},
            "tree": tree,
        },
    }
    return agent


def _split_lines(text: str) -> list:
    """按换行/分号拆成列表,过滤空项"""
    items = []
    for line in str(text).replace("；", ";").replace("，", ",").split("\n"):
        for part in line.split(";"):
            part = part.strip()
            if part:
                items.append(part)
    return items


def _parse_goals(text: str) -> dict:
    """解析"目标:权重"行列表,如 '收益最大化:0.6\n风险规避:0.4'

    规则:
    - 每行"目标:权重"→ 按权重解析
    - 填了目标但没给权重(纯目标名)→ 给等权
    - 完全没填 → 返回空 dict(框架可接受,目标可选)
    """
    goals = {}
    unnamed = []
    for line in str(text).split("\n"):
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            k, v = line.split(":", 1)
        elif "：" in line:
            k, v = line.split("：", 1)
        else:
            unnamed.append(line)
            continue
        try:
            goals[k.strip()] = float(v.strip())
        except ValueError:
            unnamed.append(k.strip())
    # 有目标名但没权重 → 等权(已有权重时,未命名目标分剩余权重)
    if unnamed:
        used = sum(v for v in goals.values())
        remaining = max(0.0, 1.0 - used)
        for g in unnamed:
            goals[g] = remaining / len(unnamed) if remaining > 0 else 1.0 / (len(goals) + len(unnamed))
    return goals


# ---------------------------------------------------------------------------
# 落地:生成到 MAVIS 实际加载目录(frontend/static/assets/village/agents/<角色名>/)
# 贴图映射:从贴图池(agents_pool/,25 人小镇历史贴图)按哈希索引选择
# - 哈希式:hash(角色名) → 池中索引,确定性(同名角色永远同一贴图)
# - agent.json 记录 texture_ref(映射来源),供 Unity 端同样处理
# ---------------------------------------------------------------------------
AGENTS_ROOT = os.path.join(VILLAGE_ROOT, "agents")
POOL_ROOT = os.path.join(VILLAGE_ROOT, "agents_pool")
DEFAULT_TEXTURE_SOURCE = "沈砚之"  # 兜底贴图(池空时用)


def _safe_agent_name(name: str) -> str:
    """角色名守卫:只接受"名字",不接受路径。

    2026-09-23 安全体检发现:`save_agent()` / `upgrade_agent()` 原来只去掉制表符与
    换行,名字里带 `..\\..` 就能在 AGENTS_ROOT **之外**建目录、写 agent.json
    (角色名还进 `portrait` 路径)。`delete_agent` 早就有这个守卫,这两处漏了。
    角色名本来也不该含路径分隔符或冒号。
    """
    name = "".join(c for c in str(name or "") if c not in "\t\r\n").strip()
    if not name:
        raise ValueError("角色名不能为空")
    if "/" in name or "\\" in name or ":" in name or name in (".", ".."):
        raise ValueError("非法的角色名(不能含路径分隔符): {!r}".format(name))
    return name


def _pick_texture_ref(name: str) -> str:
    """从贴图池按角色名哈希选一个贴图来源(确定性映射)

    返回:池中角色名(如"伊莎贝拉");池不可用则返回默认来源。
    """
    if not os.path.isdir(POOL_ROOT):
        return DEFAULT_TEXTURE_SOURCE
    pool_names = sorted(
        d for d in os.listdir(POOL_ROOT)
        if os.path.exists(os.path.join(POOL_ROOT, d, "texture.png"))
    )
    if not pool_names:
        return DEFAULT_TEXTURE_SOURCE
    idx = abs(hash(name)) % len(pool_names)
    return pool_names[idx]


def save_agent(business: str, agent_json: dict, agents_root: str = "") -> str:
    # 角色名守卫(2026-09-23):原来只去制表符/换行,`../..` 能在 AGENTS_ROOT 之外
    # 建目录并写 agent.json;见 `_safe_agent_name()`。
    name = _safe_agent_name(agent_json.get("name", ""))
    agent_json["name"] = name
    agents_root = agents_root or AGENTS_ROOT
    agent_dir = os.path.join(agents_root, name)
    os.makedirs(agent_dir, exist_ok=True)

    # portrait 字段指向贴图路径(相对 frontend/static)
    agent_json["portrait"] = f"assets/village/agents/{name}/portrait.png"

    # 贴图映射:从池选来源,记录 texture_ref
    texture_ref = _pick_texture_ref(name)
    agent_json["texture_ref"] = texture_ref

    # 写入 agent.json
    path = os.path.join(agent_dir, "agent.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(agent_json, f, ensure_ascii=False, indent=2)

    # 复制所选来源的 portrait/texture(若不存在)
    src_dir = os.path.join(POOL_ROOT, texture_ref)
    if not os.path.isdir(src_dir):
        src_dir = os.path.join(AGENTS_ROOT, DEFAULT_TEXTURE_SOURCE)
    for fname in ("portrait.png", "texture.png"):
        src = os.path.join(src_dir, fname)
        dst = os.path.join(agent_dir, fname)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)

    return path


# ---------------------------------------------------------------------------
# 关系 / 剧情:追加到 scenarios/<业务>/relationships.json / story.json
# (框架从 scenarios/investment/ 加载)
# ---------------------------------------------------------------------------
def append_relationship(business: str, rel: dict) -> str:
    path = os.path.join(SCENARIOS_DIR, business, "relationships.json")
    data = {"relations": []}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"relations": []}
    data.setdefault("relations", []).append(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def append_story(business: str, ev: dict) -> str:
    path = os.path.join(SCENARIOS_DIR, business, "story.json")
    data = {"events": []}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"events": []}
    data.setdefault("events", []).append(ev)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# 升级现有角色:读旧 agent.json,保留原值,补全新字段(role_type/duty/goals/...)
# ---------------------------------------------------------------------------
def upgrade_agent(name: str, extra: dict = None) -> str:
    """把 frontend/static/assets/village/agents/<name>/agent.json 升级为全字段

    - 保留:portrait/coord/currently/scratch/spatial 原值
    - 新增:role_type(默认 user)/organization/duty/initial_tendency/values/intervention
    - extra 可覆盖新增字段(如 role_type 指定 ai_tool)
    """
    agent_dir = os.path.join(AGENTS_ROOT, _safe_agent_name(name))
    path = os.path.join(agent_dir, "agent.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"角色 {name} 不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        agent_json = json.load(f)

    extra = extra or {}
    # 清理 IVD 重构前的旧字段(goals 已外部化到 governance.json)
    agent_json.pop("goals", None)
    # 补全新字段(仅缺省时补,已有值保留)
    agent_json.setdefault("role_type", extra.get("role_type", "user"))
    agent_json.setdefault("organization", extra.get("organization", ""))
    agent_json.setdefault("duty", {
        "position": extra.get("position", ""),
        "responsibility": extra.get("responsibility", []),
        "authority": extra.get("authority", []),
        "rules": extra.get("rules", []),
    })
    agent_json.setdefault("initial_tendency", extra.get("initial_tendency", {}))

    # 校验(复用 MAVIS validator)
    maze = _load_maze()
    errors = validate_agents({name: agent_json}, maze)
    if errors:
        raise ValueError("校验未通过: " + "; ".join(errors))

    with open(path, "w", encoding="utf-8") as f:
        json.dump(agent_json, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
def _list_agents() -> list:
    """扫描已配置角色,返回完整详情(供列表页)"""
    agents = []
    if not os.path.isdir(AGENTS_ROOT):
        return agents
    for name in sorted(os.listdir(AGENTS_ROOT)):
        p = os.path.join(AGENTS_ROOT, name, "agent.json")
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            agents.append(d)  # 完整 agent.json
        except Exception:
            continue
    return agents


@app.get("/", response_class=RedirectResponse)
async def index():
    """收敛为单一作者入口:根路径直入场景创建器(独立角色/关系/剧情页由它承载)。"""
    return RedirectResponse(url="/scenario")


@app.get("/relationships", response_class=HTMLResponse)
async def relationships_page(request: Request):
    """关系录入页:追加关系到 relationships.json,并展示已添加条目"""
    path = os.path.join(SCENARIOS_DIR, "investment", "relationships.json")
    relations = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                relations = json.load(f).get("relations", [])
        except Exception:
            relations = []
    return templates.TemplateResponse(
        request, "relationships.html",
        {"relations": relations, "active": "relationships"}
    )


@app.get("/story", response_class=HTMLResponse)
async def story_page(request: Request):
    """剧情录入页:追加事件到 story.json,并展示已添加条目"""
    path = os.path.join(SCENARIOS_DIR, "investment", "story.json")
    events = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                events = json.load(f).get("events", [])
        except Exception:
            events = []
    return templates.TemplateResponse(
        request, "story.html",
        {"events": events, "active": "story"}
    )


@app.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    return templates.TemplateResponse(
        request, "agents.html",
        {"agents": _list_agents(), "active": "agents"}
    )


# ---------------------------------------------------------------------------
# 场景创建器(scenario.yaml 生成与落盘;确定性,复用引擎 schema 校验)
# ---------------------------------------------------------------------------
@app.get("/scenario", response_class=HTMLResponse)
async def scenario_page(request: Request):
    """场景创建页:表单 + 已发现场景列表。"""
    scenes = _list_scenarios()
    try:
        addresses = _maze_addresses(_load_maze())
    except Exception:
        addresses = []
    return templates.TemplateResponse(
        request, "scenario.html",
        {"scene_builder": scenario_builder,
         "scenes": scenes,
         "platform_dir": _PLATFORM_DIR,
         "engines": scenario_builder.SUPPORTED_ENGINES,
         "maze_candidates": _maze_candidates(),
         "addresses": addresses,
         "active": "scenario"},
    )


@app.get("/scenario/addresses")
async def scenario_addresses(maze: str = ""):
    """给定地图相对路径(空=默认全局),返回排序地址树供角色居住区下拉动态跟随所选地图。"""
    try:
        return {"addresses": _maze_addresses(_load_maze_rel(maze))}
    except RuntimeError as exc:  # 地图/平台目录不可用 —— 如实报,不返回空数组假装"这图没地址"
        return JSONResponse({"addresses": [], "errors": [str(exc)]})


def _list_scenarios() -> list:
    """扫描 cases_dir 下已落盘的场景 yaml,返回摘要列表。"""
    cases_root = scenario_builder.cases_dir(_PLATFORM_DIR)
    out = []
    if not os.path.isdir(cases_root):
        return out
    for cid in sorted(os.listdir(cases_root)):
        yp = os.path.join(cases_root, cid, "scenario.yaml")
        if not os.path.isfile(yp):
            continue
        try:
            import yaml
            with open(yp, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            meta = data.get("meta") or {}
            out.append({
                "case_id": meta.get("case_id") or cid,
                "name": meta.get("name", ""),
                "engine": meta.get("engine", ""),
                "n_roles": len(data.get("roles") or []),
                "path": yp,
            })
        except Exception:  # noqa: BLE001 —— 单场景读取失败不拖垮列表
            out.append({"case_id": cid, "name": "(解析失败)", "engine": "", "path": yp})
    return out


# ---------------------------------------------------------------------------
# 场景内容自包含(sandbox-value):场景创建器即单一作者入口,保存时把完整保真的
# 内容资产(agent.json/relationships/story/governance)写进该场景自带的 assets/,
# world.assets 指向它;roles 由所录角色内容推导,不再在声明层重复录。
# 独立角色配置/关系/剧情/已配置角色页随之废弃。
# ---------------------------------------------------------------------------
def scenario_assets_dir(case_id: str) -> str:
    """cases/<case_id>/assets/ —— 场景内容资产根(相对平台根的 rel 亦同)。"""
    return os.path.join(scenario_builder.cases_dir(_PLATFORM_DIR), case_id, "assets")


def _slug(name: str) -> str:
    """display_name → 唯一 id:小写、非字母数字压成下划线;全符号则保留原名。"""
    import re
    s = re.sub(r"\W+", "_", name.strip().lower()).strip("_")
    return s or name.strip()


def _engine_roles_from_agents(agents: list) -> list:
    """由 full-fidelity 角色内容推导引擎可见的 roles 子集(单一来源 = 内容)。"""
    roles = []
    for a in agents or []:
        disp = str(a.get("name") or "").strip()
        if not disp:
            continue
        roles.append({
            "id": _slug(disp),
            "display_name": disp,
            "type": "ai_tool" if a.get("role_type") == "ai_tool" else "user",
            "llm": "local", "system_prompt": "", "max_tokens": 2048, "temperature": 0.5,
        })
    return roles


def _write_scenario_content(case_id: str, form: dict) -> dict:
    """把 sandbox 场景的完整内容资产写进 cases/<case_id>/assets/。

    返回该目录下各资产的 rel 路径(正斜杠,供 world.assets 用);不生成 maze
    (结构性重资产,由表单复用既有地图路径)。
    """
    d = scenario_assets_dir(case_id)
    os.makedirs(d, exist_ok=True)
    # 本轮所选地图(空声明→默认),用于角色坐标自动分配,保证坐标落在运行时图内
    scene_maze = _load_maze_rel((form.get("asset_maze") or "").strip())
    rel = {
        "agents": "cases/{}/assets/agents".format(case_id),
        "story": "cases/{}/assets/story.json".format(case_id),
        "relationships": "cases/{}/assets/relationships.json".format(case_id),
        "governance": "cases/{}/assets/governance.json".format(case_id),
    }
    # 1) 每个角色 → 完整保真 assets/agents/<name>/agent.json(复用构建+落盘逻辑)
    #    跳过角色必须**说出来**(2026-09-21 修静默):以前 except: continue 会把角色悄悄丢掉,
    #    作者以为写进去了、场景里却没有 —— 这里打印原因并累计,调用方可据此提示。
    skipped_agents = []
    for a in form.get("agents") or []:
        try:
            agent_json = build_agent_json(a, maze=scene_maze)
            save_agent(case_id, agent_json, agents_root=os.path.join(d, "agents"))
        except (ValueError, KeyError) as exc:
            name = (a or {}).get("display_name") or (a or {}).get("id") or "?"
            skipped_agents.append("{}: {}: {}".format(name, type(exc).__name__, exc))
            print("[agents] 跳过角色 {}: {}: {}".format(name, type(exc).__name__, exc))
    # 2) 关系
    with open(os.path.join(d, "relationships.json"), "w", encoding="utf-8") as f:
        json.dump({"relations": list(form.get("relationships") or [])},
                  f, ensure_ascii=False, indent=2)
    # 3) 剧情
    with open(os.path.join(d, "story.json"), "w", encoding="utf-8") as f:
        json.dump({"events": list(form.get("story") or [])},
                  f, ensure_ascii=False, indent=2)
    # 4) 制度层价值权重:走**引擎的同一映射**(scenario_builder.governance_payload),
    #    不再本地再写一份口径;引擎不可用时它会在返回值里标 used_engine=False。
    _scenario_for_gov = scenario_builder.build_scenario(form)
    _gov = scenario_builder.governance_payload(_scenario_for_gov, _ENGINE_DIR)
    if _gov.get("roles"):
        with open(os.path.join(d, "governance.json"), "w", encoding="utf-8") as f:
            json.dump({"roles": _gov["roles"]}, f, ensure_ascii=False, indent=2)
        if not _gov.get("used_engine"):
            print("[governance] 运行方式不可用,已退回本地口径: {}".format(
                _gov.get("engine_note", "")))
    if skipped_agents:
        rel["skipped_agents"] = skipped_agents
    return rel


def _maze_candidates() -> list:
    """平台下既有 maze.json 相对路径(供沙盒场景复用地图,结构性重资产不表单生成)。"""
    out = []
    if not _PLATFORM_DIR:
        return out
    for root, dirs, files in os.walk(_PLATFORM_DIR):
        dirs[:] = [dd for dd in dirs if dd not in (".git", ".venv", ".venv-live",
                                                   "node_modules", "__pycache__", "dist")]
        if "maze.json" in files:
            rel = os.path.relpath(os.path.join(root, "maze.json"), _PLATFORM_DIR) \
                .replace("\\", "/")
            if rel not in out:
                out.append(rel)
    return sorted(out, key=lambda r: (len(r.split("/")), r))


def _sandbox_link(form: dict) -> dict:
    """sandbox-value 场景:roles 由录入的完整角色内容推导(单一来源)。"""
    if (form.get("engine") or "") != "sandbox-value":
        return form
    out = dict(form)
    out["roles"] = _engine_roles_from_agents(form.get("agents") or [])
    return out


def _sandbox_world_assets(case_id: str, form: dict, rel: dict) -> dict:
    """sandbox-value 场景的 world.assets:自包含内容资产 + 复用既有地图(maze)。"""
    assets = dict(rel)
    maze = (form.get("asset_maze") or "").strip()
    if maze:
        assets["maze"] = maze
    return assets


@app.post("/api/scenario/preview")
async def scenario_preview(request: Request):
    """接收表单 → 生成 scenario.yaml 文本 + 校验结果(不落盘,供预览/评审)。"""
    form = await request.json()
    form = _sandbox_link(form)
    try:
        cfg = scenario_builder.build_scenario(form)
    except Exception as exc:  # noqa: BLE001 —— 构建失败给可读错误
        return JSONResponse({"ok": False, "errors": ["构建失败: {}".format(exc)]})
    ok, errors = scenario_builder.validate_scenario(cfg, _ENGINE_DIR)
    body = {
        "ok": True,
        "yaml": scenario_builder.dump_scenario_yaml(cfg),
        "case_id": (cfg.get("meta") or {}).get("case_id"),
        "engine_check": ok,
        "errors": errors,
        "engine_available": _engine_available(),
        "engine_reason": engine_bridge.reason(_ENGINE_DIR),
    }
    return JSONResponse(body)


@app.post("/api/scenario/save")
async def scenario_save(request: Request):
    """校验通过后把场景写入 cases/<case_id>/;sandbox-value 场景同时生成完整内容资产。"""
    form = await request.json()
    form = _sandbox_link(form)
    try:
        cfg = scenario_builder.build_scenario(form)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "errors": ["构建失败: {}".format(exc)]})
    if not scenario_builder.cases_dir(_PLATFORM_DIR):
        return JSONResponse({"ok": False, "errors": [
            "场景无处落盘:平台目录未声明({} 或 MAVIS_PLATFORM_DIR),"
            "或用 CASE_ENGINE_CASES_ROOT 直接指定场景根".format(engine_bridge.ENV_DIR)]})
    case_id = (cfg.get("meta") or {}).get("case_id")
    try:
        # sandbox-value:先写完整内容资产,再把 world.assets 指向自包含目录
        if (form.get("engine") or "") == "sandbox-value":
            rel = _write_scenario_content(case_id, form)
            cfg["world"]["assets"] = _sandbox_world_assets(case_id, form, rel)
    except RuntimeError as exc:  # 地图/平台目录不可用等 —— 如实报,不写半成品
        return JSONResponse({"ok": False, "errors": ["内容资产写入失败: {}".format(exc)]})
    ok, errors = scenario_builder.validate_scenario(cfg, _ENGINE_DIR)
    if not ok:
        return JSONResponse({"ok": False, "errors": errors or ["校验未通过"]})
    path = scenario_builder.save_scenario(_PLATFORM_DIR, cfg)
    return JSONResponse({"ok": True, "path": path,
                         "case_id": case_id})


def _engine_available() -> bool:
    """引擎是否可用(决定校验深度提示与「组合/引擎」页是否置灰)。"""
    return engine_bridge.available(_ENGINE_DIR)


def _engine_catalog() -> dict:
    """引擎清单(「引擎」页只读展示用)。

    读引擎注册表本身(id/名称/说明/部件),再按发现的场景算"它能跑哪些" ——
    这里**不做任何配置写操作**:引擎是运行策略,本工具只负责让人看清"现在有哪些、
    各自能跑什么";配对与运行去「组合」页。

    引擎接触全部走 engine_bridge;不可用时返回 available=False + **可读原因**。
    """
    st = engine_bridge.status(_ENGINE_DIR)
    out = {"available": False, "engines": [], "scenarios": [],
           "reason": st["reason"], "dir": st["dir"]}
    if not st["available"]:
        return out

    infos = engine_bridge.discover("", _ENGINE_DIR)
    # 用 all_ids()(公开的"全部已注册引擎")—— describe() 无参只给默认引擎,
    # 照它列会漏掉 sandbox-value
    ids = engine_bridge.engine_ids(_ENGINE_DIR)

    def supported_of(info):
        try:
            return engine_bridge.supported_by(
                engine_bridge.load_yaml(info.path, _ENGINE_DIR), _ENGINE_DIR)
        except Exception:  # noqa: BLE001
            return []

    cat = []
    for eid in ids:
        meta = engine_bridge.describe(eid, _ENGINE_DIR)
        cat.append({"id": eid,
                    "name": meta.get("name") or eid,
                    "summary": meta.get("note") or meta.get("description") or "",
                    "primitives": meta.get("primitives") or "",
                    "output": meta.get("output") or "",
                    "components": engine_bridge.components(eid, _ENGINE_DIR),
                    "scenarios": [i.case_id for i in infos if eid in supported_of(i)]})
    out["available"] = True
    out["engines"] = cat
    out["scenarios"] = [{"case_id": i.case_id, "name": i.name,
                         "engine": getattr(i, "engine", "") or "",
                         "supported": supported_of(i)} for i in infos]
    return out


# ---------------------------------------------------------------------------
# 场景加载(反向解析 scenario.yaml → 表单,支撑"已有场景可编辑修改")
# 映射与 scenario_builder.build_scenario / _write_scenario_content 严格对偶,
# 保证"存进去的能原样取回来改",不做 AI 推断。
# ---------------------------------------------------------------------------
def _join_words(words) -> str:
    """词表 list → textarea 单行一个词文案(与 parse_words 可逆)。"""
    if not words:
        return ""
    return "\n".join(str(w) for w in words)


def _tendency_join(tend: dict) -> str:
    """initial_tendency dict → 每行"目标:权重"。"""
    if not tend:
        return ""
    return "\n".join("{}:{}".format(k, v) for k, v in tend.items())


def _load_scenario_dict(case_id: str) -> dict | None:
    """读 cases/<case_id>/scenario.yaml 返回原始 dict;不存在返回 None。"""
    import yaml
    root = scenario_builder.cases_dir(_PLATFORM_DIR)
    if not root:
        return None
    path = os.path.join(root, case_id, "scenario.yaml")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _form_from_scenario(case_id: str, data: dict) -> dict:
    """scenario dict → 场景创建器表单结构(collectForm 对偶)。"""
    meta = data.get("meta") or {}
    world = data.get("world") or {}
    form = {
        "engine": meta.get("engine", ""),
        "case_id": meta.get("case_id") or case_id,
        "name": meta.get("name", ""),
        "description": meta.get("description", ""),
        "start_date": meta.get("start_date", ""),
        "end_date": meta.get("end_date", ""),
    }
    # 角色(experiment-eval 手工 roles;沙盒由 agents 内容推导,下面单独装载)
    roles = []
    for r in data.get("roles") or []:
        roles.append({
            "id": r.get("id", ""),
            "display_name": r.get("display_name", ""),
            "type": r.get("type", "ai_tool"),
            "llm": r.get("llm", "local"),
            "system_prompt": r.get("system_prompt", ""),
            "max_tokens": r.get("max_tokens", 2048),
            "temperature": r.get("temperature", 0.5),
        })
    form["roles"] = roles
    # 世界状态 schema
    state_schema = []
    for key, f in (world.get("state_schema") or {}).items():
        state_schema.append({
            "field": key,
            "initial": f.get("initial"),
            "type": f.get("type", "str"),
        })
    form["state_schema"] = state_schema
    # 分支判定
    branch = data.get("branch") or {}
    form["branch_default"] = branch.get("default_branch", "")
    form["branch_no_buy"] = _join_words(branch.get("no_buy"))
    form["branch_refuse"] = _join_words(branch.get("refuse"))
    form["branch_conditional"] = _join_words(branch.get("conditional"))
    form["branch_anti_allin"] = _join_words(branch.get("anti_allin"))
    form["branch_fallback_map"] = json.dumps(
        branch.get("fallback_map") or {}, ensure_ascii=False, indent=4)
    # 一致性信号
    cons = data.get("consistency") or {}
    form["cons_buy_words"] = _join_words(cons.get("buy_words"))
    form["cons_cond_words"] = _join_words(cons.get("cond_words"))
    form["cons_negators"] = _join_words(cons.get("negators"))
    form["cons_neg_phrases"] = _join_words(cons.get("neg_phrases"))
    # 沙盒标准段 → 表单字段
    assets = world.get("assets") or {}
    form["asset_maze"] = assets.get("maze", "")
    form["value_tendency"] = json.dumps(
        world.get("value_tendency") or {}, ensure_ascii=False, indent=4)
    form["sandbox_params"] = json.dumps(
        world.get("params") or {}, ensure_ascii=False, indent=4)
    # 沙盒完整内容资产(roles/relationships/story):从 cases/<case_id>/assets 装载
    form["agents"] = _load_agents_content(case_id, data)
    form["relationships"] = _load_relations_content(case_id, data)
    form["story"] = _load_story_content(case_id, data)
    return form


def _asset_abs(data: dict, key: str, case_id: str, default_name: str) -> str:
    """按 world.assets 声明解析某个资产的真实绝对路径,缺声明回退默认。

    - 声明值是文件路径(如 case00/scenario/relationships.json)→ 返回其绝对路径;
    - 声明值是目录(如 world.assets.agents = case00/scenario/agents/)→ 返回该目录;
    - 无声明 → cases/<case_id>/assets/<default_name>;
    - 声明但文件不存在 → 回退默认(允许 CONFIG 侧尚未落盘的半成品)。
    """
    assets = ((data or {}).get("world") or {}).get("assets") or {}
    decl = str(assets.get(key) or "").strip()
    if decl:
        cand = os.path.abspath(os.path.join(_PLATFORM_DIR, decl.replace("\\", "/")))
        if os.path.isdir(cand) or os.path.isfile(cand) or key == "agents":
            return cand
    return os.path.join(scenario_assets_dir(case_id), default_name)


def _load_agents_content(case_id: str, data: dict) -> list:
    """从 agents 资产根(以 world.assets.agents 声明为准)agents/<name>/agent.json 反解析。

    build_agent_json 落盘的字段逐一映射还原(coord/spatial 由后端生成,不要求回填)。
    找不到资产目录时退回 roles 里能推断的 name(保证 experiment-eval 也能回填)。
    """
    agents_root = _asset_abs(data, "agents", case_id, "agents")
    out = []
    if os.path.isdir(agents_root):
        for name in sorted(os.listdir(agents_root)):
            p = os.path.join(agents_root, name, "agent.json")
            if not os.path.isfile(p):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    a = json.load(f)
                duty = a.get("duty") or {}
                spatial = a.get("spatial") or {}
                addr = spatial.get("address") or {}
                la = addr.get("living_area") or []
                scratch = a.get("scratch") or {}
                out.append({
                    "name": a.get("name", name),
                    "role_type": a.get("role_type", "user"),
                    "organization": a.get("organization", ""),
                    "living_area": ":".join(la) if isinstance(la, list) else "",
                    "currently": a.get("currently", ""),
                    "position": duty.get("position", ""),
                    "responsibility": "\n".join(duty.get("responsibility") or []),
                    "authority": "\n".join(duty.get("authority") or []),
                    "rules": "\n".join(duty.get("rules") or []),
                    "initial_tendency": _tendency_join(a.get("initial_tendency") or {}),
                    "age": scratch.get("age", ""),
                    "innate": scratch.get("innate", ""),
                    "learned": scratch.get("learned", ""),
                    "lifestyle": scratch.get("lifestyle", ""),
                    "daily_plan": scratch.get("daily_plan", ""),
                })
            except Exception:  # noqa: BLE001 —— 单个 agent 读取失败不拖垮编辑
                continue
    # 沙盒场景但无资产目录时,至少把 roles 对应的 display_name 回填
    if not out:
        meta = data.get("meta") or {}
        if (meta.get("engine") or "") == "sandbox-value":
            for r in data.get("roles") or []:
                nm = r.get("display_name") or ""
                if nm:
                    out.append({"name": nm, "role_type": "user",
                                "organization": "", "living_area": "",
                                "currently": "", "position": "",
                                "responsibility": "", "authority": "",
                                "rules": "", "initial_tendency": "",
                                "age": "", "innate": "", "learned": "",
                                "lifestyle": "", "daily_plan": ""})
    return out


def _load_relations_content(case_id: str, data: dict) -> list:
    path = _asset_abs(data, "relationships", case_id, "relationships.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return list((json.load(f) or {}).get("relations") or [])
    except Exception:  # noqa: BLE001
        return []


def _load_story_content(case_id: str, data: dict) -> list:
    path = _asset_abs(data, "story", case_id, "story.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return list((json.load(f) or {}).get("events") or [])
    except Exception:  # noqa: BLE001
        return []


@app.get("/api/scenario/load")
async def scenario_load(case_id: str):
    """按 case_id 读回已有场景,返回可直接回填场景创建器表单的结构。"""
    case_id = (case_id or "").strip()
    data = _load_scenario_dict(case_id) if case_id else None
    if data is None:
        return JSONResponse({"ok": False, "errors": ["场景不存在: {}".format(case_id)]})
    return JSONResponse({"ok": True, "form": _form_from_scenario(case_id, data)})


# ---------------------------------------------------------------------------
# 引擎运行器(界面切换跑 case00/case01 自检)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 组合(case×engine)注册表 —— 场景=数据,组合=可复用的运行记录;运行器直接选组合去跑
# ---------------------------------------------------------------------------
def _comps() -> list:
    return compositions.load()


@app.get("/api/compositions")
async def list_compositions():
    return JSONResponse({"ok": True, "compositions": _comps()})


@app.post("/api/compositions")
async def create_composition(request: Request):
    body = await request.json() or {}
    comps = _comps()
    new_comps, ok, payload = compositions.create(
        comps,
        case_id=body.get("case_id"),
        engine_id=body.get("engine_id"),
        name=body.get("name"),
        description=body.get("description"),
        platform_dir=_PLATFORM_DIR,
    )
    if not ok:
        return JSONResponse({"ok": False, "errors": payload})
    compositions.save(new_comps)
    return JSONResponse({"ok": True, "composition": payload})


@app.delete("/api/compositions/{comp_id}")
async def delete_composition(comp_id: str):
    comps = _comps()
    new_comps, removed = compositions.delete(comps, comp_id)
    if not removed:
        return JSONResponse({"ok": False, "errors": ["组合不存在: {}".format(comp_id)]})
    compositions.save(new_comps)
    return JSONResponse({"ok": True})


@app.patch("/api/compositions/{comp_id}")
async def update_composition(comp_id: str, request: Request):
    body = await request.json() or {}
    comps = _comps()
    action = (body or {}).get("action") or ""
    if action == "rename":
        ok, _ = compositions.rename(comps, comp_id, (body or {}).get("name", ""))
    elif action == "toggle":
        ok, _ = compositions.set_enabled(
            comps, comp_id, bool((body or {}).get("enabled")))
    elif action == "default":
        c, _ = compositions.set_default(comps, comp_id)
        ok = c is not None
    else:
        return JSONResponse({"ok": False, "errors": ["未知 action: {}".format(action)]})
    if not ok:
        return JSONResponse({"ok": False, "errors": ["组合不存在: {}".format(comp_id)]})
    compositions.save(comps)
    return JSONResponse({"ok": True})


@app.get("/run", response_class=HTMLResponse)
async def run_page(request: Request):
    """运行页 = 「组合」页:列出 scene×engine 的组合记录,点选一个即跑。

    `/composition` 是同一页的正式地址(`/run` 保留兼容);两者都高亮顶栏「组合」。
    """
    cases = engine_runner.list_cases(_PLATFORM_DIR)
    return templates.TemplateResponse(
        request, "run.html",
        {"cases": cases,
         "compositions": _comps(),
         "engine_options": scenario_builder.SUPPORTED_ENGINES,
         "platform_dir": _PLATFORM_DIR,
         "engine_available": _engine_available(),
         "active": "composition"},
    )


@app.get("/composition", response_class=HTMLResponse)
async def composition_page(request: Request):
    """「组合」= 场景 × 引擎(与 /run 同一页)。"""
    return await run_page(request)


@app.get("/engines", response_class=HTMLResponse)
async def engines_page(request: Request):
    """「引擎」页:只读展示当前可用引擎与它们各自能跑的场景。

    (引擎是运行策略,改它属于框架侧;本页只做展示 + 指路到「组合」页。)
    """
    cat = _engine_catalog()
    return templates.TemplateResponse(
        request, "engines.html",
        {"engines": cat.get("engines") or [],
         "scenarios": cat.get("scenarios") or [],
         "available": bool(cat.get("available")),
         "dir": cat.get("dir", ""),
         "reason": cat.get("reason", ""),
         "active": "engines"},
    )


@app.post("/api/run/execute")
async def run_execute(request: Request):
    """执行一次运行。优先用「组合记录」(composition_id);否则按 case_id+engine_id 临时跑。

    - composition_id 提供:取组合记录 → case/engine,并校验记录 enabled;
    - 否则沿用传入的 case_id+engine_id(允许临时试跑未登记组合)。
    """
    body = await request.json()
    comp_id = str((body or {}).get("composition_id") or "").strip()
    case_id = str((body or {}).get("case_id") or "").strip()
    engine_id = str((body or {}).get("engine_id") or "").strip()
    input_text = str((body or {}).get("input_text") or "").strip()
    if comp_id:
        c = compositions.find(_comps(), comp_id)
        if c is None:
            return JSONResponse({"ok": False, "errors": [
                "组合不存在: {};请先在组合列表创建".format(comp_id)],
                "case_id": "", "engine_used": ""})
        if not c.get("enabled", True):
            return JSONResponse({"ok": False, "errors": [
                "组合已禁用: {}".format(c.get("name"))],
                "case_id": c.get("case_id"), "engine_used": c.get("engine_id")})
        case_id = str(c.get("case_id") or "")
        engine_id = str(c.get("engine_id") or "")
    if not case_id:
        return JSONResponse({"ok": False, "errors": ["case_id 必填(或提供组合折叠的 composition_id)"]})
    ok, summary, errors = engine_runner.run_case(
        _PLATFORM_DIR, case_id, engine_id=engine_id, input_text=input_text)
    # 联动 5010:运行组合即把实时入口切到该 case(非阻塞;case00 首页=小镇+治理面板,case01=小镇+结果)
    live = _launch_live(case_id)
    return JSONResponse({"ok": ok, "summary": summary, "errors": errors,
                         "case_id": case_id, "engine_used": summary.get("_engine_id", ""),
                         "live": live})


@app.post("/api/generate")
async def generate(request: Request):
    form = await request.json()
    business = form.get("business", "").strip()
    if not business:
        return JSONResponse({"ok": False, "errors": ["业务名称不能为空"]})

    try:
        agent_json = build_agent_json(form)
        # 校验(复用 MAVIS validator)
        maze = _load_maze()
    except RuntimeError as exc:  # 地图/平台目录不可用 —— 如实报,不写半成品
        return JSONResponse({"ok": False, "errors": [str(exc)]})
    errors = validate_agents({agent_json["name"]: agent_json}, maze)
    if errors:
        return JSONResponse({"ok": False, "errors": errors})

    path = save_agent(business, agent_json)
    return JSONResponse({
        "ok": True,
        "path": path,
        "agent": agent_json,
    })


@app.post("/api/upgrade")
async def upgrade(request: Request):
    """升级现有角色为全字段(读旧 agent.json,补新字段)"""
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"ok": False, "errors": ["角色名不能为空"]})
    try:
        path = upgrade_agent(name, body)
        with open(path, "r", encoding="utf-8") as f:
            agent_json = json.load(f)
        return JSONResponse({"ok": True, "path": path, "agent": agent_json})
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        return JSONResponse({"ok": False, "errors": [str(e)]})


@app.post("/api/relationship")
async def add_relationship(request: Request):
    """追加一条角色关系到 relationships.json"""
    body = await request.json()
    business = body.get("business", "investment").strip()
    agents = [a.strip() for a in body.get("agents", "").split(",") if a.strip()]
    if len(agents) != 2:
        return JSONResponse({"ok": False, "errors": ["关系需要恰好两个角色,用逗号分隔"]})
    rel_type = body.get("type", "").strip()
    if not rel_type:
        return JSONResponse({"ok": False, "errors": ["关系类型(type)为必填"]})
    rel = {
        "agents": agents,
        "type": rel_type,
        "direction": body.get("direction", "").strip(),
        "trigger": body.get("trigger", "").strip(),
        "frequency": body.get("frequency", "medium").strip(),
    }
    path = append_relationship(business, rel)
    return JSONResponse({"ok": True, "path": path, "relation": rel})


@app.post("/api/relationship/delete")
async def delete_relationship(request: Request):
    """按序号删除一条关系(序号 = 列表页行号,从 0 开始)"""
    body = await request.json()
    business = body.get("business", "investment").strip()
    index = body.get("index")
    if not isinstance(index, int) or index < 0:
        return JSONResponse({"ok": False, "errors": ["缺少有效的 index(从 0 开始的行号)"]})
    path = os.path.join(SCENARIOS_DIR, business, "relationships.json")
    if not os.path.exists(path):
        return JSONResponse({"ok": False, "errors": ["relationships.json 不存在"]})
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rels = data.get("relations", [])
    if index >= len(rels):
        return JSONResponse({"ok": False, "errors": [f"index {index} 超出范围(共 {len(rels)} 条)"]})
    removed = rels.pop(index)
    data["relations"] = rels
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return JSONResponse({"ok": True, "removed": removed})


@app.post("/api/story")
async def add_story(request: Request):
    """追加一条剧情事件到 story.json"""
    body = await request.json()
    business = body.get("business", "investment").strip()
    time_ = body.get("time", "").strip()
    event_type = body.get("event_type", "").strip()
    content = body.get("content", "").strip()
    if not time_ or not event_type or not content:
        return JSONResponse({"ok": False, "errors": ["触发时间(time)/事件类型(event_type)/事件内容(content)均为必填"]})
    import re
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", time_):
        return JSONResponse({"ok": False, "errors": [f"触发时间格式应为 HH:MM(00:00-23:59),得到 '{time_}'"]})
    ev = {
        "id": body.get("id", "").strip() or f"s-{int(__import__('time').time())}",
        "time": time_,
        "event_type": event_type,
        "content": content,
        "targets": body.get("targets", "all").strip() or "all",
        "expected": body.get("expected", "").strip(),
    }
    importance = body.get("importance")
    if importance:
        ev["importance"] = int(importance)
    path = append_story(business, ev)
    return JSONResponse({"ok": True, "path": path, "event": ev})


@app.post("/api/story/delete")
async def delete_story(request: Request):
    """按 id 删除一条剧情事件"""
    body = await request.json()
    business = body.get("business", "investment").strip()
    ev_id = str(body.get("id", "")).strip()
    if not ev_id:
        return JSONResponse({"ok": False, "errors": ["缺少剧情 id"]})
    path = os.path.join(SCENARIOS_DIR, business, "story.json")
    if not os.path.exists(path):
        return JSONResponse({"ok": False, "errors": ["story.json 不存在"]})
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    events = data.get("events", [])
    kept = [e for e in events if str(e.get("id", "")) != ev_id]
    if len(kept) == len(events):
        return JSONResponse({"ok": False, "errors": [f"未找到 id={ev_id} 的剧情"]})
    data["events"] = kept
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return JSONResponse({"ok": True, "removed_id": ev_id})


@app.post("/api/agent/delete")
async def delete_agent(request: Request):
    """按角色名删除角色目录(agent.json + 贴图)"""
    body = await request.json()
    name = str(body.get("name", "")).strip()
    # 防路径穿越:与 save_agent / upgrade_agent 用**同一处**守卫(策略只有一个来源)
    try:
        name = _safe_agent_name(name)
    except ValueError:
        return JSONResponse({"ok": False, "errors": ["非法的角色名"]})
    agent_dir = os.path.join(AGENTS_ROOT, name)
    if not os.path.isdir(agent_dir):
        return JSONResponse({"ok": False, "errors": [f"角色 {name} 不存在"]})
    shutil.rmtree(agent_dir)
    return JSONResponse({"ok": True, "name": name})


@app.get("/api/export")
async def export_configs():
    """把所有已配置角色 + 关系/剧情打包成 zip 下载

    zip 结构:
        agents/<角色名>/agent.json + portrait.png + texture.png
        scenarios/<业务>/relationships.json
        scenarios/<业务>/story.json
    """
    import zipfile
    import io as _io

    buf = _io.BytesIO()
    count_agents, count_scenarios = 0, 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1) 角色文件夹(agents 目录下每个角色一个文件夹)
        if os.path.isdir(AGENTS_ROOT):
            for name in sorted(os.listdir(AGENTS_ROOT)):
                agent_dir = os.path.join(AGENTS_ROOT, name)
                if not os.path.isdir(agent_dir):
                    continue
                for fname in os.listdir(agent_dir):
                    fp = os.path.join(agent_dir, fname)
                    if os.path.isfile(fp):
                        zf.write(fp, f"agents/{name}/{fname}")
                count_agents += 1
        # 2) 场景(每个业务目录下的 relationships.json / story.json)
        if os.path.isdir(SCENARIOS_DIR):
            for biz in sorted(os.listdir(SCENARIOS_DIR)):
                biz_dir = os.path.join(SCENARIOS_DIR, biz)
                if not os.path.isdir(biz_dir):
                    continue
                wrote = False
                for fname in ("relationships.json", "story.json"):
                    fp = os.path.join(biz_dir, fname)
                    if os.path.isfile(fp):
                        zf.write(fp, f"scenarios/{biz}/{fname}")
                        wrote = True
                if wrote:
                    count_scenarios += 1

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="mavis-configs.zip"'},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8060, log_level="info")
