"""framework.runtime.simulator — 模拟调度(并行 + 回调 + 存档,与前端解耦)

从 start.py SimulateServer 迁移:并行执行多个 Agent 的 think,
通过回调钩子(on_agent/on_step/on_chat_line)对外通知,
checkpoint 存档、conversation 存档、决策导出(decisions)在此统一接线。
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

from mavisframework.core.timer import Timer


class Simulator:
    """模拟调度器:持有 Agent 集合,驱动 think 循环,发出协议消息"""

    def __init__(
        self,
        on_agent: Optional[Callable] = None,      # 单 agent 完成: (name, state, step, sim_time)
        on_step: Optional[Callable] = None,       # 整步完成: (config)
        on_chat_line: Optional[Callable] = None,  # 对话逐句: (speaker, text)
        on_story: Optional[Callable] = None,      # 剧情注入: (event_dict)
        max_workers: int = 5,                     # 并行思考线程数
        llm_concurrency: int = 0,                 # LLM 并发上限(0 = 自动 = max_workers)
        export_decisions: bool = False,           # 每步结束后导出决策流(experts 平台)
        decisions_path: str = "",
        roles: Optional[Dict[str, str]] = None,   # 角色名 -> 职位(决策导出用)
        story: Optional[List[dict]] = None,       # 剧情事件( story.json 注入)
        stride: int = 2,
    ):
        self.on_agent = on_agent
        self.on_step = on_step
        self.on_chat_line = on_chat_line
        self.on_story = on_story
        self.max_workers = max_workers
        # LLM 并发上限:显式指定优先,否则取 max_workers(Ollama 单实例并发有限,过多线程只排队)
        self.llm_concurrency = llm_concurrency or max_workers
        self.export_decisions = export_decisions
        self.decisions_path = decisions_path
        self.roles = roles or {}
        self.story = story or []
        self.stride = stride

    # ------------------------------------------------------------------
    # 条件触发检查器注册表:condition.type -> 检查函数(game, ev) -> bool
    # 注册新条件 = 在此添加一个函数,不动主逻辑
    CONDITION_CHECKERS = {}

    @classmethod
    def register_condition(cls, cond_type):
        """装饰器:注册一个条件触发检查器
        用法:
            @Simulator.register_condition("poignancy")
            def check_poignancy(game, ev): ...
        """

        def deco(fn):
            cls.CONDITION_CHECKERS[cond_type] = fn
            return fn

        return deco

    def _trigger_fired(self, ev, game) -> bool:
        """判断 story 事件是否满足触发条件
        - 有 condition 字段:交给对应检查器(注册表)
        - 无 condition:按 time(HH:MM) 匹配
        """
        condition = ev.get("condition")
        if condition:
            cond_type = condition.get("type")
            checker = self.CONDITION_CHECKERS.get(cond_type)
            if checker is None:
                game.logger.warning(
                    "未知条件类型 '{}' for story {},按时间触发兜底".format(cond_type, ev.get("id"))
                )
                return ev.get("time") == game._timer.get_date("%H:%M")
            try:
                return bool(checker(game, ev))
            except Exception as e:
                game.logger.warning("condition check failed: {}".format(e))
                return False
        return ev.get("time") == game._timer.get_date("%H:%M")

    def _inject_story(self, game, config):
        """剧情调度:检查 story 事件触发条件,命中则向目标角色注入

        触发规则:
        - 无 condition:事件 time("HH:MM") 与当前模拟时间匹配
        - 有 condition:由注册的检查器判定(见 CONDITION_CHECKERS)
        每个事件只触发一次(用已触发 id 集合去重)。
        """
        if not self.story:
            return
        if not hasattr(self, "_story_fired"):
            self._story_fired = set()
        now_hm = game._timer.get_date("%H:%M")
        for ev in self.story:
            if ev.get("id") in self._story_fired:
                continue
            if not self._trigger_fired(ev, game):
                continue
            self._story_fired.add(ev.get("id"))
            targets = ev.get("targets", []) or ["all"]
            for name, agent in game.agents.items():
                if "all" not in targets and name not in targets:
                    continue
                agent.inject_story_event(ev)
            game.logger.info(
                "STORY {} @ {}: {}".format(ev.get("id"), now_hm, ev.get("content", "")[:50])
            )
            # 剧情注入事件广播(前端开发者控制台可见)
            if self.on_story:
                try:
                    self.on_story({
                        "type": "story",
                        "id": ev.get("id"),
                        "time": now_hm,
                        "event_type": ev.get("event_type", ""),
                        "content": ev.get("content", ""),
                        "targets": targets,
                    })
                except Exception as e:
                    game.logger.warning("story broadcast failed: {}".format(e))

    def simulate(self, game, config, step, stride=0, start_step=0, checkpoints_folder="", on_step=None, on_agent=None):
        """连续模拟多步(等价 SimulateServer.simulate)

        game              : framework.runtime.game.Game
        config            : 模拟配置(含 agents 状态,用于存档/续跑)
        step              : 本次要跑的步数
        stride            : 每步推进的游戏分钟数(0 = 不推进)
        start_step        : 起始步号(续跑时 = 已完成的步数)
        checkpoints_folder: 存档目录(空 = 不落盘)
        """
        on_step = on_step or self.on_step
        on_agent = on_agent or self.on_agent
        timer = game._timer
        for i in range(start_step, start_step + step):
            title = "Simulate Step[{}/{}, time: {}]".format(i + 1, start_step + step, timer.get_date())
            game.logger.info("\n" + split_line(title, "="))
            sim_time = timer.get_date("%Y%m%d-%H:%M")

            # 剧情调度:到点的 story 事件注入目标角色记忆
            self._inject_story(game, config)

            # 并行思考所有 Agent(对话通过 agent 内互斥锁保证同一时刻一场)
            with ThreadPoolExecutor(max_workers=max(1, self.max_workers)) as executor:
                futures = {
                    executor.submit(game.agent_think, name, status): name
                    for name, status in self._status_map(config, game).items()
                }
                for fut in as_completed(futures):
                    name = futures[fut]
                    plan = fut.result()["plan"]
                    agent = game.get_agent(name)
                    if name not in config["agents"]:
                        config["agents"][name] = {}
                    config["agents"][name].update(agent.to_dict())
                    status = self._status_map(config, game)[name]
                    if plan.get("path"):
                        status["coord"], status["path"] = plan["path"][-1], []
                    config["agents"][name].update({"coord": status["coord"]})

                    # IVD:客观后果反馈 → 更新价值倾向(内化)
                    # 行动描述作为"做了什么",consequence_fn 给出各目标的反馈
                    action_desc = ""
                    if agent.action and agent.action.event:
                        action_desc = str(agent.action.event.get_describe())
                    agent.observe_consequence(action_desc)

                    # 逐 Agent 回调:单个 Agent 思考完成即可推送(实时可视化)
                    if on_agent is not None:
                        on_agent(name, config["agents"][name], i + 1, sim_time)

            config.update({"time": sim_time, "step": i + 1})

            # 存档
            if checkpoints_folder:
                os.makedirs(checkpoints_folder, exist_ok=True)
                with open(f"{checkpoints_folder}/simulate-{sim_time.replace(':', '')}.json", "w", encoding="utf-8") as f:
                    f.write(json.dumps(config, indent=2, ensure_ascii=False))
                with open(f"{checkpoints_folder}/conversation.json", "w", encoding="utf-8") as f:
                    f.write(json.dumps(game.conversation, indent=2, ensure_ascii=False))
                if self.export_decisions and self.decisions_path:
                    self._export_decisions(checkpoints_folder, game)

            # 实时可视化:每个 step 完成后通知外部
            if on_step is not None:
                on_step(config)

            if stride > 0:
                timer.forward(stride)

    # ------------------------------------------------------------------
    def run_step(self, game, agents, status_map, step_no, sim_time) -> Dict[str, Any]:
        """并行执行一步,返回更新后的 config(agents 状态)(轻量接口)"""
        config_agents: Dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._think_one, name, agents[name], status_map[name]): name
                for name in agents
            }
            for fut in as_completed(futures):
                name = futures[fut]
                plan = fut.result()
                status = status_map[name]
                if plan and plan.get("path"):
                    status["coord"], status["path"] = plan["path"][-1], []
                config_agents[name] = {"coord": status["coord"], "name": name}
                if self.on_agent:
                    self.on_agent(name, config_agents[name], step_no, sim_time)
        return config_agents

    def _think_one(self, name, agent, status) -> dict:
        return agent.think(status, {}) if hasattr(agent, "think") else {}

    @staticmethod
    def _status_map(config, game) -> Dict[str, dict]:
        status = {}
        for agent_name in config["agents"]:
            acfg = config["agents"][agent_name]
            coord = acfg.get("coord")
            if coord is None and agent_name in game.agents:
                coord = game.agents[agent_name].coord
            status[agent_name] = {"coord": coord, "path": acfg.get("path", [])}
        return status

    def _export_decisions(self, checkpoints_folder: str, game=None):
        from mavisframework.runtime.logger import get_logger

        logger = get_logger("simulator")
        from mavisframework.output.decisions import export_decision_stream

        constraints = None
        if game is not None and getattr(game, "governance", None) is not None:
            try:
                constraints = game.governance.all_constraints()
            except Exception as e:
                logger.warning(f"read constraints for decisions failed: {e}")
        try:
            export_decision_stream(
                checkpoints_folder,
                self.decisions_path,
                simulation=os.path.basename(checkpoints_folder),
                stride=self.stride,
                roles=self.roles,
                constraints=constraints,
            )
        except Exception as e:
            logger.warning(f"decisions export failed: {e}")

    def emit_time(self, time_str: str):
        if self.on_step:
            self.on_step({"time": time_str})

    def emit_chat_line(self, speaker: str, text: str):
        if self.on_chat_line:
            self.on_chat_line(speaker, text)


# ---------------------------------------------------------------------------
# 内置条件触发检查器(示例)
# ---------------------------------------------------------------------------

@Simulator.register_condition("poignancy")
def _cond_poignancy(game, ev):
    """condition: {"type":"poignancy","role":"agent_name","min":100}
    当指定角色 status.poignancy 累计 >= min 时触发。
    """
    role = ev.get("condition", {}).get("role", "")
    min_val = int(ev.get("condition", {}).get("min", 100))
    agent = game.agents.get(role)
    if agent is None:
        return False
    return agent.status.get("poignancy", 0) >= min_val


@Simulator.register_condition("at_location")
def _cond_at_location(game, ev):
    """condition: {"type":"at_location","role":"agent_name","address":"meeting_room"}
    当指定角色位于含该地址的格子时触发。
    """
    role = ev.get("condition", {}).get("role", "")
    keyword = ev.get("condition", {}).get("address", "")
    agent = game.agents.get(role)
    if agent is None or not keyword:
        return False
    addr = agent.get_tile().get_address(as_list=False)
    return keyword in addr


def split_line(title: str, fill: str = "=") -> str:
    width = max(len(title) + 4, 40)
    return title.center(width, fill)
