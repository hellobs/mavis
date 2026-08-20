"""framework.runtime.protocol — 消息协议(框架契约)

所有外部消费方(Phaser 前端 / Unity / 决策平台)统一按本协议解析数据。
传输层可换(SSE / WebSocket / HTTP),但消息结构不变。

坐标一律使用"格子坐标"(int,int),由前端/Unity 自行换算世界坐标。
"""
from typing import Any, Dict, List, Optional, TypedDict


# ---------------------------------------------------------------------------
# 实时消息(模拟运行中推送)
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    """单个 Agent 的状态(逐 agent 实时推送)"""
    type: str                     # "agent"
    name: str                     # 角色名
    coord: List[int]              # 当前格子坐标 [x, y]
    path: List[List[int]]         # 寻路路径点(格子),前端沿点移动
    action: str                   # 当前动作描述
    location: str                 # 地址(业务语义,如 "资料室:资料桌")
    currently: str                # 人设当前状态
    conversation: Dict[str, str]  # 该时间点的对话文本


class TimeMsg(TypedDict):
    """模拟时间(整步完成推送)"""
    type: str                     # "time"
    time: str                     # "20250213-12:42"


class ChatLineMsg(TypedDict):
    """对话逐句(每生成一句推送)"""
    type: str                     # "chat_line"
    speaker: str
    text: str


class SnapshotMsg(TypedDict):
    """全量快照(新连接时推送,供追赶进度)"""
    type: str                     # "snapshot"
    agents: Dict[str, AgentState]
    time: str


class DoneMsg(TypedDict):
    type: str                     # "done"


class ErrorMsg(TypedDict):
    type: str                     # "error"
    message: str


# ---------------------------------------------------------------------------
# 决策事件(供决策平台 / 专家界面,离线导出)
# ---------------------------------------------------------------------------

class DecisionEvent(TypedDict, total=False):
    """一个 Agent 的一条决策事件"""
    id: str                       # 全局唯一 "e-0001"
    step: int                     # 第几步
    time: str                     # 模拟时间
    agent: str                    # 角色名
    role: str                     # 职位/职责(业务字段)
    action: str                   # 动作描述
    location: str                 # 地址
    predicate: str                # "此时" / "对话" / "正在"
    poignancy: int                # 事件重要性分
    involves: List[str]           # 涉他(对话/协作对象)
    has_conversation: bool
    category: Optional[str]       # 分类(平台全权,导出留空)
    risk_level: Optional[str]     # 风险等级(平台全权,导出留空)
    tags: List[str]


class DecisionEventStream(TypedDict):
    """决策事件流(一个模拟的完整导出)"""
    simulation: str
    start_time: str
    stride: int
    total_steps: int
    events: List[DecisionEvent]


# ---------------------------------------------------------------------------
# 配置 schema(业务方填表 → 框架配置)
# ---------------------------------------------------------------------------

class RelationshipConfig(TypedDict):
    """角色关系(边列表,邻接表)"""
    agents: List[str]             # [A, B]
    type: str                     # 业务关系类型
    direction: str                # "A→B" / "双向"
    trigger: str                  # 互动约定(何时/何地/做什么)
    frequency: str                # high / medium / low


class StoryEventConfig(TypedDict):
    """剧情事件(危机注入)"""
    id: str
    time: str                     # 模拟时间触发
    event_type: str               # 市场波动/监管/客户投诉/内部冲突...
    content: str                  # 事件描述(注入环境)
    targets: List[str]            # 影响对象(角色名 / "all")
    expected: str                 # 期望触发行为(评估/开会/对话...)


def validate_message(msg: Dict[str, Any]) -> bool:
    """校验消息是否合规(框架契约的简易校验)"""
    if not isinstance(msg, dict) or "type" not in msg:
        return False
    t = msg["type"]
    if t == "agent":
        return "name" in msg and "coord" in msg
    if t in ("init", "time", "chat_line", "snapshot", "done", "error"):
        return True
    return False
