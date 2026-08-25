"""framework.runtime.compressor — 实时压缩器(逐步生成回放帧/Agent 状态)

从 compress.py 的 LiveCompressor 迁移:改用 framework.scene.maze.Maze,
去掉对 modules / start 的依赖。live 服务每完成一个 step 调用一次 add_agent,
即可得到该 Agent 的状态供 WebSocket 推送;snapshot() 供新连接追赶进度。
"""
import json
import os
from typing import Dict, List, Optional

from mavisframework.scene.maze import Maze

frames_per_step = 60  # 每个 step 包含的帧数(回放模式用)


def get_location(address: List[str]) -> Optional[str]:
    """将 address 转换为字符串(去掉第一级 the Ville)"""
    if not address:
        return None
    location = "，".join(address[1:])
    return location or None


class LiveCompressor:
    """逐步生成回放帧数据(供实时可视化使用)。

    模拟每完成一个 step,调用一次 :meth:`add_agent`(单 Agent 直接驱动)
    或 :meth:`add_step`(整步帧模式),由 live 服务推送。
    """

    def __init__(self, checkpoints_folder, static_root=None):
        # static_root:前端静态资源根;默认环境变量 MAVIS_STATIC_ROOT,否则相对路径 frontend/static
        if static_root is None:
            static_root = os.environ.get(
                "MAVIS_STATIC_ROOT", os.path.join("frontend", "static")
            )
        self.checkpoints_folder = checkpoints_folder
        self.static_root = static_root
        self.conversation_file = os.path.join(checkpoints_folder, "conversation.json")

        # 加载地图数据,用于计算 Agent 移动路径
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
        """插入第0帧数据（Agent的初始状态）"""
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

    def _find_nearby_path(self, source, target):
        """目标 tile 不可达时,按曼哈顿距离递增寻找附近可达的 tile 并寻路。"""
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
        """单个 Agent 思考完成:记录其新位置与动作(直接驱动模式)。

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

    def add_step(self, json_data):
        """处理单个 step 的存档数据,返回该 step 的帧数据(回放模式)。

        返回值：``(frames, conversation, new_description)``
        """
        step = json_data["step"]
        agents = json_data["agents"]

        new_description = {}
        if not self.started:
            for agent_name in agents:
                new_description[agent_name] = self._insert_frame0(agent_name)
            self.started = True
            if len(self.start_datetime) < 1:
                self.start_datetime = json_data["time"]

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
                        step_conversation += f"{c[0]}：{c[1]}\n"

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
        self.all_movement.update(frames)
        return frames, {step_time: step_conversation}, new_description

    def snapshot(self):
        """返回当前所有 Agent 的状态(供新连接的客户端初始化画面)"""
        return {
            "type": "snapshot",
            "agents": self.agent_states,
            "time": self._last_time,
        }
