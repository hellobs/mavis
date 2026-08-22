# MAVIS 生成式多智能体框架

基于斯坦福 AI 小镇(Generative Agents)重构的实现,用于多智能体仿真与可视化。

## 功能

- 智能体独立，由大模型驱动自主决策、移动、对话
- **自研框架内核 `framework/`**:Agent 完整生命周期(思考/日程/感知/反应/对话/反思)、三因子记忆检索、可插拔存储(纯 stdlib / 向量)、提示词系统
- 消息契约(protocol.py),支撑实时流与客户端对接
- 提供一种实时可视化方式:FastAPI + WebSocket 边跑边看(框架驱动),对话逐句推送
- 决策导出:模拟过程自动生成 decisions.json(时间/角色/动作/涉他/重要性),供决策平台与专家界面
- 支持 API 与本地 Ollama

## 快速开始

### 1. 环境准备

需要 [uv](https://docs.astral.sh/uv/) 或 [conda](https://docs.conda.io/):

```bash
# 用 uv(更快)
cd generative_agents
uv venv .venv --python 3.12
uv pip install -r requirements.txt

# 或使用 conda
conda create -n generative_agents_cn python=3.12
conda activate generative_agents_cn
pip install -r requirements.txt
```

> 运行时用 `python live_fastapi.py` 即可(有 conda 环境先 `conda activate`;有 uv 环境用 `.venv/Scripts/python.exe`)。

### 2. 配置大模型(二选一)

- **本地 Ollama**(免费,推荐开发调试):安装 [Ollama](https://ollama.com/) 并拉取模型
  ```bash
  ollama pull qwen3:4b-instruct-2507-q4_K_M
  ollama pull qwen3-embedding:0.6b-q8_0
  ```
  无需改配置(默认就是 Ollama)。
- **DeepSeek API**:在 `generative_agents/.env` 中配置
  ```
  LLM_API_KEY=你的key
  ```
  并编辑 `generative_agents/data/config.json` 的 `agent.think.llm`:
  ```json
  "llm": {
    "provider": "openai",
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": ""
  }
  ```

### 3. 实时观看(FastAPI + WebSocket)

```bash
cd generative_agents
python live_fastapi.py --name sim-test --start "20250213-09:30" --stride 2 --step 0 --port 5001
```

浏览器打开 http://127.0.0.1:5001/

## 常用参数

| 参数 | 说明 |
|---|---|
| `--name` | 模拟名称(唯一,存档按此分目录) |
| `--start` | 起始时间 |
| `--stride` | 每步游戏分钟数(2 较细腻) |
| `--step` | 步数,`0`=持续运行 |
| `--resume` | 从断点续跑 |
| `--port` | 服务端口 |

## 目录结构

```
generative_agents/
├── live_fastapi.py     # ★ 实时模拟+可视化(FastAPI + WebSocket,框架驱动,唯一入口)
├── framework/          # ★ 自研框架内核(零前端依赖,可独立运行)
│   ├── core/           #   Agent 生命周期/记忆/日程/空间/事件/时钟/提示词
│   ├── scene/          #   空间/碰撞/寻路
│   ├── runtime/        #   协议(protocol.py)/LLM 适配/游戏容器/并行调度/实时压缩器
│   ├── output/         #   决策导出(decisions.json)
│   └── config/         #   场景配置加载 + 模拟配置(新开/续跑)
├── scenarios/          # 业务场景配置(investment: 人物关系/剧情事件)
├── frontend/           # 可视化前端(Phaser)
├── data/               # 配置与提示词
└── results/            # 存档与回放数据
```

## 说明

- 实时可视化走 WebSocket(`/ws`),推送框架契约消息(agent/time/chat_line/snapshot);浏览器断线 3s 后自动重连
- 实时服务由 `framework/` 驱动(Game + Simulator + LiveCompressor)
- 换用英文界面/提示词:改 `framework/prompt/scratch.py` 与前端文案即可,逻辑无需改动
- 前端 Phaser 脚本:服务端优先用 `frontend/static/vendor/phaser.min.js`(本地化,断网可用),不存在时回退 CDN;离线环境下建议下载 phaser.min.js 放入该目录
  - **首次运行前**(可选但推荐):在浏览器打开 `https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.min.js`(约 1.3MB),右键另存为 `frontend/static/vendor/phaser.min.js`。之后无需外网即可显示画面

## 修改地图

创建新地图，有以下几种方案：

1. 参考原始generative_agents项目中maze.py的逻辑，修改现有代码，以便兼容tiled编辑器导出的json和csv数据文件；
2. 参考现有的maze.json格式，编写代码用于合并tiled编辑器导出的maze_meta_info.json、collision_maze.csv、sector_maze.csv等文件，为新地图生成maze.json。
3. `jiejieje`开发了一款地图标注工具: https://github.com/jiejieje/tiled_to_maze.json

## 参考资料

### 论文

[Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)

### 代码

[Generative Agents](https://github.com/joonspk-research/generative_agents)

[wounderland](https://github.com/Archermmt/wounderland)
