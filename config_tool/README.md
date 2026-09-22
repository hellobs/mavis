# config_tool — MAVIS 角色配置工具

**引擎侧的配套工具**:业务方通过网页表单填写角色/关系/剧情/场景,工具按 MAVIS 的
Schema 生成标准 JSON/YAML 配置,经校验后写入引擎加载目录。它**需要引擎的配合**——
引擎注册表与 schema 校验、场景运行自检都来自引擎包;引擎包的位置由环境变量
`CASE_ENGINE_DIR` **显式声明**(本工具不探测兄弟目录)。不设置也能启动,但引擎相关
功能(「引擎」页、「组合」页的运行、引擎 schema 深度校验)会**置灰并在页面上给出原因**。

## 定位

- **独立服务**:不依赖仿真服务(live_fastapi),只做配置生成与场景编排
- **Schema 单一来源**:复用 MAVIS 的 validator 与引擎的 scenario schema,避免双份维护
- **确定性映射**:表单字段一一对应 JSON/YAML,不做 AI 解析(保证配置可靠)
- **角色/关系/剧情三者独立**:分别录入,互不耦合
- **显式依赖**:引擎与平台目录只认环境变量声明,找不到就**置灰 + 报原因**,绝不静默降级

## 启动

```bash
# 依赖:fastapi + uvicorn,以及能 import 到 mavisframework(同仓库源码即可)
# 引擎包位置由 CASE_ENGINE_DIR 显式声明(= 引擎包 case_engine/ 的父目录)

# 例 1(Windows,本机并列仓库布局,引擎功能可用):
cd D:\zzr\mavis\config_tool
set CASE_ENGINE_DIR=D:\zzr\provenance\provenance
python app.py

# 例 2(不设 CASE_ENGINE_DIR):工具照常启动,引擎功能置灰并显示原因
cd D:\zzr\mavis\config_tool
python app.py
```

服务地址:http://127.0.0.1:8060/

启动时会把**实际解析到的路径**打印在控制台(引擎目录 / 平台根 / 资源根 / 场景目录 / 地图),
成功与失败都可见:

```
[engine] 引擎可用:CASE_ENGINE_DIR = D:\zzr\provenance\provenance(来源:参数)
[platform] 平台根 = D:\zzr\provenance\provenance
[platform] 资源根 = ...\frontend\static\assets\village ; 场景目录 = ...\scenarios ; 地图 = ...\case00\scenario\maze.json
```

未设置时:

```
[engine] 引擎不可用:未设置环境变量 CASE_ENGINE_DIR(引擎相关功能不可用)(...)
[platform] 平台根 = (未声明)
```

> 端口说明:8060 专用于 config 工具;5001(live 服务)与 5002(case01 只读数据服务)是平台侧端口,已占用,故本工具常驻 8060 避免冲突。
>
> **改了本工具代码后必须重启进程**:模板是每次请求从磁盘读的(会"热更新"),app.py 不会;
> 只重启模板不重启进程,会出现"模板比进程新"的错配(页面上会有明确横幅提示重启,不会白屏)。

## 页面

| 路径 | 功能 | 依赖引擎? |
|---|---|---|
| `/scenario` | 场景创建器(默认入口;填表 → `scenario.yaml` + 完整内容资产) | 可用时做 schema 深度校验;不可用退回基础校验 |
| `/engines` | 引擎清单(只读:有哪些引擎、各自能跑哪些场景) | **是**,不可用时置灰并显示原因 |
| `/composition`(`/run` 兼容) | 组合(场景 × 引擎)登记与运行自检 | **是**,不可用时运行按钮不可用 |
| `/agents`、`/relationships`、`/story` | 旧版独立录入页(保留兼容) | 否 |

顶部菜单导航,当前页高亮。必填字段标红色 `*`,选填标灰色 `(选填)`。
引擎/平台目录未声明时,每页顶部会显示黄色横幅说明缺什么、怎么设置(不静默)。

**导出配置**:导航栏右侧"导出配置 (.zip)"按钮(或 `GET /api/export`)把当前所有已配置
内容打包下载,zip 结构:

```
agents/<角色名>/agent.json + portrait.png + texture.png
scenarios/<业务>/relationships.json + story.json
```

> 适合把配置发给他方合并(打包后直接发送,无需逐文件寻找)。

> 「配置角色」的字段清单(含填入类型、必填标记、可选范围)见独立文档:**[角色字段清单.md](角色字段清单.md)**,供需求方/字段提供方使用,不含技术细节。

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

**路径声明(全部显式,不探测兄弟目录)**:

| 环境变量 | 含义 | 不设置时 |
|---|---|---|
| `CASE_ENGINE_DIR` | **引擎包所在目录**(`case_engine/` 的父目录) | 引擎功能全部置灰,页面显示"引擎功能不可用:未设置 CASE_ENGINE_DIR" |
| `MAVIS_PLATFORM_DIR` | 平台仓根(产物落盘/地图/实时入口联动) | 沿用 `CASE_ENGINE_DIR`(平台仓当前布局下二者为同一目录) |
| `MAVIS_ASSETS_ROOT` | 平台前端资源根(`frontend/static/assets/village`) | 由平台根推导 |
| `MAVIS_SCENARIOS_DIR` | 平台场景目录(`scenarios`) | 由平台根推导 |
| `MAVIS_MAZE_PATH` | 默认地图文件 | 优先平台根下 `case00/scenario/maze.json`,否则资源根下 `maze.json` |
| `CASE_ENGINE_CASES_ROOT` | 场景根目录(引擎侧同名变量) | 平台根下 `cases/` |

