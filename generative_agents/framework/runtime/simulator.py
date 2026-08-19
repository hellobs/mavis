"""framework.runtime.simulator — 模拟调度(并行 + 回调,与前端解耦)

- 并行执行多个 Agent 的 think
- 通过回调钩子(on_agent/on_step/on_chat_line)对外通知
- 回调由外部注入(SSE 前端 / FastAPI+WebSocket / 决策平台),框架本身零渲染
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional


class Simulator:
    """模拟调度器:持有 Agent 集合,驱动 think 循环,发出协议消息"""

    def __init__(
        self,
        on_agent: Optional[Callable] = None,      # 单 agent 完成: (name, state, step, sim_time)
        on_step: Optional[Callable] = None,       # 整步完成: (config)
        on_chat_line: Optional[Callable] = None,  # 对话逐句: (speaker, text)
        max_workers: int = 5,
    ):
        self.on_agent = on_agent
        self.on_step = on_step
        self.on_chat_line = on_chat_line
        self.max_workers = max_workers

    def run_step(self, agents: Dict[str, Any], status_map: Dict[str, dict],
                 step_no: int, sim_time: str) -> Dict[str, Any]:
        """并行执行一步,返回更新后的 config(agents 状态)

        agents   : {name: agent_core(或现有 agent)}
        status_map: {name: {"coord":..., "path":...}}
        """
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
                config_agents[name] = {
                    "coord": status["coord"],
                    "name": name,
                }
                if self.on_agent:
                    self.on_agent(name, config_agents[name], step_no, sim_time)
        return config_agents

    def _think_one(self, name, agent, status) -> dict:
        return agent.think(status, {}) if hasattr(agent, "think") else {}

    def emit_time(self, time_str: str):
        if self.on_step:
            self.on_step({"time": time_str})

    def emit_chat_line(self, speaker: str, text: str):
        if self.on_chat_line:
            self.on_chat_line(speaker, text)
