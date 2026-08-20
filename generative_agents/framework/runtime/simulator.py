"""framework.runtime.simulator — 模拟调度(并行 + 回调 + 存档,与前端解耦)

从 start.py SimulateServer 迁移:并行执行多个 Agent 的 think,
通过回调钩子(on_agent/on_step/on_chat_line)对外通知,
checkpoint 存档、conversation 存档、决策导出(decisions)在此统一接线。
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, Optional

from framework.core.timer import Timer


class Simulator:
    """模拟调度器:持有 Agent 集合,驱动 think 循环,发出协议消息"""

    def __init__(
        self,
        on_agent: Optional[Callable] = None,      # 单 agent 完成: (name, state, step, sim_time)
        on_step: Optional[Callable] = None,       # 整步完成: (config)
        on_chat_line: Optional[Callable] = None,  # 对话逐句: (speaker, text)
        max_workers: int = 5,                     # 并行思考线程数
        llm_concurrency: int = 0,                 # LLM 并发上限(0 = 自动 = max_workers)
        export_decisions: bool = False,           # 每步结束后导出决策流(experts 平台)
        decisions_path: str = "",
        roles: Optional[Dict[str, str]] = None,   # 角色名 -> 职位(决策导出用)
        stride: int = 2,
    ):
        self.on_agent = on_agent
        self.on_step = on_step
        self.on_chat_line = on_chat_line
        self.max_workers = max_workers
        # LLM 并发上限:显式指定优先,否则取 max_workers(Ollama 单实例并发有限,过多线程只排队)
        self.llm_concurrency = llm_concurrency or max_workers
        self.export_decisions = export_decisions
        self.decisions_path = decisions_path
        self.roles = roles or {}
        self.stride = stride

    # ------------------------------------------------------------------
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
                    self._export_decisions(checkpoints_folder)

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

    def _export_decisions(self, checkpoints_folder: str):
        from framework.runtime.logger import get_logger

        logger = get_logger("simulator")
        from framework.output.decisions import export_decision_stream

        try:
            export_decision_stream(
                checkpoints_folder,
                self.decisions_path,
                simulation=os.path.basename(checkpoints_folder),
                stride=self.stride,
                roles=self.roles,
            )
        except Exception as e:
            logger.warning(f"decisions export failed: {e}")

    def emit_time(self, time_str: str):
        if self.on_step:
            self.on_step({"time": time_str})

    def emit_chat_line(self, speaker: str, text: str):
        if self.on_chat_line:
            self.on_chat_line(speaker, text)


def split_line(title: str, fill: str = "=") -> str:
    width = max(len(title) + 4, 40)
    return title.center(width, fill)