> 2026-09-22 变更:以前 config_tool 会**探测**兄弟目录 `../provenance` 来定位平台与引擎。
> 探测靠猜、失败还静默降级,已移除;现在只认上表的显式声明。平台目录未声明时,产物
> 不会写进当前工作目录 —— 保存接口直接返回可读错误。

**角色产物附加处理**:
- 自动补 `portrait` 字段,并从贴图池(`agents_pool/`,25 人历史贴图)按角色名哈希映射贴图
- agent.json 记录 `texture_ref`(贴图来源,供 Unity 端同样处理)

> 提示:提交角色后,重启仿真服务(5001)即可让新角色进入模拟;
> 刷新 http://127.0.0.1:5001 可在画面中看到新角色。

## API

| 接口 | 说明 |
|---|---|
| `POST /api/generate` | 表单数据 → 生成 agent.json → 校验 → 写入 agents 目录 |
| `POST /api/upgrade` | 升级现有角色:读旧 agent.json,补全缺失字段(如老角色补 role_type/duty/initial_tendency,清理旧 goals) |
| `POST /api/relationship` | 追加一条关系(必填:两个角色、关系类型) |
| `POST /api/relationship/delete` | 按行号 index 删除关系 |
| `POST /api/story` | 追加一条剧情(必填:time/event_type/content;time 须为 00:00-23:59) |
| `POST /api/story/delete` | 按 id 删除剧情 |
| `POST /api/agent/delete` | 按角色名删除角色目录(agent.json + 贴图),防路径穿越 |
| `POST /api/scenario/preview` | 场景表单 → `scenario.yaml` 文本 + 校验结果(不落盘);返回 `engine_available` / `engine_reason` |
| `POST /api/scenario/save` | 校验通过后写入 `cases/<case_id>/`;平台目录未声明时返回可读错误 |
| `GET /api/scenario/load` | 按 case_id 反解析回表单 |
| `GET\|POST\|PATCH\|DELETE /api/compositions` | 组合(场景 × 引擎)记录的增删改查 |
| `POST /api/run/execute` | 跑一次组合(引擎不可用时返回 `引擎不可用: <原因>`) |

## 配置校验

生成/升级都会调用 MAVIS 的 `mavisframework.config.validator`,校验:
- 语法(必填字段/类型)
- 地图一致性(coord 范围、spatial 地址存在于地图)
- 角色交叉(relationships/story 引用存在)

校验失败返回具体错误清单,不会写入坏配置。

## 设计说明

- **与引擎的接触面只有一处**:`engine_bridge.py`。引擎包名、`sys.path` 注入、可用性判定
  与全部引擎调用都收敛在这个模块;`app.py` / `scenario_builder.py` / `engine_runner.py` /
  `compositions.py` 只调它的函数(2026-09-22 收口,反向依赖引用点 57 → 5,涉及文件 4 → 2)。
  provenance 侧有棘轮测试 `tests/test_mavis_purity.py::test_config_tool_reverse_dependency_does_not_grow` 盯着只准降。
- **不允许静默**(与产线铁律一致):引擎不可用 → 页面置灰 + 原因可见;地图缺失 → 接口返回
  可读错误而不是空数组;`governance.json` 拿不到引擎口径 → 返回值里标 `used_engine=False`。
- 角色配置是"三层":行为层(人设/关系/剧情)+ 制度层(组织/职责/权限/规则)+ 价值层(人物初始底色 initial_tendency;制度约束 governance.json 由治理面板维护)
- 关系 → `scenarios/<business>/relationships.json`,剧情 → `scenarios/<business>/story.json`,与角色独立维护
- 迁移 Unity 时:角色→贴图的映射依赖需在 Unity 端同样处理(读 `texture_ref`)
- **本工具仍属 mavis 仓**:它对引擎/平台的依赖是**运行期配置**(环境变量声明),不是代码级
  反向依赖;把工具整体搬进平台仓是更大的动作,需要单独拍板。

## 相关仓库与工具

| 组件 | 位置 | 说明 |
|---|---|---|
| 仿真引擎(内核) | [hellobs/mavis](https://github.com/hellobs/mavis)(本仓库) | `mavisframework`,本工具依赖其 validator |
| 引擎包 + 演示平台 | [hellobs/provenance](https://github.com/hellobs/provenance) | `CASE_ENGINE_DIR` 指向这里的 `provenance/`(引擎包 `case_engine/` 所在目录);产物写入 `agents/`、`scenarios/` |
| 地图转换工具 | provenance `tools/tilemap_to_maze.py` | Tiled 地图 → maze.json(换场景用) |
| Unity 版前端(已冻结) | [hellobs/Multi-Model-AI-Visualization-and-Interactive-Simulation-Platform](https://github.com/hellobs/Multi-Model-AI-Visualization-and-Interactive-Simulation-Platform) | 消费同一 WebSocket 契约,读取 `texture_ref` |
