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
        if f.endswith(".json") and f != "conversation.json"
    )
    events: List[DecisionEvent] = []
    for idx, fname in enumerate(files):
        with open(os.path.join(checkpoints_folder, fname), "r", encoding="utf-8") as f:
            data = json.load(f)
        time_key = data.get("time", "")
        step = data.get("step", 0)
        involves = extract_involves(conversation, time_key)
        for agent_name, ad in data.get("agents", {}).items():
            ev = ad.get("action", {}).get("event", {})
            predicate = ev.get("predicate", "")
            has_chat = any(agent_name in i for i in [involves])
            events.append({
                "id": "e-%04d" % (idx + 1),
                "step": step,
                "time": time_key,
                "agent": agent_name,
                "role": roles.get(agent_name, ""),
                "action": ev.get("describe", ""),
                "location": "，".join(ev.get("address", [])),
                "predicate": predicate,
                "poignancy": ad.get("status", {}).get("poignancy", 0) if isinstance(ad.get("status"), dict) else 0,
                "goal_score": ad.get("status", {}).get("goal_score", None) if isinstance(ad.get("status"), dict) else None,
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
        if f.endswith(".json") and f != "conversation.json"
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
