# config_tool — MAVIS 角色配置工具

独立于仿真引擎的角色配置生成工具。业务方通过网页表单填写角色信息,工具按 MAVIS 的 Schema 生成标准 JSON 配置,经校验后写入引擎加载目录。

## 定位

- **独立服务**:不依赖仿真引擎(live_fastapi),只做配置生成
- **Schema 单一来源**:复用 MAVIS 的 validator,避免双份维护
- **确定性映射**:表单字段一一对应 JSON,不做 AI 解析(保证配置可靠)
- **学术朴素**:纯表单界面,无装饰,面向业务方/后端填数

## 启动

```bash
# 依赖 MAVIS 的 uv 环境(fastapi 等)
cd config_tool
../generative_agents/.venv-live/Scripts/python.exe app.py
```

服务地址:http://127.0.0.1:5002/

## 页面

| 路径 | 功能 |
|---|---|
| `/` | 角色配置表单(填表生成新角色) |
| `/agents` | 已配置角色列表(点开看完整详情) |

## 表单区块

1. **角色基本信息**:业务名/角色类型(user 或 ai_tool)/角色名/组织/坐标/所在区域/当前状态
2. **职责与权限**:岗位/职责/权限/规则
3. **目标**:目标权重(如"收益最大化:0.6\n风险规避:0.4",总和应为 1)
4. **人设与习惯**:年龄/性格/习得/习惯/日常

> 表单内容自动保存草稿(localStorage),刷新不丢失;"清空草稿"按钮可重置。

## 生成结果

- 写入 `generative_agents/frontend/static/assets/village/agents/<角色名>/agent.json`
- 自动补 `portrait` 字段,并从贴图池(`agents_pool/`,25 人历史贴图)按角色名哈希映射贴图
- agent.json 记录 `texture_ref`(贴图来源,供 Unity 端同样处理)

## API

| 接口 | 说明 |
|---|---|
| `POST /api/generate` | 表单数据 → 生成 agent.json → 校验 → 写入 agents 目录 |
| `POST /api/upgrade` | 升级现有角色:读旧 agent.json,补全缺失字段(如老角色补 role_type/duty/goals) |

## 配置校验

生成/升级都会调用 MAVIS 的 `framework.config.validator`,校验:
- 语法(必填字段/类型)
- 地图一致性(coord 范围、spatial 地址存在于地图)
- 角色交叉(relationships/story 引用存在)

校验失败返回具体错误清单,不会写入坏配置。

## 设计说明

- 角色配置是"三层":行为层(人设/关系/剧情)+ 制度层(组织/职责/权限/规则)+ 价值层(目标)
- 本工具当前覆盖:行为层的人设 + 制度层 + 目标(价值层中的"可干预方式"按需求暂不配置)
- 迁移 Unity 时:角色→贴图的映射依赖需在 Unity 端同样处理(读 `texture_ref`)
