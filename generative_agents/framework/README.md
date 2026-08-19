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
│   ├── memory.py         # 联想记忆 + 三因子检索(近因0.995/重要/相关)
│   └── agent_core.py     # Agent 生命周期(组件注入式:LLM/记忆/空间/提示词可插拔)
├── scene/
│   └── maze.py           # 空间/碰撞/寻路/地址索引(纯标准库)
├── runtime/
│   ├── protocol.py       # ★ 消息协议(前端/Unity/决策平台统一消费的契约)
│   ├── llm.py            # LLM 适配接口(可插拔:Ollama/OpenAI)
│   ├── llm_providers.py  # Provider 实现(包装现有 modules/model)
│   └── simulator.py      # 并行调度 + 回调钩子(与前端解耦)
├── output/
│   └── decisions.py      # 决策事件导出(供决策平台/专家界面)
└── config/
    └── loader.py         # 从业务层 scenarios/ 加载配置
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

## 使用方式(2 条路线)

### A. 现有项目(modules/ 业务实现)——保持现状
`start.py` / `live.py` / `replay.py` 继续用 `modules/`,框架层提供契约与抽象,不打断。

### B. 新场景 / 新前端——用框架
1. 建 `scenarios/<业务>/`(agents/scene/relationships/story)
2. `ScenarioConfig` 加载 → `Maze` + Agent 组件
3. `Simulator` 驱动 → 产出 `protocol` 消息
4. 前端(Phaser/Unity)按协议消费

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
- ⏳ 后续:业务层配置生效(关系注入/剧情注入)、决策导出接入、FastAPI+WebSocket+Unity
