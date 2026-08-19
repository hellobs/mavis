"""实时模拟 + 可视化服务(FastAPI + WebSocket 版)

替代 live.py 的 Flask+SSE,推送同一套 framework 契约消息(protocol.py)。
- 页面渲染:Jinja2 模板(复用现有前端)
- 实时推送:WebSocket /ws(双向,为 Unity 交互铺路)
- 模拟调度:复用现有 SimulateServer + LiveCompressor,广播走 WebSocket

Flask 版 live.py 源代码保留(不再作为运行入口)。
"""
import os
import json
import queue
import threading
from typing import Dict, List

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from start import SimulateServer, personas, get_config, get_config_from_log
from compress import LiveCompressor
import modules.agent as agent_module

from framework.runtime.protocol import AgentState, TimeMsg, ChatLineMsg, validate_message

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Generative Agents Live (FastAPI)")
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE_DIR, "frontend/static")),
    name="static",
)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "frontend/templates"))


# ---------------------------------------------------------------------------
# WebSocket 连接管理(线程安全的广播)
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self._queues: Dict[WebSocket, "asyncio.Queue"] = {}
        self._lock = threading.Lock()

    def register(self, ws: WebSocket, q) -> None:
        with self._lock:
            self._queues[ws] = q

    def unregister(self, ws: WebSocket) -> None:
        with self._lock:
            self._queues.pop(ws, None)

    def broadcast(self, data: dict) -> None:
        """线程安全:向所有连接队列投放消息(WebSocket 发送由各自的协程执行)"""
        if not validate_message(data):
            print(f"[protocol] 非契约消息: type={data.get('type')}", flush=True)
        with self._lock:
            for q in self._queues.values():
                q.put_nowait(data)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._queues)


manager = ConnectionManager()

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
sim_state = {"status": "idle", "error": "", "start_time": "", "stride": 2}
compressor = None
server = None


def conversation_text(conversation, step_time):
    text = ""
    if step_time in conversation:
        for chats in conversation[step_time]:
            for persons, chat in chats.items():
                text += f"\n地点：{persons.split(' @ ')[1]}\n\n"
                for c in chat:
                    text += f"{c[0]}：{c[1]}\n"
    return {step_time: text}


def on_agent(name, agent_data, step, sim_time):
    global compressor, server
    if compressor is None:
        return
    agent_state, _, description = compressor.add_agent(name, agent_data, step, sim_time)
    conv_text = {}
    if server is not None and sim_time in server.game.conversation:
        conv_text = conversation_text(server.game.conversation, sim_time)
    msg: AgentState = {
        "type": "agent",
        "name": agent_state["name"],
        "coord": agent_state["coord"],
        "path": agent_state["path"],
        "action": agent_state["action"],
        "location": agent_state["location"],
        "currently": agent_data.get("currently", ""),
        "conversation": conv_text,
    }
    if description:
        msg["description"] = description
    manager.broadcast(msg)


def on_step(config):
    msg: TimeMsg = {"type": "time", "time": config["time"]}
    manager.broadcast(msg)


def on_chat_line(speaker, text):
    msg: ChatLineMsg = {"type": "chat_line", "speaker": speaker, "text": text}
    manager.broadcast(msg)


def run_simulation(name, sim_config, start_step, step, stride):
    """后台线程运行模拟"""
    global server, compressor
    try:
        agent_module.chat_callback = on_chat_line
        checkpoints_folder = f"results/checkpoints/{name}"
        compressor = LiveCompressor(checkpoints_folder, "frontend/static")
        server = SimulateServer(
            name, "frontend/static", checkpoints_folder, sim_config, start_step, "info"
        )
        sim_state["status"] = "running"
        if step <= 0:
            while True:
                server.simulate(1, stride, on_step=on_step, on_agent=on_agent)
                server.start_step += 1
        else:
            server.simulate(step, stride, on_step=on_step, on_agent=on_agent)
        sim_state["status"] = "done"
        manager.broadcast({"type": "done"})
    except Exception as e:
        sim_state["status"] = "error"
        sim_state["error"] = str(e)
        manager.broadcast({"type": "error", "message": str(e)})


def load_initial_payload(start_datetime, stride):
    from datetime import datetime
    persona_init_pos = {}
    description = {}
    for name in personas:
        json_path = os.path.join(
            "frontend/static", f"assets/village/agents/{name}/agent.json"
        )
        with open(json_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)
        persona_init_pos[name] = json_data["coord"]
        description[name] = {
            "currently": json_data["currently"],
            "scratch": json_data["scratch"],
        }
    return {
        "start_datetime": datetime.strptime(start_datetime, "%Y%m%d-%H:%M").isoformat(),
        "stride": stride,
        "sec_per_step": stride,
        "persona_init_pos": persona_init_pos,
        "all_movement": {"description": description, "conversation": {}},
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    speed = int(request.query_params.get("speed", 0))
    zoom = float(request.query_params.get("zoom", 0))
    if speed < 0:
        speed = 0
    elif speed > 5:
        speed = 5
    play_speed = 2 ** speed
    payload = load_initial_payload(sim_state["start_time"], sim_state["stride"])
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "persona_names": personas,
            "step": 1,
            "play_speed": play_speed,
            "zoom": zoom,
            "live_mode": True,
            **payload,
        },
    )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    q: "asyncio.Queue" = __import__("asyncio").Queue()
    manager.register(ws, q)
    # 初始消息:确认连接 + 快照(追赶进度)
    await ws.send_json({"type": "init"})
    if compressor is not None and compressor.started:
        await ws.send_json(compressor.snapshot())
    try:
        while True:
            data = await q.get()
            await ws.send_json(data)
    except WebSocketDisconnect:
        manager.unregister(ws)
    except Exception:
        manager.unregister(ws)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="live simulation server (FastAPI)")
    parser.add_argument("--name", type=str, default="", help="The simulation name")
    parser.add_argument("--start", type=str, default="20250213-09:30", help="The starting time of the simulated ville")
    parser.add_argument("--resume", action="store_true", help="Resume running the simulation")
    parser.add_argument("--step", type=int, default=0, help="The simulate step (<=0 means run forever)")
    parser.add_argument("--stride", type=int, default=2, help="The step stride in minute")
    parser.add_argument("--port", type=int, default=5001, help="The server port")
    args = parser.parse_args()

    name = args.name
    if len(name) < 1:
        name = input("Please enter a simulation name (e.g. sim-test): ")

    if args.resume:
        while not os.path.exists(f"results/checkpoints/{name}"):
            name = input(f"'{name}' doesn't exists, please re-enter the simulation name: ")
    else:
        while os.path.exists(f"results/checkpoints/{name}"):
            name = input(f"The name '{name}' already exists, please enter a new name: ")

    checkpoints_folder = f"results/checkpoints/{name}"
    if args.resume:
        sim_config = get_config_from_log(checkpoints_folder)
        if sim_config is None:
            print("No checkpoint file found to resume running.")
            exit(0)
        start_step = sim_config["step"]
    else:
        sim_config = get_config(args.start, args.stride, personas)
        start_step = 0

    sim_state["start_time"] = sim_config["time"]["start"]
    sim_state["stride"] = args.stride

    sim_thread = threading.Thread(
        target=run_simulation,
        args=(name, sim_config, start_step, args.step, args.stride),
        daemon=True,
    )
    sim_thread.start()

    print(f"Live simulation '{name}' started (FastAPI). Open http://127.0.0.1:{args.port}/")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
