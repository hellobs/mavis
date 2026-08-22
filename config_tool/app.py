"""config_tool.app — MAVIS 角色配置生成工具(独立服务,端口 5002)

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

# MAVIS 根目录(本工具与 generative_agents/ 同级)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAVIS_DIR = os.path.join(os.path.dirname(BASE_DIR), "generative_agents")
sys.path.insert(0, MAVIS_DIR)  # 允许 import framework.*

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

# 复用 MAVIS 的 validator(Schema 单一来源)
from framework.config.validator import (
    validate_agents, validate_relationships, validate_story,
)

app = FastAPI(title="MAVIS 角色配置工具")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

SCENARIOS_DIR = os.path.join(MAVIS_DIR, "scenarios")
MAZE_PATH = os.path.join(MAVIS_DIR, "frontend/static/assets/village/maze.json")


def _load_maze():
    with open(MAZE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 表单 → agent.json 的确定性映射(字段一一对应,不做 AI 解析)
# ---------------------------------------------------------------------------
def build_agent_json(form: dict) -> dict:
    """把表单数据映射成 agent.json(按 Schema)"""
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
        "coord": [int(form.get("coord_x", 0)), int(form.get("coord_y", 0))],
        "currently": form.get("currently", ""),
        "organization": form.get("organization", ""),
        "duty": {
            "position": form.get("position", ""),
            "responsibility": _split_lines(form.get("responsibility", "")),
            "authority": _split_lines(form.get("authority", "")),
            "rules": _split_lines(form.get("rules", "")),
        },
        "goals": _parse_goals(form.get("goals", "")),
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
    """解析"目标:权重"行列表,如 '收益最大化:0.6\n风险规避:0.4'"""
    goals = {}
    for line in str(text).split("\n"):
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            k, v = line.split(":", 1)
        elif "：" in line:
            k, v = line.split("：", 1)
        else:
            continue
        try:
            goals[k.strip()] = float(v.strip())
        except ValueError:
            continue
    return goals


# ---------------------------------------------------------------------------
# 落地:生成到 MAVIS 实际加载目录(frontend/static/assets/village/agents/<角色名>/)
# 贴图映射:从贴图池(agents_pool/,25 人小镇历史贴图)按哈希索引选择
# - 哈希式:hash(角色名) → 池中索引,确定性(同名角色永远同一贴图)
# - agent.json 记录 texture_ref(映射来源),供 Unity 端同样处理
# ---------------------------------------------------------------------------
AGENTS_ROOT = os.path.join(MAVIS_DIR, "frontend/static/assets/village/agents")
POOL_ROOT = os.path.join(MAVIS_DIR, "frontend/static/assets/village/agents_pool")
DEFAULT_TEXTURE_SOURCE = "沈砚之"  # 兜底贴图(池空时用)


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


def save_agent(business: str, agent_json: dict) -> str:
    # 清理角色名:去掉首尾空白/制表符(Windows 路径不允许制表符等)
    name = str(agent_json.get("name", "")).strip()
    name = "".join(c for c in name if c not in "\t\r\n")
    if not name:
        raise ValueError("角色名不能为空")
    agent_json["name"] = name
    agent_dir = os.path.join(AGENTS_ROOT, name)
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
# 升级现有角色:读旧 agent.json,保留原值,补全新字段(role_type/duty/goals/...)
# ---------------------------------------------------------------------------
def upgrade_agent(name: str, extra: dict = None) -> str:
    """把 frontend/static/assets/village/agents/<name>/agent.json 升级为全字段

    - 保留:portrait/coord/currently/scratch/spatial 原值
    - 新增:role_type(默认 user)/organization/duty/goals/values/intervention
    - extra 可覆盖新增字段(如 role_type 指定 ai_tool)
    """
    agent_dir = os.path.join(AGENTS_ROOT, name)
    path = os.path.join(agent_dir, "agent.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"角色 {name} 不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        agent_json = json.load(f)

    extra = extra or {}
    # 补全新字段(仅缺省时补,已有值保留)
    agent_json.setdefault("role_type", extra.get("role_type", "user"))
    agent_json.setdefault("organization", extra.get("organization", ""))
    agent_json.setdefault("duty", {
        "position": extra.get("position", ""),
        "responsibility": extra.get("responsibility", []),
        "authority": extra.get("authority", []),
        "rules": extra.get("rules", []),
    })
    agent_json.setdefault("goals", extra.get("goals", {}))

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


@app.get("/", response_class=HTMLResponse)
async def form_page(request: Request):
    maze = _load_maze()
    # 提供给表单的地址选项(业务方下拉选,不用知道技术地址)
    addresses = []
    for t in maze.get("tiles", []):
        a = t.get("address", [])
        if len(a) >= 2:
            addr = ":".join(a)
            if addr not in addresses:
                addresses.append(addr)
    return templates.TemplateResponse(
        request, "form.html", {"addresses": sorted(addresses)}
    )


@app.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    return templates.TemplateResponse(
        request, "agents.html", {"agents": _list_agents()}
    )


@app.post("/api/generate")
async def generate(request: Request):
    form = await request.json()
    business = form.get("business", "").strip()
    if not business:
        return JSONResponse({"ok": False, "errors": ["业务名称不能为空"]})

    agent_json = build_agent_json(form)

    # 校验(复用 MAVIS validator)
    maze = _load_maze()
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
    except (FileNotFoundError, ValueError) as e:
        return JSONResponse({"ok": False, "errors": [str(e)]})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5002, log_level="info")
