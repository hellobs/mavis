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

app = Flask(
    __name__,
    template_folder="frontend/templates",
    static_folder="frontend/static",
    static_url_path="/static",
)

# ---- SSE 客户端管理 ----
clients = list()
clients_lock = threading.Lock()


def broadcast(data):
    """向所有已连接的 SSE 客户端推送一条消息"""
    payload = json.dumps(data, ensure_ascii=False)
    with clients_lock:
        for q in clients:
            q.put(payload)


# ---- 全局状态 ----
sim_state = {"status": "idle", "error": "", "start_time": "", "stride": 10}
server = None
compressor = None


def on_step(config):
    """模拟每完成一个 step 时调用：生成该 step 的帧数据并推送给浏览器"""
    global compressor
    if compressor is None:
        return
    frames, conversation, description = compressor.add_step(config)
    msg = {"type": "step", "frames": frames, "conversation": conversation}
    if description:
        msg["description"] = description
    broadcast(msg)


def run_simulation(name, sim_config, start_step, step, stride):
    """在后台线程中运行模拟"""
    global server, compressor
    try:
        checkpoints_folder = f"results/checkpoints/{name}"
        compressor = LiveCompressor(checkpoints_folder, "frontend/static")
        server = SimulateServer(
            name, "frontend/static", checkpoints_folder, sim_config, start_step, "info"
        )
        sim_state["status"] = "running"
        if step <= 0:
            # step <= 0 表示持续运行，直到手动停止
            while True:
                server.simulate(1, stride, on_step=on_step)
                server.start_step += 1
        else:
            server.simulate(step, stride, on_step=on_step)
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
    speed = int(request.args.get("speed", 1))
    zoom = float(request.args.get("zoom", 0.8))
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
    parser.add_argument("--stride", type=int, default=10, help="The step stride in minute")
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
