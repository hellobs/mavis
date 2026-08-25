# mavisframework

[English](./README_en.md) | 简体中文

自研生成式智能体仿真框架(MAVIS),面向"精细化业务推演"。Agent 在空间环境中自主生活、记忆、反思、决策与交互,每一步均可配置、可解释、可实时可视化。

框架层保持零渲染依赖(不嵌入 Phaser / Unity / Flask 等前端或服务端框架),前端仅作为协议消息的消费端。

## 1. 安装

推荐使用 [uv](https://docs.astral.sh/uv/),Python ≥ 3.12。

```bash
# 方式 A:构建 wheel 并安装(推荐,已验证稳定)
uv build
uv pip install dist/mavisframework-1.0.0-py3-none-any.whl

# 方式 B:可编辑安装(开发框架时改动即时生效)
uv venv --python 3.12
uv pip install -e .
```

已知问题:可编辑安装(`-e`)在当前环境下存在导入异常——顶层 `mavisframework` 可正常导入,但切换工作目录后嵌套子模块(如 `mavisframework.config.loader`)可能解析失败。生产环境或平台集成请使用方式 A(wheel 安装)。

运行依赖仅 `pydantic>=2.0` 与 `requests>=2.31`,无 AI 或渲染框架的硬依赖。LLM 通过可插拔 Provider(Ollama / OpenAI)接入,非强制。

## 2. 模块结构

```
mavisframework/
├── core/                 # 纯逻辑层(零渲染/通信依赖)
│   ├── event.py          # 事件模型
│   ├── action.py         # 行动(Action,时间注入)
│   ├── spatial.py        # 空间记忆(地址树)
│   ├── schedule.py       # 日程(时间注入)
│   ├── timer.py          # 模拟时钟(可注入,零全局状态)
│   ├── memory.py         # 联想记忆 + 三因子检索(近因 0.995 / 重要性 / 相关性)
│   ├── store.py          # 记忆存储抽象(SimpleStore 纯 stdlib / LlamaIndexStore 向量)
│   ├── associate.py      # 联想记忆(事件/对话/想法 + 检索)
│   ├── agent_core.py     # Agent 完整生命周期(组件注入式)
│   └── prompts/          # 提示词模板(随包分发)
├── scene/
│   └── maze.py           # 空间/碰撞/寻路/地址索引(纯标准库)
├── runtime/
│   ├── protocol.py       # 消息协议(前端/Unity/决策平台的统一契约)
│   ├── llm.py            # LLM 适配接口(可插拔)
│   ├── llm_providers.py  # Provider 实现(自包含)
│   ├── game.py           # 游戏容器(agents + maze + conversation)
│   ├── simulator.py      # 并行调度/回调/存档/决策导出
│   └── compressor.py     # 实时压缩器(Agent 状态/回放帧)
├── output/
│   └── decisions.py      # 决策事件导出
└── config/
    ├── loader.py         # 场景配置与模拟配置加载
    └── validator.py      # 配置校验(语法/地图一致性/角色交叉)
```

## 3. 环境变量

| 变量 | 默认值 | 用途 |
|---|---|---|
| `MAVIS_PROMPT_DIR` | 包内 `prompts/` | 提示词模板目录 |
| `MAVIS_CONFIG_PATH` | `data/config.json` | agent_base 配置(LLM 等) |
| `MAVIS_ASSETS_ROOT` | `assets/village` | 静态资源相对根 |
| `MAVIS_STATIC_ROOT` | `frontend/static` | 前端静态资源根(compressor) |
| `MAVIS_CHECKPOINTS_ROOT` | `results/checkpoints` | 存档根目录 |

## 4. 分层关系

```
scenarios/          业务层(换业务=改配置):角色/场景/关系/剧情
   ↓ 加载
mavisframework/     框架层(纯逻辑,零渲染)
   ↓ 产出
runtime/protocol.py 消息协议(agent/time/chat_line/decision...)
   ↓ 消费
frontend/phaser     前端壳(当前,浏览器)
frontend/unity      前端壳(规划中,WebSocket 消费同一协议)
决策平台            消费 DecisionEventStream
```

## 5. 消息协议

定义于 `runtime/protocol.py`,坐标一律为格子坐标,传输与具体通道无关(SSE / WebSocket 均可)。

| 消息 | 用途 | 消费者 |
|---|---|---|
| `AgentState` | 单 agent 状态(坐标/路径/动作) | 前端/Unity |
| `TimeMsg` | 模拟时间 | 前端时钟 |
| `ChatLineMsg` | 对话逐句 | 对话面板 |
| `SnapshotMsg` | 全量快照 | 新连接追赶 |
| `DecisionEvent` | 决策事件 | 决策平台/专家界面 |

## 6. 使用方式

框架 `Game` + `Simulator` + `LiveCompressor` 驱动完整模拟(并行思考/存档/决策导出/WebSocket 推送)。项目已移除旧实现(`modules/`,以及 `start.py`/`live.py`/`compress.py`/`replay.py`),全部逻辑在框架内,可在 git 历史中回退查看。

完整演示平台见 [Provenance](https://github.com/hellobs/provenance):其实时服务 `live_fastapi.py` 即框架路线的参考实现(FastAPI + WebSocket 消费框架契约消息)。

## 7. Unity 迁移

```
框架核心(agent/记忆/寻路/决策导出)  ← 零改动
        ↓ protocol.py 消息
传输:SSE(Phaser) → WebSocket(Unity)   ← 仅更换传输层
前端:Phaser → Unity                    ← 仅更换渲染层(消费同一协议)
```

框架层不感知前端的具体实现,这是"Phaser 不嵌入框架"的结构保证。

## 8. 仓库结构

```
mavisframework/          # 框架包(pip 包,pyproject 位于仓库根)
├── core/ scene/ runtime/ output/ config/ prompt/
└── prompts/             # 提示词模板(随包分发)
config_tool/             # 角色配置工具(独立 FastAPI 服务)
pyproject.toml           # 包构建配置(uv build / uv pip install)
```

config_tool 属于框架仓库,但其产物(角色/关系/剧情)写入平台的前端资源与场景目录。默认探测兄弟目录 `../provenance`(平台仓库,兼容平台代码位于仓库子目录或根目录两种结构);部署时可通过环境变量显式指定:

- `MAVIS_ASSETS_ROOT` — 平台前端资源根(`frontend/static/assets/village`)
- `MAVIS_SCENARIOS_DIR` — 平台场景目录(`scenarios`)

## 9. 状态

- 已完成:protocol / core / scene(maze) / runtime(llm, simulator) / output(decisions) / config(loader, validator)
- 框架可独立运行:Agent 完整生命周期、记忆存储(SimpleStore / LlamaIndexStore 可选)、提示词系统均不依赖外部模块
- 平台消费:Provenance 平台的实时服务由框架 Game + Simulator 驱动,决策导出接入管线
- 后续:业务层配置生效(关系注入/剧情注入)、Unity 前端
