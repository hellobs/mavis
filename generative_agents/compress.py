import os
import json
import argparse
from datetime import datetime

from modules.maze import Maze
from start import personas

file_markdown = "simulation.md"
file_movement = "movement.json"

frames_per_step = 60  # 每个step包含的帧数


# 从存档文件中读取stride
def get_stride(json_files):
    if len(json_files) < 1:
        return 1

    with open(json_files[-1], "r", encoding="utf-8") as f:
        config = json.load(f)

    return config["stride"]


# 将address转换为字符串
def get_location(address):
    # 仅为兼容原版
    # if address[0] == "<waiting>" or address[0] == "<persona>":
    #     return None

    # 不需要显示address第一级（"the Ville"）
    location = "，".join(address[1:])

    return location


# 插入第0帧数据（Agent的初始状态）
def insert_frame0(init_pos, movement, agent_name):
    key = "0"
    if key not in movement.keys():
        movement[key] = dict()

    json_path = f"frontend/static/assets/village/agents/{agent_name}/agent.json"
    with open(json_path, "r", encoding="utf-8") as f:
        json_data = json.load(f)
        address = json_data["spatial"]["address"]["living_area"]
    location = get_location(address)
    coord = json_data["coord"]
    init_pos[agent_name] = coord
    movement[key][agent_name] = {
        "location": location,
        "movement": coord,
        "description": "正在睡觉",
    }
    movement["description"][agent_name] = {
        "currently": json_data["currently"],
        "scratch": json_data["scratch"],
    }


# 从所有存档文件中提取数据（用于回放）
def generate_movement(checkpoints_folder, compressed_folder, compressed_file):
    movement_file = os.path.join(compressed_folder, compressed_file)

    conversation_file = "conversation.json"
    conversation = {}
    if os.path.exists(os.path.join(checkpoints_folder, conversation_file)):
        with open(os.path.join(checkpoints_folder, conversation_file), "r", encoding="utf-8") as f:
            conversation = json.load(f)

    files = sorted(os.listdir(checkpoints_folder))
    json_files = list()
    for file_name in files:
        if file_name.endswith(".json") and file_name != conversation_file:
            json_files.append(os.path.join(checkpoints_folder, file_name))

    persona_init_pos = dict()
    all_movement = dict()
    all_movement["description"] = dict()
    all_movement["conversation"] = dict()

    stride = get_stride(json_files)
    sec_per_step = stride

    result = {
        "start_datetime": "",  # 起始时间
        "stride": stride,  # 每个step对应的分钟数（必须与生成时的参数一致）
        "sec_per_step": sec_per_step,  # 回放时每一帧对应的秒数
        "persona_init_pos": persona_init_pos,  # 每个Agent的初始位置
        "all_movement": all_movement,  # 所有Agent在每个setp中的位置变化
    }

    last_location = dict()

    # 加载地图数据，用于计算Agent移动路径
    json_path = "frontend/static/assets/village/maze.json"
    with open(json_path, "r", encoding="utf-8") as f:
        json_data = json.load(f)
        maze = Maze(json_data, None)

    for file_name in json_files:
        # 依次读取所有存档文件
        with open(file_name, "r", encoding="utf-8") as f:
            json_data = json.load(f)
            step = json_data["step"]
            agents = json_data["agents"]

            # 保存回放的起始时间
            if len(result["start_datetime"]) < 1:
                t = datetime.strptime(json_data["time"], "%Y%m%d-%H:%M")
                result["start_datetime"] = t.isoformat()

            # 遍历单个存档文件中的所有Agent
            for agent_name, agent_data in agents.items():
                # 插入第0帧
                if step == 1:
                    insert_frame0(persona_init_pos, all_movement, agent_name)

                source_coord = last_location.get(agent_name, all_movement["0"][agent_name])["movement"]
                target_coord = agent_data["coord"]
                location = get_location(agent_data["action"]["event"]["address"])
                if location is None:
                    location = last_location.get(agent_name, all_movement["0"][agent_name])["location"]
                    path = [source_coord]
                else:
                    path = maze.find_path(source_coord, target_coord)

                had_conversation = False
                step_conversation = ""
                persons_in_conversation = []
                step_time = json_data["time"]
                if step_time in conversation.keys():
                    for chats in conversation[step_time]:
                        for persons, chat in chats.items():
                            persons_in_conversation.append(persons.split(" @ ")[0].split(" -> "))
                            step_conversation += f"\n地点：{persons.split(' @ ')[1]}\n\n"
                            for c in chat:
                                agent = c[0]
                                text = c[1]
                                step_conversation += f"{agent}：{text}\n"

                for i in range(frames_per_step):
                    moving = len(path) > 1
                    if len(path) > 0:
                        movement = list(path[0])
                        path = path[1:]
                        if agent_name not in last_location.keys():
                            last_location[agent_name] = dict()
                        last_location[agent_name]["movement"] = movement
                        last_location[agent_name]["location"] = location
                    else:
                        movement = None

                    if moving:
                        action = f"前往 {location}"
                    elif movement is not None:
                        action = agent_data["action"]["event"]["describe"]
                        if len(action) < 1:
                            action = f'{agent_data["action"]["event"]["predicate"]}{agent_data["action"]["event"]["object"]}'

                        # 判断该存档文件中当前Agent是否有新的对话（用于设置图标）
                        for persons in persons_in_conversation:
                            if agent_name in persons:
                                had_conversation = True
                                break

                        # 针对睡觉和对话设置图标
                        if "睡觉" in action:
                            action = "😴 " + action
                        elif had_conversation:
                            action = "💬 " + action

                    step_key = "%d" % ((step-1) * frames_per_step + 1 + i)
                    if step_key not in all_movement.keys():
                        all_movement[step_key] = dict()

                    if movement is not None:
                        all_movement[step_key][agent_name] = {
                            "location": location,
                            "movement": movement,
                            "action": action,
                        }
                all_movement["conversation"][step_time] = step_conversation

    # 保存数据
    with open(movement_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(result, indent=2, ensure_ascii=False))

    return result


