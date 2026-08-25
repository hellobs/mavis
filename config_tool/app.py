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

# MAVIS 框架包目录(本仓库内,config_tool 与 mavisframework/ 同级)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAVIS_DIR = os.path.join(os.path.dirname(BASE_DIR), "mavisframework")
sys.path.insert(0, MAVIS_DIR)  # 允许 import mavisframework.*

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

# 复用 MAVIS 的 validator(Schema 单一来源)
from mavisframework.config.validator import (
    validate_agents, validate_relationships, validate_story,
)

app = FastAPI(title="MAVIS 角色配置工具")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ---------------------------------------------------------------------------
# 路径注入(config_tool 属框架,但产物写入平台的前端资源)
# MAVIS_ASSETS_ROOT   : 平台前端资源根(frontend/static/assets/village)
# MAVIS_SCENARIOS_DIR : 业务场景目录(scenarios)
# 默认指向兄弟目录 ../provenance(本地开发时 mavis 与 provenance 并列);
# 部署时通过环境变量指向平台仓库对应目录。
# ---------------------------------------------------------------------------
_PLATFORM_DIR = os.path.join(os.path.dirname(os.path.dirname(BASE_DIR)), "provenance")
VILLAGE_ROOT = os.environ.get(
    "MAVIS_ASSETS_ROOT",
    os.path.join(_PLATFORM_DIR, "frontend", "static", "assets", "village"),
)
SCENARIOS_DIR = os.environ.get(
    "MAVIS_SCENARIOS_DIR",
    os.path.join(_PLATFORM_DIR, "scenarios"),
)
MAZE_PATH = os.path.join(VILLAGE_ROOT, "maze.json")


def _load_maze():
    with open(MAZE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 表单 → agent.json 的确定性映射(字段一一对应,不做 AI 解析)
# ---------------------------------------------------------------------------
def _auto_coord(living_area: list) -> list:
    """从地图自动分配该地址下的一个可达坐标(业务方不用填坐标)

    匹配规则(按优先级):
    1) 精确:tile.address == living_area(如 living_area 本身就是可达区域)
    2) 包含:tile.address 以 living_area 开头(区域内更深一级 tile,如 资料室:办公桌)
    3) 前缀兜底:living_area 以 tile.address 开头(区域级 tile,如 投资咨询中心)
    world 前缀(如 "the Ville")与 tile address 不对齐,先剥掉再匹配。
    """
    maze = _load_maze()
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
        "coord": _auto_coord(living_area),  # 自动分配可达坐标,业务方不填
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
        request, "form.html", {"addresses": sorted(addresses), "active": "config"}
    )


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
    # 防路径穿越:角色名不能含路径分隔符
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return JSONResponse({"ok": False, "errors": ["非法的角色名"]})
    agent_dir = os.path.join(AGENTS_ROOT, name)
    if not os.path.isdir(agent_dir):
        return JSONResponse({"ok": False, "errors": [f"角色 {name} 不存在"]})
    shutil.rmtree(agent_dir)
    return JSONResponse({"ok": True, "name": name})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5002, log_level="info")
