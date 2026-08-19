# 业务层:投资场景(investment)

业务层 = 框架的"输入",换业务只改这里,框架层零改动。

## 目录说明

```
scenarios/investment/
├── relationships.json   # 角色关系(邻接表):谁找谁、何时、频率
├── story.json           # 剧情事件(危机注入):时间/类型/影响/期望行为
├── agents/              # (可选)角色 agent.json 独立副本
│                        #   当前复用 frontend/static/assets/village/agents/
└── scene/               # (可选)场景 maze.json 独立副本
                         #   当前复用 frontend/static/assets/village/maze.json
```

## 当前角色(5)

| 角色 | 职位 | 关系 |
|---|---|---|
| 沈砚之 | 首席投资顾问 | 老周咨询对象、林晚晴汇报对象 |
| 苏清越 | 量化交易分析师 | 与陈慕白数据协作 |
| 陈慕白 | 行业研究员 | 与苏清越交叉验证 |
| 林晚晴 | 风控合规专员 | 向沈砚之提交风险评级 |
| 老周 | 资深散户投资者 | 主动找沈砚之咨询 |

## 关系配置如何生效(规划)

1. `relationships.json` → 注入相关角色 daily_plan("下午3点找沈砚之")→ 空间共现
2. 可选强约束:`frequency=high` 的关系,对话决策直接放行(必聊)

## 剧情配置如何生效(规划)

`story.json` → 指定模拟时间往环境注入事件 → agent 感知 → 触发反应(评估/开会/对话)
