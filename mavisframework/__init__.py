"""mavisframework — 自研生成式智能体仿真框架

顶层 API 一览(推荐用法,内部子模块路径仍可用):

    # 配置加载与校验
    from mavisframework import (
        load_config, load_config_from_log, load_scenario,
        validate_all,
    )
    # 运行时
    from mavisframework import Game, Simulator, create_llm_provider
    # 消息协议
    from mavisframework import validate_message
    # 核心
    from mavisframework import Agent, Timer, Maze

分层:
- core    : Agent 生命周期、记忆、日程、空间(纯逻辑,零渲染依赖)
- scene   : 空间/碰撞/寻路
- runtime : 运行调度、LLM 适配、消息协议(protocol)
- config  : 配置加载与校验
- output  : 决策事件导出
- prompt  : 提示词系统

业务层(scenarios/)与前端层(frontend/)与框架层分离:
- 换业务 = 改 scenarios/ 配置
- 换前端(Phaser/Unity)= 按 runtime/protocol.py 消费消息
"""

from mavisframework.config.loader import (
    load_config,
    load_config_from_log,
    load_json,
    load_scenario,
    ScenarioConfig,
)
from mavisframework.config.validator import (
    validate_agents,
    validate_relationships,
    validate_story,
    validate_all,
)
from mavisframework.core.agent_core import Agent
from mavisframework.core.timer import Timer, to_date, daily_duration
from mavisframework.scene.maze import Maze
from mavisframework.runtime.game import Game
from mavisframework.runtime.simulator import Simulator
from mavisframework.runtime.llm import create_llm_provider, LLMProvider
from mavisframework.runtime.protocol import (
    AgentState,
    TimeMsg,
    ChatLineMsg,
    SnapshotMsg,
    DoneMsg,
    ErrorMsg,
    DecisionEvent,
    DecisionEventStream,
    validate_message,
)

__version__ = "1.0.0"

__all__ = [
    # config
    "load_config",
    "load_config_from_log",
    "load_json",
    "load_scenario",
    "ScenarioConfig",
    "validate_agents",
    "validate_relationships",
    "validate_story",
    "validate_all",
    # core
    "Agent",
    "Timer",
    "to_date",
    "daily_duration",
    # scene
    "Maze",
    # runtime
    "Game",
    "Simulator",
    "create_llm_provider",
    "LLMProvider",
    # protocol
    "AgentState",
    "TimeMsg",
    "ChatLineMsg",
    "SnapshotMsg",
    "DoneMsg",
    "ErrorMsg",
    "DecisionEvent",
    "DecisionEventStream",
    "validate_message",
]
