# 教程:消息协议

本教程讲解 mavisframework 的**消息协议**(`runtime/protocol.py`)——前端(Phaser)、
Unity、决策平台统一消费的契约。读完你会理解:模拟运行中会推送什么消息、
每条消息的结构、以及如何用 `validate_message` 做防线。

## 协议的设计原则

- **传输无关**:消息是纯 JSON 结构,SSE / WebSocket / HTTP 都能承载
- **坐标统一**:一律是格子坐标 `[x, y]`(int,int),由前端/Unity 自行换算世界坐标
- **契约即文档**:消息结构就是消费方的对接依据,改了协议 = 破坏了所有前端

## 消息一览

| 消息 type | 用途 | 推送时机 |
|---|---|---|
| `agent` | 单个 Agent 状态(坐标/路径/动作) | 每 Agent 思考完成 |
| `time` | 模拟时间 | 每步完成 |
| `chat_line` | 对话逐句 | 每生成一句 |
| `snapshot` | 全量快照 | 新连接追赶进度 |
| `done` | 模拟结束 | 全部完成 |
| `error` | 运行错误 | 出错时 |

## 1. AgentState(agent)

```python
{
  "type": "agent",
  "name": "老周",
  "coord": [10, 6],             # 当前格子坐标
  "path": [[10, 6], [11, 6], ...],  # 寻路路径点(前端沿点移动)
  "action": "正在查看行情",       # 动作描述
  "location": "投资咨询中心,资料室",  # 业务语义地址
  "currently": "正在盯着今天的行情",
  "conversation": {"20250213-09:32": "老周:今天行情如何"}
}
```

## 2. TimeMsg(time)

```python
{"type": "time", "time": "20250213-09:32"}
```

## 3. ChatLineMsg(chat_line)

```python
{"type": "chat_line", "speaker": "老周", "text": "今天新能源板块涨了不少"}
```

## 4. SnapshotMsg(snapshot)

新客户端连接时推送全量状态,供追赶进度:

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
{"type": "error", "message": "LLM 连接失败: ..."}
```

## 6. 防线:validate_message

框架提供简易校验函数,消费方/生产方都可用它做防线:

```python
from mavisframework import validate_message

msgs = [
    {"type": "agent", "name": "老周", "coord": [10, 6]},
    {"type": "time", "time": "20250213-09:32"},
    {"type": "chat_line", "speaker": "老周", "text": "你好"},
    {"type": "unknown", "anything": 1},
]

for m in msgs:
    print(validate_message(m))   # True True True False
```

**规则**:

| 输入 | 结果 |
|---|---|
| 非 dict / 缺 `type` | False |
| `agent` 缺 `name` 或 `coord` | False |
| `time`/`chat_line`/`snapshot`/`done`/`error`(只要 type 对) | True |
| 未知 type | False |

> 这是"简易"校验:只查 type 与关键字段。完整的字段级校验(如坐标范围)由
> 消费方按需实现——协议是契约,消费方应对未知字段保持宽容。

## 7. 前端接入示例(WebSocket)

```python
import json
import websockets

async def consume():
    async with websockets.connect("ws://127.0.0.1:5001/ws") as ws:
        async for raw in ws:
            msg = json.loads(raw)
            if not validate_message(msg):
                print("协议外消息:", raw)
                continue
            if msg["type"] == "agent":
                draw_agent(msg["name"], msg["coord"], msg.get("path", []))
            elif msg["type"] == "time":
                update_clock(msg["time"])
            elif msg["type"] == "chat_line":
                append_chat(msg["speaker"], msg["text"])
```

## 小结

- 协议是 JSON 契约:agent / time / chat_line / snapshot / done / error
- 坐标一律格子坐标;传输无关(SSE/WebSocket/HTTP 均可)
- `validate_message` 提供简易防线,消费方对未知字段保持宽容
- 改协议 = 破坏所有前端,变更需版本化

下一步:阅读 [决策导出教程](tutorial-decisions.md),了解模拟结果如何沉淀为决策事件流。