# 生成Markdown文档
def generate_report(checkpoints_folder, compressed_folder, compressed_file):
    last_state = dict()

    conversation_file = "conversation.json"
    conversation = {}
    if os.path.exists(os.path.join(checkpoints_folder, conversation_file)):
        with open(os.path.join(checkpoints_folder, conversation_file), "r", encoding="utf-8") as f:
            conversation = json.load(f)

    def extract_description():
        markdown_content = "# 基础人设\n\n"
        for agent_name in personas:
            json_path = f"frontend/static/assets/village/agents/{agent_name}/agent.json"
            with open(json_path, "r", encoding="utf-8") as f:
                json_data = json.load(f)
                markdown_content += f"## {agent_name}\n\n"
                markdown_content += f"年龄：{json_data['scratch']['age']}岁  \n"
                markdown_content += f"先天：{json_data['scratch']['innate']}  \n"
                markdown_content += f"后天：{json_data['scratch']['learned']}  \n"
                markdown_content += f"生活习惯：{json_data['scratch']['lifestyle']}  \n"
                markdown_content += f"当前状态：{json_data['currently']}\n\n"
        return markdown_content

    def extract_action(json_data):
        markdown_content = ""
        agents = json_data["agents"]
        for agent_name, agent_data in agents.items():
            if agent_name not in last_state.keys():
                last_state[agent_name] = {"currently": "", "location": "", "action": ""}

            location = "，".join(agent_data["action"]["event"]["address"])
            action = agent_data["action"]["event"]["describe"]

            if location == last_state[agent_name]["location"] and action == last_state[agent_name]["action"]:
                continue

            last_state[agent_name]["location"] = location
            last_state[agent_name]["action"] = action

            if len(markdown_content) < 1:
                markdown_content = f"# {json_data['time']}\n\n"
                markdown_content += "## 活动记录：\n\n"

            markdown_content += f"### {agent_name}\n"

            if len(action) < 1:
                action = "睡觉"

            markdown_content += f"位置：{location}  \n"
            markdown_content += f"活动：{action}  \n"

            markdown_content += f"\n"

        if json_data['time'] not in conversation.keys():
            return markdown_content

        markdown_content += "## 对话记录：\n\n"
        for chats in conversation[json_data['time']]:
            for agents, chat in chats.items():
                markdown_content += f"### {agents}\n\n"
                for item in chat:
                    markdown_content += f"`{item[0]}`\n> {item[1]}\n\n"
        return markdown_content

    all_markdown_content = extract_description()
    files = sorted(os.listdir(checkpoints_folder))
    for file_name in files:
        if (not file_name.endswith(".json")) or (file_name == conversation_file):
            continue

        file_path = os.path.join(checkpoints_folder, file_name)
        with open(file_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)
            content = extract_action(json_data)
            all_markdown_content += content + "\n\n"
    with open(f"{compressed_folder}/{compressed_file}", "w", encoding="utf-8") as compressed_file:
        compressed_file.write(all_markdown_content)


