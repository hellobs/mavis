# MAVIS 生成式智能体(由GenerativeAgents CN基础开发)

基于斯坦福 AI 小镇(Generative Agents)重构的中文实现,用于多智能体仿真与可视化。

## 功能

- 5 个投资场景智能体(投资顾问 / 量化分析 / 行业研究 / 风控 / 散户),由大模型驱动自主决策、移动、对话
- 实时可视化:模拟运行时可边跑边看,支持对话逐句显示、时间与状态展示
- 事后回放:模拟结果可压缩为回放数据,随时回看
- 支持 DeepSeek API 与本地 Ollama 两种大模型后端

## 快速开始

### 1. 环境准备

```bash
conda create -n generative_agents_cn python=3.12
conda activate generative_agents_cn
pip install -r requirements.txt
```

大模型二选一:

- **本地 Ollama**:安装 [Ollama](https://ollama.com/) 并拉取模型
  ```bash
  ollama pull qwen3:4b-instruct-2507-q4_K_M
  ollama pull qwen3-embedding:0.6b-q8_0
  ```
- **DeepSeek API**:在 `generative_agents/.env` 中配置
  ```
  LLM_API_KEY=你的key
  ```
  并在 `generative_agents/data/config.json` 中切换 `think.llm` 的 provider

### 2. 实时观看

```bash
cd generative_agents
python live.py --name sim-test --start "20250213-09:30" --stride 2 --step 0 --port 5001
```

浏览器打开 http://127.0.0.1:5001/

### 3. 先跑后放(回放)

```bash
python start.py --name sim-test --start "20250213-09:30" --step 10 --stride 10
python compress.py --name sim-test
python replay.py
```

浏览器打开 http://127.0.0.1:5000/?name=sim-test

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
├── start.py        # 模拟(无头)
├── live.py         # 实时模拟+可视化(SSE)
├── compress.py     # 压缩回放数据
├── replay.py       # 回放服务
├── modules/        # 核心逻辑(agent/memory/prompt/model)
├── frontend/       # 可视化前端(Phaser)
├── data/           # 配置与提示词
└── results/        # 存档与回放数据
```

## 说明

- 角色与场景配置见 `docs/角色设定采集模板.md`(给业务方填写)
- 换用英文界面/提示词:改 `modules/prompt/scratch.py` 与前端文案即可,逻辑无需改动


## 修改地图

由于wounderland项目原作者没有提供maze.json的生成代码，所以想要创建新地图，有以下几种方案：

1. 参考原始generative_agents项目中maze.py的逻辑，修改现有代码，以便兼容tiled编辑器导出的json和csv数据文件；
2. 参考现有的maze.json格式，编写代码用于合并tiled编辑器导出的maze_meta_info.json、collision_maze.csv、sector_maze.csv等文件，为新地图生成maze.json。
3. `jiejieje`已为本项目开发了一款地图标注工具，项目地址：https://github.com/jiejieje/tiled_to_maze.json

## 5. 参考资料

### 5.1 论文

[Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)

### 5.2 代码

[Generative Agents](https://github.com/joonspk-research/generative_agents)

[wounderland](https://github.com/Archermmt/wounderland)
