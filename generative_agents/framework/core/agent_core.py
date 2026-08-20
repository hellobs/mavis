"""framework.core.agent_core — Agent 完整生命周期(组件注入式,纯逻辑)

从旧实现(modules/agent.py)迁移完整实现:依赖全部注入(timer/llm/记忆/空间/提示词),
不 import modules。Agent 每步(think)编排:
    移动 → 取计划 → (睡/醒) → 感知 → 反应(对话/等待) → 反思 → 输出路径

可插拔组件:
- llm     : framework.runtime.llm.LLMProvider(completion/is_available/get_summary)
- memory  : framework.core.associate.Associate(联想记忆)
- maze    : framework.scene.maze.Maze(空间/寻路)
- prompts : framework.prompt.Scratch(prompt_xxx 方法族)
- timer   : framework.core.timer.Timer(模拟时钟)
"""
import datetime
import math
import os
import random
import threading
from typing import Any, Dict, List, Optional, Tuple

from framework.core.action import Action
from framework.core.associate import Associate, Concept
from framework.core.event import Event
from framework.core.schedule import Schedule
from framework.core.spatial import Spatial
from framework.core.timer import Timer, daily_duration, to_date

# 对话互斥锁:并行思考时,同一时刻只允许一场对话进行
chat_lock = threading.Lock()

# 对话逐句回调:由外部(如 live 服务)设置,每生成一句话实时推送
chat_callback = None