class LiveCompressor:
    """逐步生成回放帧数据(供实时可视化使用)。

    与 ``generate_movement`` 共用同一套帧生成逻辑，但不需要一次性读取全部存档文件：
    模拟每完成一个 step，调用一次 :meth:`add_step`，即可得到该 step 对应的 60 帧数据，
    由 live.py 通过 SSE 推送给前端。
    """

    def __init__(self, checkpoints_folder, static_root="frontend/static"):
        self.checkpoints_folder = checkpoints_folder
        self.static_root = static_root
        self.conversation_file = os.path.join(checkpoints_folder, "conversation.json")

        # 加载地图数据，用于计算Agent移动路径
        maze_path = os.path.join(static_root, "assets/village/maze.json")
        with open(maze_path, "r", encoding="utf-8") as f:
            self.maze = Maze(json.load(f), None)

        self.persona_init_pos = dict()
        self.all_movement = dict()
        self.all_movement["description"] = dict()
        self.all_movement["conversation"] = dict()
        self.last_location = dict()
        self.agent_states = dict()   # 直接驱动:每个 Agent 的最新状态
        self._last_time = ""         # 最新模拟时间
        self.started = False
        self.start_datetime = ""

    def _insert_frame0(self, agent_name):
        """插入第0帧数据（Agent的初始状态），与 ``insert_frame0`` 逻辑一致"""
        key = "0"
        if key not in self.all_movement.keys():
            self.all_movement[key] = dict()

        json_path = os.path.join(
            self.static_root, f"assets/village/agents/{agent_name}/agent.json"
        )
        with open(json_path, "r", encoding="utf-8") as f:
            json_data = json.load(f)
            address = json_data["spatial"]["address"]["living_area"]
        location = get_location(address)
        coord = json_data["coord"]
        self.persona_init_pos[agent_name] = coord
        self.all_movement[key][agent_name] = {
            "location": location,
            "movement": coord,
            "description": "正在睡觉",
        }
        self.all_movement["description"][agent_name] = {
            "currently": json_data["currently"],
            "scratch": json_data["scratch"],
        }
        return self.all_movement["description"][agent_name]

    def add_step(self, json_data):
        """处理单个 step 的存档数据，返回该 step 的帧数据。

        返回值：``(frames, conversation)``
        - ``frames``: {帧号: {agent: {location, movement, action}}}，帧号从 1 开始计数；
        - ``conversation``: {step_time: 对话文本}，供前端按时间显示对话内容。
        """
        step = json_data["step"]
        agents = json_data["agents"]

        # 首次调用时插入第0帧
        new_description = {}
        if not self.started:
            for agent_name in agents:
                new_description[agent_name] = self._insert_frame0(agent_name)
            self.started = True
            if len(self.start_datetime) < 1:
                self.start_datetime = json_data["time"]

        # 读取该 step 的对话数据（模拟线程每步都会重写 conversation.json）
        conversation = {}
        if os.path.exists(self.conversation_file):
            with open(self.conversation_file, "r", encoding="utf-8") as f:
                conversation = json.load(f)

        step_time = json_data["time"]
        step_conversation = ""
        persons_in_conversation = []
        if step_time in conversation.keys():
            for chats in conversation[step_time]:
                for persons, chat in chats.items():
                    persons_in_conversation.append(
                        persons.split(" @ ")[0].split(" -> ")
                    )
                    step_conversation += f"\n地点：{persons.split(' @ ')[1]}\n\n"
                    for c in chat:
                        agent = c[0]
                        text = c[1]
                        step_conversation += f"{agent}：{text}\n"

        frames = dict()
        for agent_name, agent_data in agents.items():
            source_coord = self.last_location.get(
                agent_name, self.all_movement["0"][agent_name]
            )["movement"]
            target_coord = agent_data["coord"]
            location = get_location(agent_data["action"]["event"]["address"])
            if location is None:
                location = self.last_location.get(
                    agent_name, self.all_movement["0"][agent_name]
                )["location"]
                path = [source_coord]
            else:
                path = self.maze.find_path(source_coord, target_coord)

            # 判断该存档文件中当前Agent是否有新的对话（用于设置图标）
            had_conversation = False
            for persons in persons_in_conversation:
                if agent_name in persons:
                    had_conversation = True
                    break

            for i in range(frames_per_step):
                moving = len(path) > 1
                if len(path) > 0:
                    movement = list(path[0])
                    path = path[1:]
                    if agent_name not in self.last_location.keys():
                        self.last_location[agent_name] = dict()
                    self.last_location[agent_name]["movement"] = movement
                    self.last_location[agent_name]["location"] = location
                else:
                    movement = None

                if moving:
                    action = f"前往 {location}"
                elif movement is not None:
                    action = agent_data["action"]["event"]["describe"]
                    if len(action) < 1:
                        action = f'{agent_data["action"]["event"]["predicate"]}{agent_data["action"]["event"]["object"]}'

                    # 针对睡觉和对话设置图标
                    if "睡觉" in action:
                        action = "😴 " + action
                    elif had_conversation:
                        action = "💬 " + action

                step_key = "%d" % ((step - 1) * frames_per_step + 1 + i)
                if step_key not in frames.keys():
                    frames[step_key] = dict()

                if movement is not None:
                    frames[step_key][agent_name] = {
                        "location": location,
                        "movement": movement,
                        "action": action,
                    }

        self.all_movement["conversation"][step_time] = step_conversation
        # 累积到 all_movement，供 snapshot()（新连接客户端追赶进度）使用
        self.all_movement.update(frames)
        return frames, {step_time: step_conversation}, new_description

    def _find_nearby_path(self, source, target):
        """目标 tile 不可达时,按曼哈顿距离递增寻找附近可达的 tile 并寻路。

        返回: 到达附近可达点的完整路径;都不可达时返回 None(调用方原地不动)
        """
        for r in range(1, 6):
            candidates = []
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if abs(dx) + abs(dy) != r:
                        continue
                    cx, cy = target[0] + dx, target[1] + dy
                    if 0 <= cx < self.maze.maze_width and 0 <= cy < self.maze.maze_height:
                        if not self.maze.tile_at([cx, cy]).collision:
                            candidates.append([cx, cy])
            for c in candidates:
                p = self.maze.find_path(source, c)
                if p:
                    return p
        return None

    def add_agent(self, agent_name, agent_data, step, step_time):
        """单个 Agent 思考完成:记录其新位置与动作。

        直接驱动模式:不再生成 60 帧插值动画,只返回该 Agent 的最终状态,
        由前端用补间动画平滑移动。

        返回值：(agent_state, conversation, new_description)
        - agent_state: {name, coord, action, location}
        - conversation: {step_time: 对话文本}
        """
        # 首次调用时插入第0帧(提供初始位置与描述)
        new_description = {}
        if not self.started:
            new_description[agent_name] = self._insert_frame0(agent_name)
            self.started = True
            if len(self.start_datetime) < 1:
                self.start_datetime = step_time
        elif agent_name not in self.all_movement["description"]:
            new_description[agent_name] = self._insert_frame0(agent_name)

        coord = agent_data["coord"]
        source_coord = self.last_location.get(
            agent_name, self.all_movement["0"][agent_name]
        )["movement"]
        location = get_location(agent_data["action"]["event"]["address"])
        if location is None:
            location = self.last_location.get(
                agent_name, {"location": ""}
            )["location"]
            path = [source_coord]
        else:
            # 沿寻路路径移动(前端按路径点逐格平滑移动,不穿墙)
            path = self.maze.find_path(source_coord, coord)
            if not path:
                # 目标不可达:找目标附近的可达 tile 寻路过去(绝不直线穿墙)
                path = self._find_nearby_path(source_coord, coord)
            if not path:
                path = [source_coord]  # 实在不可达:原地不动

        # 实际可达终点(目标不可达时,agent 停在附近可达点)
        actual_target = path[-1] if path else coord

        # 记录位置(供下次推送与快照)
        self.last_location[agent_name] = {
            "movement": actual_target, "location": location
        }
        self.agent_states[agent_name] = {
            "name": agent_name,
            "coord": actual_target,
            "location": location,
            "action": agent_data["action"]["event"].get("describe", ""),
            "path": path,
        }
        self._last_time = step_time

        # 当前该 step 的对话快照
        conversation = {}
        if os.path.exists(self.conversation_file):
            with open(self.conversation_file, "r", encoding="utf-8") as f:
                conversation = json.load(f)
        step_conversation = ""
        if step_time in conversation.keys():
            for chats in conversation[step_time]:
                for persons, chat in chats.items():
                    step_conversation += f"\n地点：{persons.split(' @ ')[1]}\n\n"
                    for c in chat:
                        step_conversation += f"{c[0]}：{c[1]}\n"

        return (
            self.agent_states[agent_name],
            {step_time: step_conversation},
            new_description,
        )

    def snapshot(self):
        """返回当前所有 Agent 的状态(供新连接的客户端初始化画面)"""
        return {
            "type": "snapshot",
            "agents": self.agent_states,
            "time": self._last_time,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", type=str, default="", help="the name of the simulation")
    args = parser.parse_args()

    name = args.name
    if len(name) < 1:
        name = input("Please enter a simulation name: ")

    while not os.path.exists(f"results/checkpoints/{name}"):
        name = input(f"'{name}' doesn't exists, please re-enter the simulation name: ")

    checkpoints_folder = f"results/checkpoints/{name}"
    compressed_folder = f"results/compressed/{name}"
    os.makedirs(compressed_folder, exist_ok=True)

    generate_report(checkpoints_folder, compressed_folder, file_markdown)
    generate_movement(checkpoints_folder, compressed_folder, file_movement)

    print("Compression completed.")
