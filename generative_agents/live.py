"""实时模拟 + 可视化服务

与 start.py + compress.py + replay.py 的"先跑后放"模式不同，live.py 把模拟与
可视化合并到同一个进程：模拟每完成一个 step，立即通过 SSE（Server-Sent Events）
将该 step 的动画帧推送给浏览器，实现"边跑边看"的实时效果。

模拟过程仍会照常落盘（results/checkpoints/<name>/），事后仍可用 compress.py +
replay.py 压缩回放，两个模式互不影响。

用法：
    cd generative_agents
    python live.py --name sim-test --start "20250213-09:30" --stride 10 --step 0

然后浏览器打开 http://127.0.0.1:5001/ 即可实时观看。

参数说明：
    --name    虚拟小镇名称（唯一，用于存档与恢复）
    --start   起始时间
    --resume  从上次断点继续运行
    --step    迭代步数，<=0 表示持续运行直到手动停止（默认 0）
    --stride  每一步在虚拟小镇中对应的分钟数
    --port    服务端口（默认 5001，避免与 replay.py 的 5000 冲突）
"""

import os
import json
import queue
import threading
import argparse
from datetime import datetime

from flask import Flask, render_template, Response, request

from start import SimulateServer, personas, get_config, get_config_from_log
from compress import LiveCompressor
import modules.agent as agent_module

# 框架契约:前端(Phaser/Unity)统一消费 protocol 定义的消息结构
from framework.runtime.protocol import AgentState, TimeMsg, ChatLineMsg, validate_message


def on_chat_line(speaker, text):
    """对话逐句实时推送(每生成一句话立即发给前端,不用等整段完成)"""
    msg: ChatLineMsg = {"type": "chat_line", "speaker": speaker, "text": text}
    broadcast(msg)

app = Flask(
    __name__,
    template_folder="frontend/templates",
    static_folder="frontend/static",
    static_url_path="/static",
)


@app.after_request
def no_cache_html(resp):
    """HTML 页面不缓存(模板每次渲染最新版);静态资源(瓦片/图片)正常缓存"""
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

# ---- SSE 客户端管理 ----
clients = list()
clients_lock = threading.Lock()


def broadcast(data):
    """向所有已连接的 SSE 客户端推送一条消息(框架契约校验)"""
    if not validate_message(data):
        print(f"[protocol] 非契约消息: type={data.get('type')}", flush=True)
    payload = json.dumps(data, ensure_ascii=False)
    with clients_lock:
        for q in clients:
            q.put(payload)


# ---- 全局状态 ----
sim_state = {"status": "idle", "error": "", "start_time": "", "stride": 10}
server = None
compressor = None


def conversation_text(conversation, step_time):
    """把内存中的对话记录格式化为文本"""
    text = ""
    if step_time in conversation:
        for chats in conversation[step_time]:
            for persons, chat in chats.items():
                text += f"\n地点：{persons.split(' @ ')[1]}\n\n"
                for c in chat:
                    text += f"{c[0]}：{c[1]}\n"
    return {step_time: text}


def on_agent(name, agent_data, step, sim_time):
    """单个 Agent 思考完成时调用:推送该 Agent 的最新状态(前端直接驱动移动)"""
    global compressor, server
    if compressor is None:
        return
    agent_state, _, description = compressor.add_agent(
        name, agent_data, step, sim_time
    )
    # 从内存读取当前 step 的对话(文件是整步结束后才写入,并行下读文件会拿到旧数据)
    conv_text = {}
    if server is not None and sim_time in server.game.conversation:
        conv_text = conversation_text(server.game.conversation, sim_time)
    # 按框架契约(protocol.AgentState)构造消息:坐标/路径/动作/地点/当前状态/对话
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
    broadcast(msg)


def on_step(config):
    """整步完成时调用:推送模拟时间(前端更新时钟显示)"""
    msg: TimeMsg = {"type": "time", "time": config["time"]}
    broadcast(msg)


def run_simulation(name, sim_config, start_step, step, stride):
    """在后台线程中运行模拟"""
    global server, compressor
    try:
        # 对话逐句回调:每生成一句话就实时推送
        agent_module.chat_callback = on_chat_line
        checkpoints_folder = f"results/checkpoints/{name}"
        compressor = LiveCompressor(checkpoints_folder, "frontend/static")
        server = SimulateServer(
            name, "frontend/static", checkpoints_folder, sim_config, start_step, "info"
        )
        sim_state["status"] = "running"
        if step <= 0:
            # step <= 0 表示持续运行，直到手动停止
            while True:
                server.simulate(1, stride, on_step=on_step, on_agent=on_agent)
                server.start_step += 1
        else:
            server.simulate(step, stride, on_step=on_step, on_agent=on_agent)
        sim_state["status"] = "done"
        broadcast({"type": "done"})
    except Exception as e:
        sim_state["status"] = "error"
        sim_state["error"] = str(e)
        broadcast({"type": "error", "message": str(e)})


def load_initial_payload(start_datetime, stride):
    """构造页面首次渲染所需的初始数据（所有 Agent 的初始位置与描述）"""
    persona_init_pos = dict()
    description = dict()
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
        "start_datetime": datetime.strptime(
            start_datetime, "%Y%m%d-%H:%M"
        ).isoformat(),
        "stride": stride,
        "sec_per_step": stride,
        "persona_init_pos": persona_init_pos,
        "all_movement": {"description": description, "conversation": {}},
    }


@app.route("/", methods=["GET"])
def index():
    speed = int(request.args.get("speed", 0))
    zoom = float(request.args.get("zoom", 0))  # 0 = 前端按地图尺寸自适应
    if speed < 0:
        speed = 0
    elif speed > 5:
        speed = 5
    play_speed = 2 ** speed

    payload = load_initial_payload(sim_state["start_time"], sim_state["stride"])
    return render_template(
        "index.html",
        persona_names=personas,
        step=1,
        play_speed=play_speed,
        zoom=zoom,
        live_mode=True,
        **payload
    )


@app.route("/stream")
def stream():
    def gen():
        q = queue.Queue()
        with clients_lock:
            clients.append(q)
        try:
            # 立即发送一条初始消息，让响应头立刻返回（确认连接建立）
            yield f"data: {json.dumps({'type': 'init'}, ensure_ascii=False)}\n\n"
            # 再推送当前已生成的数据，供新连接的客户端追赶进度
            if compressor is not None and compressor.started:
                yield f"data: {json.dumps(compressor.snapshot(), ensure_ascii=False)}\n\n"
            while True:
                data = q.get()
                yield f"data: {data}\n\n"
        except GeneratorExit:
            with clients_lock:
                if q in clients:
                    clients.remove(q)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="live simulation server")
    parser.add_argument("--name", type=str, default="", help="The simulation name")
    parser.add_argument("--start", type=str, default="20240213-09:30", help="The starting time of the simulated ville")
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

    print(
        f"Live simulation '{name}' started. "
        f"Open http://127.0.0.1:{args.port}/ in your browser."
    )
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False)