class Agent:
    """框架版 Agent(与旧 modules.agent.Agent 行为对齐,依赖注入)"""

    def __init__(
        self,
        config: dict,
        maze,
        conversation: dict,
        timer: Optional[Timer] = None,
        llm=None,
        logger=None,
    ):
        self.name = config["name"]
        self.maze = maze
        self.conversation = conversation
        self._llm = llm
        self._timer = timer or Timer()
        if logger is None:
            from framework.runtime.logger import get_logger

            logger = get_logger(f"agent.{self.name}", level="info")
        self.logger = logger

        # agent config
        self.percept_config = config["percept"]
        self.think_config = config["think"]
        self.chat_iter = config["chat_iter"]

        # memory
        self.spatial = Spatial(**config["spatial"])
        self.schedule = Schedule(**config["schedule"])
        self.associate = Associate(
            os.path.join(config["storage_root"], "associate"),
            config["associate"].get("embedding", {}),
            timer=self._timer,
            **{k: v for k, v in config["associate"].items() if k != "embedding"},
        )
        self.concepts, self.chats = [], config.get("chats", [])

        # prompt
        from framework.prompt import Scratch

        self.scratch = Scratch(
            self.name, config["currently"], config["scratch"], timer=self._timer
        )

        # status
        status = {"poignancy": 0}
        self.status = self._update_dict(status, config.get("status", {}))
        self.plan = config.get("plan", {})

        # record
        self.last_record = self._timer.daily_duration()

        # action and events
        if "action" in config:
            self.action = Action.from_dict(config["action"])
            tiles = self.maze.get_address_tiles(self.get_event().address)
            config["coord"] = random.choice(list(tiles))
        else:
            tile = self.maze.tile_at(config["coord"])
            address = tile.get_address("game_object", as_list=True)
            self.action = Action(
                Event(self.name, address=address),
                Event(address[-1], address=address),
            )

        # update maze
        self.coord, self.path = None, None
        self.move(config["coord"], config.get("path"))
        if self.coord is None:
            self.coord = config["coord"]

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _update_dict(base: dict, extra: dict) -> dict:
        base = {**base}
        for k, v in (extra or {}).items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = {**base[k], **v}
            else:
                base[k] = v
        return base

    def abstract(self):
        des = {
            "name": self.name,
            "currently": self.scratch.currently,
            "tile": self.maze.tile_at(self.coord).abstract(),
            "status": self.status,
            "concepts": {c.node_id: c.abstract() for c in self.concepts},
            "chats": self.chats,
            "action": self.action.abstract(self._timer.get_date()),
            "associate": self.associate.abstract(),
        }
        if self.schedule.scheduled(self._timer):
            des["schedule"] = self.schedule.abstract(self._timer)
        if self.llm_available():
            des["llm"] = self._llm.get_summary()
        return des

    def __str__(self):
        import json

        return json.dumps(self.abstract(), ensure_ascii=False, indent=2)

    def reset(self):
        if not self._llm:
            from framework.runtime.llm import create_llm_provider

            self._llm = create_llm_provider(self.think_config["llm"])

    def completion(self, func_hint, *args, **kwargs):
        assert hasattr(
            self.scratch, "prompt_" + func_hint
        ), "Can not find func prompt_{} from scratch".format(func_hint)
        func = getattr(self.scratch, "prompt_" + func_hint)
        res = func(*args, **kwargs)._asdict()
        title, msg = "{}.{}".format(self.name, func_hint), {}
        if self.llm_available():
            self.logger.info("{} -> {}".format(self.name, func_hint))
            output = self._llm.completion(**res)
            msg = {"<PROMPT>": "\n" + res["prompt"] + "\n"}
            msg.update({"response": output})
        self.logger.debug(_block_msg(title, msg))
        return output

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def think(self, status, agents):
        events = self.move(status["coord"], status.get("path"))
        plan, _ = self.make_schedule()

        if (plan["describe"] == "sleeping" or "睡" in plan["describe"]) and self.is_awake():
            self.logger.info("{} is going to sleep...".format(self.name))
            address = self.spatial.find_address("睡觉", as_list=True)
            tiles = self.maze.get_address_tiles(address)
            coord = random.choice(list(tiles))
            events = self.move(coord)
            self.action = Action(
                Event(self.name, "正在", "睡觉", address=address, emoji="😴"),
                Event(
                    address[-1],
                    "被占用",
                    self.name,
                    address=address,
                    emoji="🛌",
                ),
                duration=plan["duration"],
                start=self._timer.daily_time(plan["start"]),
            )
        if self.is_awake():
            self.percept()
            self.make_plan(agents)
            self.reflect()
        else:
            if self.action.finished(self._timer.get_date()):
                self.action = self._determine_action()

        emojis = {}
        if self.action:
            emojis[self.name] = {"emoji": self.get_event().emoji, "coord": self.coord}
        for eve, coord in events.items():
            if eve.subject in agents:
                continue
            emojis[":".join(eve.address)] = {"emoji": eve.emoji, "coord": coord}
        self.plan = {
            "name": self.name,
            "path": self.find_path(agents),
            "emojis": emojis,
        }
        return self.plan

    def move(self, coord, path=None):
        events = {}

        def _update_tile(coord):
            tile = self.maze.tile_at(coord)
            if not self.action:
                return {}
            if not tile.update_events(self.get_event()):
                tile.add_event(self.get_event())
            obj_event = self.get_event(False)
            if obj_event:
                self.maze.update_obj(coord, obj_event)
            return {e: coord for e in tile.get_events()}

        if self.coord and self.coord != coord:
            tile = self.get_tile()
            tile.remove_events(subject=self.name)
            if tile.has_address("game_object"):
                addr = tile.get_address("game_object")
                self.maze.update_obj(
                    self.coord, Event(addr[-1], address=addr)
                )
            events.update({e: self.coord for e in tile.get_events()})
        if not path:
            events.update(_update_tile(coord))
        self.coord = coord
        self.path = path or []

        return events

    def make_schedule(self):
        if not self.schedule.scheduled(self._timer):
            self.logger.info("{} is making schedule...".format(self.name))
            # update currently
            if self.associate.index.nodes_num > 0:
                self.associate.cleanup_index()
                focus = [
                    f"{self.name} 在 {self._timer.daily_format_cn()} 的计划。",
                    f"在 {self.name} 的生活中，重要的近期事件。",
                ]
                retrieved = self.associate.retrieve_focus(focus)
                self.logger.info(
                    "{} retrieved {} concepts".format(self.name, len(retrieved))
                )
                if retrieved:
                    plan = self.completion("retrieve_plan", retrieved)
                    thought = self.completion("retrieve_thought", retrieved)
                    self.scratch.currently = self.completion(
                        "retrieve_currently", plan, thought
                    )
            # make init schedule
            self.schedule.create = self._timer.get_date()
            wake_up = self.completion("wake_up")
            init_schedule = self.completion("schedule_init", wake_up)
            # make daily schedule
            hours = [f"{i}:00" for i in range(24)]
            seed = [(h, "睡觉") for h in hours[:wake_up]]
            seed += [(h, "") for h in hours[wake_up:]]
            schedule = {}
            for _ in range(self.schedule.max_try):
                schedule = {h: s for h, s in seed[:wake_up]}
                schedule.update(
                    self.completion("schedule_daily", wake_up, init_schedule)
                )
                if len(set(schedule.values())) >= self.schedule.diversity:
                    break

            def _to_duration(date_str):
                return daily_duration(to_date(date_str, "%H:%M"))

            schedule = {_to_duration(k): v for k, v in schedule.items()}
            starts = list(sorted(schedule.keys()))
            for idx, start in enumerate(starts):
                end = starts[idx + 1] if idx + 1 < len(starts) else 24 * 60
                self.schedule.add_plan(schedule[start], end - start)
            schedule_time = self._timer.time_format_cn(self.schedule.create)
            thought = "这是 {} 在 {} 的计划：{}".format(
                self.name, schedule_time, "；".join(init_schedule)
            )
            event = Event(
                self.name,
                "计划",
                schedule_time,
                describe=thought,
                address=self.get_tile().get_address(),
            )
            self._add_concept(
                "thought",
                event,
                expire=self.schedule.create + datetime.timedelta(days=30),
            )
        # decompose current plan
        plan, _ = self.schedule.current_plan(self._timer)
        if self.schedule.decompose(plan):
            decompose_schedule = self.completion(
                "schedule_decompose", plan, self.schedule
            )
            decompose, start = [], plan["start"]
            for describe, duration in decompose_schedule:
                decompose.append(
                    {
                        "idx": len(decompose),
                        "describe": describe,
                        "start": start,
                        "duration": duration,
                    }
                )
                start += duration
            plan["decompose"] = decompose
        return self.schedule.current_plan(self._timer)

    def revise_schedule(self, event, start, duration):
        self.action = Action(event, start=start, duration=duration)
        plan, _ = self.schedule.current_plan(self._timer)
        if len(plan["decompose"]) > 0:
            plan["decompose"] = self.completion(
                "schedule_revise", self.action, self.schedule
            )

    def percept(self):
        scope = self.maze.get_scope(self.coord, self.percept_config)
        # add spatial memory
        for tile in scope:
            if tile.has_address("game_object"):
                self.spatial.add_leaf(tile.address)
        events, arena = {}, self.get_tile().get_address("arena")
        # gather events in scope
        for tile in scope:
            if not tile.events or tile.get_address("arena") != arena:
                continue
            dist = math.dist(tile.coord, self.coord)
            for event in tile.get_events():
                if dist < events.get(event, float("inf")):
                    events[event] = dist
        events = list(sorted(events.keys(), key=lambda k: events[k]))
        # get concepts
        self.concepts, valid_num = [], 0
        for idx, event in enumerate(events[: self.percept_config["att_bandwidth"]]):
            recent_nodes = (
                self.associate.retrieve_events() + self.associate.retrieve_chats()
            )
            recent_nodes = set(n.describe for n in recent_nodes)
            if event.get_describe() not in recent_nodes:
                if event.object == "idle" or event.object == "空闲":
                    node = Concept.from_event(
                        "idle_" + str(idx), "event", event, poignancy=1, timer=self._timer
                    )
                else:
                    valid_num += 1
                    node_type = "chat" if event.fit(self.name, "对话") else "event"
                    node = self._add_concept(node_type, event)
                    self.status["poignancy"] += node.poignancy
                self.concepts.append(node)
        self.concepts = [c for c in self.concepts if c.event.subject != self.name]
        self.logger.info(
            "{} percept {}/{} concepts".format(self.name, valid_num, len(self.concepts))
        )

    def make_plan(self, agents):
        if self._reaction(agents):
            return
        if self.path:
            return
        if self.action.finished(self._timer.get_date()):
            self.action = self._determine_action()

    # create action && object events
    def make_event(self, subject, describe, address):
        e_describe = describe.replace("(", "").replace(")", "").replace("<", "").replace(">", "")
        if e_describe.startswith(subject + "此时"):
            e_describe = e_describe[len(subject + "此时"):]
        if e_describe.startswith(subject):
            e_describe = e_describe[len(subject):]
        event = Event(
            subject, "此时", e_describe, describe=describe, address=address
        )
        return event

    def reflect(self):
        def _add_thought(thought, evidence=None):
            event = self.make_event(self.name, thought, self.get_tile().get_address())
            return self._add_concept("thought", event, filling=evidence)

        if self.status["poignancy"] < self.think_config["poignancy_max"]:
            return
        nodes = self.associate.retrieve_events() + self.associate.retrieve_thoughts()
        if not nodes:
            return
        self.logger.info(
            "{} reflect(P{}/{}) with {} concepts...".format(
                self.name,
                self.status["poignancy"],
                self.think_config["poignancy_max"],
                len(nodes),
            )
        )
        nodes = sorted(nodes, key=lambda n: n.access, reverse=True)[
            : self.associate.max_importance
        ]
        # summary thought
        focus = self.completion("reflect_focus", nodes, 3)
        retrieved = self.associate.retrieve_focus(focus, reduce_all=False)
        for r_nodes in retrieved.values():
            thoughts = self.completion("reflect_insights", r_nodes, 5)
            for thought, evidence in thoughts:
                _add_thought(thought, evidence)
        # summary chats
        if self.chats:
            recorded, evidence = set(), []
            for name, _ in self.chats:
                if name == self.name or name in recorded:
                    continue
                res = self.associate.retrieve_chats(name)
                if res and len(res) > 0:
                    node = res[-1]
                    evidence.append(node.node_id)
            thought = self.completion("reflect_chat_planing", self.chats)
            _add_thought(f"对于 {self.name} 的计划：{thought}", evidence)
            thought = self.completion("reflect_chat_memory", self.chats)
            _add_thought(f"{self.name} {thought}", evidence)
        self.status["poignancy"] = 0
        self.chats = []

    def find_path(self, agents):
        address = self.get_event().address
        if self.path:
            return self.path
        if address == self.get_tile().get_address():
            return []
        if address[0] == "<waiting>":
            return []
        if address[0] == "<persona>":
            target_tiles = self.maze.get_around(agents[address[1]].coord)
        else:
            target_tiles = self.maze.get_address_tiles(address)
        if tuple(self.coord) in target_tiles:
            return []

        # filter tile with self event
        def _ignore_target(t_coord):
            if list(t_coord) == list(self.coord):
                return True
            events = self.maze.tile_at(t_coord).get_events()
            if any(e.subject in agents for e in events):
                return True
            return False

        target_tiles = [t for t in target_tiles if not _ignore_target(t)]
        if not target_tiles:
            return []
        if len(target_tiles) >= 4:
            target_tiles = random.sample(target_tiles, 4)
        pathes = {t: self.maze.find_path(self.coord, t) for t in target_tiles}
        target = min(pathes, key=lambda p: len(pathes[p]))
        return pathes[target][1:]

    def _determine_action(self):
        self.logger.info("{} is determining action...".format(self.name))
        plan, de_plan = self.schedule.current_plan(self._timer)
        describes = [plan["describe"], de_plan["describe"]]
        address = self.spatial.find_address(describes[0], as_list=True)
        if not address:
            tile = self.get_tile()
            kwargs = {
                "describes": describes,
                "spatial": self.spatial,
                "address": tile.get_address("world", as_list=True),
            }
            kwargs["address"].append(
                self.completion("determine_sector", **kwargs, tile=tile)
            )
            arenas = self.spatial.get_leaves(kwargs["address"])
            if len(arenas) == 1:
                kwargs["address"].append(arenas[0])
            else:
                kwargs["address"].append(self.completion("determine_arena", **kwargs))
            objs = self.spatial.get_leaves(kwargs["address"])
            if len(objs) == 1:
                kwargs["address"].append(objs[0])
            elif len(objs) > 1:
                kwargs["address"].append(self.completion("determine_object", **kwargs))
            address = kwargs["address"]

        event = self.make_event(self.name, describes[-1], address)
        obj_describe = self.completion("describe_object", address[-1], describes[-1])
        obj_event = self.make_event(address[-1], obj_describe, address)

        event.emoji = f"{de_plan['describe']}"

        return Action(
            event,
            obj_event,
            duration=de_plan["duration"],
            start=self._timer.daily_time(de_plan["start"]),
        )

    def _reaction(self, agents=None, ignore_words=None):
        focus = None
        ignore_words = ignore_words or ["空闲"]

        def _focus(concept):
            return concept.event.subject in agents

        def _ignore(concept):
            return any(i in concept.describe for i in ignore_words)

        if agents:
            priority = [i for i in self.concepts if _focus(i)]
            if priority:
                focus = random.choice(priority)
        if not focus:
            priority = [i for i in self.concepts if not _ignore(i)]
            if priority:
                focus = random.choice(priority)
        if not focus or focus.event.subject not in agents:
            return
        other, focus = agents[focus.event.subject], self.associate.get_relation(focus)

        if self._chat_with(other, focus):
            return True
        if self._wait_other(other, focus):
            return True
        return False

    def _skip_react(self, other):
        def _skip(event):
            if not event.address or "sleeping" in event.get_describe(False) or "睡觉" in event.get_describe(False):
                return True
            if event.predicate == "待开始":
                return True
            return False

        if self._timer.daily_duration(mode="hour") >= 23:
            return True
        if _skip(self.get_event()) or _skip(other.get_event()):
            return True
        return False

    def _chat_with(self, other, focus):
        # 对话互斥:同一时刻只允许一场对话
        if not chat_lock.acquire(blocking=False):
            return False
        try:
            return self._chat_with_locked(other, focus)
        finally:
            chat_lock.release()

    def _chat_with_locked(self, other, focus):
        if len(self.schedule.daily_schedule) < 1 or len(other.schedule.daily_schedule) < 1:
            # initializing
            return False
        if self._skip_react(other):
            return False
        if other.path:
            return False
        if self.get_event().fit(predicate="对话") or other.get_event().fit(predicate="对话"):
            return False

        chats = self.associate.retrieve_chats(other.name)
        if chats:
            delta = self._timer.get_delta(chats[0].create)
            self.logger.info(
                "retrieved chat between {} and {}({} min):\n{}".format(
                    self.name, other.name, delta, chats[0]
                )
            )
            if delta < 20:
                return False

        if not self.completion("decide_chat", self, other, focus, chats):
            # 提高对话频率:即使 LLM 不倾向,也有一半概率继续尝试
            if random.random() < 0.5:
                return False

        self.logger.info("{} decides chat with {}".format(self.name, other.name))
        start, chats = self._timer.get_date(), []
        relations = [
            self.completion("summarize_relation", self, other.name),
            other.completion("summarize_relation", other, self.name),
        ]

        for i in range(self.chat_iter):
            text = self.completion(
                "generate_chat", self, other, relations[0], chats
            )
            # 逐句实时推送(不用等整段对话完成)
            if chat_callback:
                chat_callback(self.name, text)

            if i > 0:
                # 对于发起对话的Agent，从第2轮对话开始，检查是否出现"复读"现象
                end = self.completion(
                    "generate_chat_check_repeat", self, chats, text
                )
                if end:
                    break

                # 对于发起对话的Agent，从第2轮对话开始，检查话题是否结束
                chats.append((self.name, text))
                end = self.completion(
                    "decide_chat_terminate", self, other, chats
                )
                if end:
                    break
            else:
                chats.append((self.name, text))

            text = other.completion(
                "generate_chat", other, self, relations[1], chats
            )
            # 逐句实时推送
            if chat_callback:
                chat_callback(other.name, text)
            if i > 0:
                # 对于响应对话的Agent，从第2轮开始，检查是否出现"复读"现象
                end = self.completion(
                    "generate_chat_check_repeat", other, chats, text
                )
                if end:
                    break

            chats.append((other.name, text))

            # 对于响应对话的Agent，从第1轮开始，检查话题是否结束
            end = other.completion(
                "decide_chat_terminate", other, self, chats
            )
            if end:
                break

        key = self._timer.get_date("%Y%m%d-%H:%M")
        if key not in self.conversation.keys():
            self.conversation[key] = []
        self.conversation[key].append({f"{self.name} -> {other.name} @ {'，'.join(self.get_event().address)}": chats})

        self.logger.info(
            "{} and {} has chats\n  {}".format(
                self.name,
                other.name,
                "\n  ".join(["{}: {}".format(n, c) for n, c in chats]),
            )
        )
        chat_summary = self.completion("summarize_chats", chats)
        duration = int(sum([len(c[1]) for c in chats]) / 240)
        self.schedule_chat(
            chats, chat_summary, start, duration, other
        )
        other.schedule_chat(chats, chat_summary, start, duration, self)
        return True

    def _wait_other(self, other, focus):
        if self._skip_react(other):
            return False
        if not self.path:
            return False
        if self.get_event().address != other.get_tile().get_address():
            return False
        if not self.completion("decide_wait", self, other, focus):
            return False
        self.logger.info("{} decides wait to {}".format(self.name, other.name))
        start = self._timer.get_date()
        t = other.action.end - start
        duration = int(t.total_seconds() / 60)
        event = Event(
            self.name,
            "waiting to start",
            self.get_event().get_describe(False),
            address=self.get_event().address,
            emoji="⌛",
        )
        self.revise_schedule(event, start, duration)

    def schedule_chat(self, chats, chats_summary, start, duration, other, address=None):
        self.chats.extend(chats)
        event = Event(
            self.name,
            "对话",
            other.name,
            describe=chats_summary,
            address=address or self.get_tile().get_address(),
            emoji="💬",
        )
        self.revise_schedule(event, start, duration)

    def _add_concept(
        self,
        e_type,
        event,
        create=None,
        expire=None,
        filling=None,
    ):
        if event.fit(None, "is", "idle"):
            poignancy = 1
        elif event.fit(None, "此时", "空闲"):
            poignancy = 1
        elif e_type == "chat":
            poignancy = self.completion("poignancy_chat", event)
        else:
            poignancy = self.completion("poignancy_event", event)
        self.logger.debug("{} add associate {}".format(self.name, event))
        return self.associate.add_node(
            e_type,
            event,
            poignancy,
            create=create,
            expire=expire,
            filling=filling,
        )

    def get_tile(self):
        return self.maze.tile_at(self.coord)

    def get_event(self, as_act=True):
        return self.action.event if as_act else self.action.obj_event

    def is_awake(self):
        if not self.action:
            return True
        if self.get_event().fit(self.name, "is", "sleeping"):
            return False
        if self.get_event().fit(self.name, "正在", "睡觉"):
            return False
        return True

    def llm_available(self):
        if not self._llm:
            return False
        return self._llm.is_available()

    def to_dict(self, with_action=True):
        info = {
            "status": self.status,
            "schedule": self.schedule.to_dict(),
            "associate": self.associate.to_dict(),
            "chats": self.chats,
            "currently": self.scratch.currently,
        }
        if with_action:
            info.update({"action": self.action.to_dict()})
        return info


def _block_msg(title: str, msg: dict) -> str:
    lines = [title]
    for k, v in msg.items():
        lines.append("{}: {}".format(k, v))
    return "\n".join(lines)
