# Tutorial: Message Protocol

This tutorial explains the mavisframework **message protocol**
(`runtime/protocol.py`) — the unified contract consumed by frontends (Phaser),
Unity and governance platforms. After reading, you'll know what messages are
pushed during a simulation, their structures, and how to use `validate_message`
as a safety net.

## Protocol Design Principles

- **Transport-agnostic**: messages are plain JSON; SSE / WebSocket / HTTP all work
- **Grid coordinates only**: always `[x, y]` (int, int); frontends/Unity convert
  to world coordinates themselves
- **Contract = documentation**: message structure is the integration basis for
  consumers; changing the protocol breaks every frontend

## Message Overview

| Message type | Purpose | Push timing |
|---|---|---|
| `agent` | single agent state (coord/path/action) | per agent thinking done |
| `time` | simulation time | per step done |
| `chat_line` | dialogue lines | per line generated |
| `snapshot` | full snapshot | new connection catch-up |
| `done` | simulation finished | all done |
| `error` | runtime error | on failure |

## 1. AgentState (agent)

```python
{
  "type": "agent",
  "name": "老周",
  "coord": [10, 6],             # current grid coordinate
  "path": [[10, 6], [11, 6], ...],  # pathfinding points (frontend moves along)
  "action": "正在查看行情",       # action description
  "location": "投资咨询中心,资料室",  # business-meaning address
  "currently": "正在盯着今天的行情",
  "conversation": {"20250213-09:32": "老周:今天行情如何"}
}
```

## 2. TimeMsg (time)

```python
{"type": "time", "time": "20250213-09:32"}
```

## 3. ChatLineMsg (chat_line)

```python
{"type": "chat_line", "speaker": "老周", "text": "今天新能源板块涨了不少"}
```

## 4. SnapshotMsg (snapshot)

Pushed on new client connection for catch-up:

```python
{
  "type": "snapshot",
  "time": "20250213-09:32",
  "agents": {
    "老周": {"name": "老周", "coord": [10, 6], "action": "...", ...},
    "沈砚之": {...}
  }
}
```

## 5. DoneMsg / ErrorMsg

```python
{"type": "done"}
{"type": "error", "message": "LLM connection failed: ..."}
```

## 6. Safety Net: validate_message

The framework provides a light validation function usable by both producers
and consumers:

```python
from mavisframework import validate_message

msgs = [
    {"type": "agent", "name": "老周", "coord": [10, 6]},
    {"type": "time", "time": "20250213-09:32"},
    {"type": "chat_line", "speaker": "老周", "text": "hello"},
    {"type": "unknown", "anything": 1},
]

for m in msgs:
    print(validate_message(m))   # True True True False
```

**Rules**:

| Input | Result |
|---|---|
| not a dict / missing `type` | False |
| `agent` missing `name` or `coord` | False |
| `time`/`chat_line`/`snapshot`/`done`/`error` (type matches) | True |
| unknown type | False |

> This is a "light" check: only type and key fields. Full field-level
> validation (e.g. coordinate bounds) is up to consumers as needed — the
> protocol is a contract; consumers should tolerate unknown fields.

## 7. Frontend Integration Example (WebSocket)

```python
import json
import websockets

async def consume():
    async with websockets.connect("ws://127.0.0.1:5001/ws") as ws:
        async for raw in ws:
            msg = json.loads(raw)
            if not validate_message(msg):
                print("out-of-protocol message:", raw)
                continue
            if msg["type"] == "agent":
                draw_agent(msg["name"], msg["coord"], msg.get("path", []))
            elif msg["type"] == "time":
                update_clock(msg["time"])
            elif msg["type"] == "chat_line":
                append_chat(msg["speaker"], msg["text"])
```

## Summary

- The protocol is a JSON contract: agent / time / chat_line / snapshot / done / error
- Grid coordinates only; transport-agnostic (SSE/WebSocket/HTTP)
- `validate_message` is a light safety net; consumers tolerate unknown fields
- Changing the protocol breaks every frontend — version your changes

Next: read the [decision export tutorial](tutorial-decisions-en.md) to see how
simulation results become a decision event stream.
