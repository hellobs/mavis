"""framework.output.decisions — 决策事件导出(供决策平台/专家界面)

从 checkpoints 存档生成 DecisionEventStream(见 runtime/protocol.py),
每条事件 = 时间 + 角色 + role + 动作 + 地址 + 涉他 + poignancy。
category/risk_level 留空,由决策平台全权分类。
"""
import json
import glob
import os
from typing import Dict, List

from mavisframework.runtime.protocol import DecisionEvent, DecisionEventStream


def load_conversation(conversation_path: str) -> Dict[str, List]:
    if not os.path.exists(conversation_path):
        return {}
    with open(conversation_path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_involves(conversation: Dict, time_key: str) -> List[str]:
    """从该时间点的对话提取参与者(涉他)"""
    involved = set()
    if time_key in conversation:
        for chats in conversation[time_key]:
            for persons, _ in chats.items():
                head = persons.split(" @ ")[0]
                for p in head.split(" -> "):
                    involved.add(p)
    return sorted(involved)


def generate_decision_events(
    checkpoints_folder: str,
    roles: Dict[str, str] = None,
) -> List[DecisionEvent]:
    """从 checkpoints 生成决策事件列表

    roles: {角色名: 职位}(业务层提供,如 {"沈砚之": "首席投资顾问"})
    """
    roles = roles or {}
    conversation = load_conversation(os.path.join(checkpoints_folder, "conversation.json"))

    files = sorted(
        f for f in os.listdir(checkpoints_folder)
        if f.endswith(".json")
        and f not in ("conversation.json", "decisions.json", "interventions.json")
    )
    events: List[DecisionEvent] = []
    _ev_idx = 0
    for idx, fname in enumerate(files):
        with open(os.path.join(checkpoints_folder, fname), "r", encoding="utf-8") as f:
            data = json.load(f)
        time_key = data.get("time", "")
        step = data.get("step", 0)
        involves = extract_involves(conversation, time_key)
        for agent_name, ad in data.get("agents", {}).items():
            _ev_idx += 1
            ev = ad.get("action", {}).get("event", {})
            predicate = ev.get("predicate", "")
            has_chat = any(agent_name in i for i in [involves])
            status = ad.get("status", {})
            status = status if isinstance(status, dict) else {}
            alignment = status.get("goal_alignment") or {}
            tendency = status.get("value_tendency") or {}
            # IVD:goal_score = 行动对约束的整体对齐度(逐目标 alignment 均值)
            goal_score = None
            if alignment:
                goal_score = sum(alignment.values()) / len(alignment)
            events.append({
                "id": "e-%04d" % _ev_idx,
                "step": step,
                "time": time_key,
                "agent": agent_name,
                "role": roles.get(agent_name, ""),
                "action": ev.get("describe", ""),
                "location": ",".join(ev.get("address", [])),
                "predicate": predicate,
                "poignancy": status.get("poignancy", 0),
                "goal_score": goal_score,
                "goal_alignment": alignment,       # 逐目标即时对齐(审计)
                "value_tendency": tendency,        # 内化的价值倾向(审计)
                "involves": involves,
                "has_conversation": has_chat,
                "category": None,
                "risk_level": None,
                "tags": [],
            })
    return events


def export_decision_stream(
    checkpoints_folder: str,
    output_path: str,
    simulation: str = "",
    stride: int = 2,
    roles: Dict[str, str] = None,
) -> str:
    """导出决策事件流 JSON(供决策平台导入)"""
    events = generate_decision_events(checkpoints_folder, roles)
    # 起始时间取第一个存档
    start_time = ""
    files = sorted(
        f for f in os.listdir(checkpoints_folder)
        if f.endswith(".json")
        and f not in ("conversation.json", "decisions.json", "interventions.json")
    )
    if files:
        with open(os.path.join(checkpoints_folder, files[0]), "r", encoding="utf-8") as f:
            start_time = json.load(f).get("time", "")

    stream: DecisionEventStream = {
        "simulation": simulation,
        "start_time": start_time,
        "stride": stride,
        "total_steps": len(files),
        "events": events,
    }
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(stream, f, ensure_ascii=False, indent=2)
    return output_path
