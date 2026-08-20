# 自研生成式智能体仿真框架(framework/)

**定位**:面向"精细化业务推演"的生成式智能体仿真框架。
Agent 在空间里生活、记忆、反思、决策、交互,每一步可配置、可解释、可实时可视化。

**硬约束**:框架层零渲染依赖(不嵌 Phaser/Unity/Flask)——前端只是"消费协议消息的壳"。

---

## 分层

```
framework/
├── core/                 # 纯逻辑层(零渲染/通信依赖)
│   ├── event.py          # 事件模型(世界最小原子)
│   ├── action.py         # 行动(Action,时间注入)
│   ├── spatial.py        # 空间记忆(地址树)
│   ├── schedule.py       # 日程(时间注入)
│   ├── timer.py          # 模拟时钟(可注入,零全局状态)
│   ├── memory.py         # 联想记忆 + 三因子检索(近因0.995/重要/相关)
│   ├── store.py          # 记忆存储抽象(SimpleStore 纯 stdlib / LlamaIndexStore 向量)
│   ├── associate.py      # 联想记忆(事件/对话/想法 + 检索)
│   ├── agent_core.py     # Agent 完整生命周期(组件注入式:LLM/记忆/空间/提示词/时钟)
│   └── prompts/          # 提示词系统(Scratch,26 个 prompt_xxx 方法)
├── scene/
│   └── maze.py           # 空间/碰撞/寻路/地址索引(纯标准库)
├── runtime/
│   ├── protocol.py       # ★ 消息协议(前端/Unity/决策平台统一消费的契约)
│   ├── llm.py            # LLM 适配接口(可插拔:Ollama/OpenAI)
│   ├── llm_providers.py  # Provider 实现(自包含,零 modules 依赖)
│   ├── game.py           # 游戏容器(创建 agents + maze + conversation)
│   ├── simulator.py      # 并行调度 + 回调 + 存档 + 决策导出(与前端解耦)
│   └── compressor.py     # 实时压缩器(逐步生成 Agent 状态/回放帧)
├── output/
│   └── decisions.py      # 决策事件导出(供决策平台/专家界面)
└── config/
    └── loader.py         # 场景配置 + 模拟配置加载(personas/新开/续跑)
```

## 与业务层/前端层的关系

```
scenarios/          业务层(换业务=改配置):角色/场景/关系/剧情
   ↓ 加载
framework/          框架层(纯逻辑,零渲染)
   ↓ 产出
runtime/protocol.py 消息协议(agent/time/chat_line/decision...)
   ↓ 消费
frontend/phaser     前端壳(现在,浏览器)
frontend/unity      前端壳(将来,WebSocket 消费同一协议)
决策平台            消费 DecisionEventStream
```

## 关键契约(runtime/protocol.py)

| 消息 | 用途 | 消费者 |
|---|---|---|
| `AgentState` | 单 agent 状态(坐标/路径/动作) | Phaser/Unity |
| `TimeMsg` | 模拟时间 | 前端时钟 |
| `ChatLineMsg` | 对话逐句 | 对话面板 |
| `SnapshotMsg` | 全量快照 | 新连接追赶 |
| `DecisionEvent` | 决策事件 | 决策平台/专家界面 |

**坐标一律格子坐标;消息传输无关(SSE/WebSocket 都可)。**

## 使用方式(1 条路线)

### 框架驱动 ✅ 已落地
`live_fastapi.py` 即框架路线——框架 `Game` + `Simulator` + `LiveCompressor` 驱动完整模拟(并行思考/存档/决策导出/WebSocket 推送),项目已无 `modules/` 旧实现,全部逻辑在框架内。投资场景 5 角色从零跑通验证。

> 旧实现(start.py/live.py/compress.py/replay.py + modules/)已移除,可在 git 历史回退。

## Unity 迁移(框架视角)

```
框架核心(agent/记忆/寻路/决策导出)  ← 零改动
        ↓ protocol.py 消息
传输:SSE(Phaser) → WebSocket(Unity)   ← 只换传输层
前端:Phaser → Unity                    ← 只换渲染层(消费同一协议)
```

**框架层不感知前端是什么——这就是"Phaser 不嵌入框架"的保证。**

## 状态

- ✅ 已完成:protocol / core(event,memory,agent_core) / scene(maze) / runtime(llm,simulator) / output(decisions) / config(loader) / scenarios(investment 示例)
- ✅ 框架独立运行:Agent 完整生命周期(思考/日程/感知/反应/对话/反思)、记忆存储(SimpleStore 纯 stdlib / LlamaIndexStore 向量可选)、提示词系统全部迁入 framework,不依赖 modules
- ✅ 实时服务:live_fastapi.py 由框架 Game + Simulator 驱动(FastAPI + WebSocket),决策导出(decisions.json)接入管线
- ⏳ 后续:业务层配置生效(关系注入/剧情注入)、Unity 前端
