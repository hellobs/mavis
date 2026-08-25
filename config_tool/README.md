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

**导出配置**:导航栏右侧"导出配置 (.zip)"按钮(或 `GET /api/export`)把当前所有已配置
内容打包下载,zip 结构:

```
agents/<角色名>/agent.json + portrait.png + texture.png
scenarios/<业务>/relationships.json + story.json
```

> 适合把配置发给他方合并(打包后直接发送,无需逐文件寻找)。

> 「配置角色」的字段清单(含填入类型、必填标记、可选范围)见独立文档:**[角色字段清单.md](角色字段清单.md)**,供陈总/字段提供方使用,不含技术细节。

## 生成结果(产物在哪)

填表提交后,产物自动写入**平台(Provenance)仓库**的对应目录:

| 填什么 | 产物 | 落盘位置(相对平台仓库) |
|---|---|---|
| 角色 | `agent.json` + `portrait.png` + `texture.png` | `frontend/static/assets/village/agents/<角色名>/` |
| 关系 | `relationships.json`(追加) | `scenarios/<business>/relationships.json` |
| 剧情 | `story.json`(追加) | `scenarios/<business>/story.json` |

在本机(仓库并列布局)的具体路径:

```
D:\zzr\provenance\provenance\frontend\static\assets\village\agents\<角色名>\agent.json
D:\zzr\provenance\provenance\scenarios\investment\relationships.json
D:\zzr\provenance\provenance\scenarios\investment\story.json
```

**路径探测规则**:config_tool 启动时自动探测兄弟目录 `../provenance`(平台仓库),
优先找含 `frontend/` 的子目录(当前为 `../provenance/provenance/`);找不到时回退仓库根。
部署时可用环境变量显式指定:

- `MAVIS_ASSETS_ROOT` — 平台前端资源根(`frontend/static/assets/village`)
- `MAVIS_SCENARIOS_DIR` — 平台场景目录(`scenarios`)

**角色产物附加处理**:
- 自动补 `portrait` 字段,并从贴图池(`agents_pool/`,25 人历史贴图)按角色名哈希映射贴图
- agent.json 记录 `texture_ref`(贴图来源,供 Unity 端同样处理)

> 提示:提交角色后,重启仿真服务(5001)即可让新角色进入模拟;
> 刷新 http://127.0.0.1:5001 可在画面中看到新角色。

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
