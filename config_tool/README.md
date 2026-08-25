# config_tool — MAVIS 角色配置工具

独立于仿真引擎的角色配置生成工具。业务方通过网页表单填写角色/关系/剧情,工具按 MAVIS 的 Schema 生成标准 JSON 配置,经校验后写入引擎加载目录。

## 定位

- **独立服务**:不依赖仿真引擎(live_fastapi),只做配置生成
- **Schema 单一来源**:复用 MAVIS 的 validator,避免双份维护
- **确定性映射**:表单字段一一对应 JSON,不做 AI 解析(保证配置可靠)
- **角色/关系/剧情三者独立**:分别录入,互不耦合

## 启动

```bash
# 依赖 MAVIS 的 uv 环境(fastapi 等);激活后直接 python
# 或指定解释器:Windows .venv-live\Scripts\python.exe / mac·linux .venv-live/bin/python
cd config_tool
python app.py
```

服务地址:http://127.0.0.1:5002/

## 页面

| 路径 | 功能 |
|---|---|
| `/` | 角色配置(填表生成新角色) |
| `/relationships` | 关系录入(追加关系到 relationships.json,可查看/删除) |
| `/story` | 剧情录入(追加事件到 story.json,可查看/删除) |
| `/agents` | 已配置角色列表(点开看完整详情,可删除角色) |

顶部菜单导航,当前页高亮。必填字段标红色 `*`,选填标灰色 `(选填)`。

> 「配置角色」的字段清单(含填入类型、必填标记、可选范围)见独立文档:**[角色字段清单.md](角色字段清单.md)**,供陈总/字段提供方使用,不含技术细节。

## 生成结果

- 写入平台资源目录 `../provenance/provenance/frontend/static/assets/village/agents/<角色名>/agent.json`
- 自动补 `portrait` 字段,并从贴图池(`agents_pool/`,25 人历史贴图)按角色名哈希映射贴图
- agent.json 记录 `texture_ref`(贴图来源,供 Unity 端同样处理)

> 产物路径默认指向兄弟目录 `../provenance`(本地开发时 mavis 与 provenance 仓库并列);
> 部署时用环境变量覆盖:
> - `MAVIS_ASSETS_ROOT` — 平台前端资源根(`frontend/static/assets/village`)
> - `MAVIS_SCENARIOS_DIR` — 平台场景目录(`scenarios`)

## API

| 接口 | 说明 |
|---|---|
| `POST /api/generate` | 表单数据 → 生成 agent.json → 校验 → 写入 agents 目录 |
| `POST /api/upgrade` | 升级现有角色:读旧 agent.json,补全缺失字段(如老角色补 role_type/duty/goals) |
| `POST /api/relationship` | 追加一条关系(必填:两个角色、关系类型) |
| `POST /api/relationship/delete` | 按行号 index 删除关系 |
| `POST /api/story` | 追加一条剧情(必填:time/event_type/content;time 须为 00:00-23:59) |
| `POST /api/story/delete` | 按 id 删除剧情 |
| `POST /api/agent/delete` | 按角色名删除角色目录(agent.json + 贴图),防路径穿越 |

## 配置校验

生成/升级都会调用 MAVIS 的 `mavisframework.config.validator`,校验:
- 语法(必填字段/类型)
- 地图一致性(coord 范围、spatial 地址存在于地图)
- 角色交叉(relationships/story 引用存在)

校验失败返回具体错误清单,不会写入坏配置。

## 设计说明

- 角色配置是"三层":行为层(人设/关系/剧情)+ 制度层(组织/职责/权限/规则)+ 价值层(目标)
- 关系 → `scenarios/<business>/relationships.json`,剧情 → `scenarios/<business>/story.json`,与角色独立维护
- 迁移 Unity 时:角色→贴图的映射依赖需在 Unity 端同样处理(读 `texture_ref`)
