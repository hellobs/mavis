# 角色配置生成 Schema(AI 生成目标)

本文档定义 MAVIS 框架读取的配置文件格式,供自然语言解析(AI/DS 生成角色配置)按此产出。**AI 生成 → 框架校验 → 加载运行** 的闭环中,本 schema 是"生成方必须遵守的目标格式"。

## 一、文件清单

一个场景需要三类文件,加上地图(地图通常不生成,复用现有):

| 文件 | 作用 | 是否可 AI 生成 |
|---|---|---|
| `agents/<角色名>/agent.json` | 每个角色的完整人设(一个角色一个目录) | 是(核心) |
| `relationships.json` | 角色间关系(可选) | 是(可选) |
| `story.json` | 剧情事件/危机注入(可选) | 是(可选) |
| `maze.json` | 地图(格子/地址/碰撞) | 否(复用现有,地址是约束) |

**关键约束:角色配置里的"地址"和"坐标"必须与地图(maze.json)一致**,否则校验失败。生成时只能使用地图中已有的地址。

## 二、agent.json(核心,一个角色一份)

```json
{
  "name": "角色名(必须唯一,不能与其他角色重名)",
  "coord": [10, 6],
  "currently": "角色当前状态的一句话描述",
  "scratch": {
    "age": 35,
    "innate": "先天性格,如:谨慎、重视数据、情绪化",
    "learned": "后天习得,如:每天复盘交易记录",
    "lifestyle": "生活习惯,如:早睡早起、每天看盘",
    "daily_plan": "日常计划概述"
  },
  "spatial": {
    "address": {
      "living_area": ["the Ville", "投资咨询中心", "休息区"]
    },
    "tree": {
      "the Ville": {
        "投资咨询中心": {
          "休息区": ["床"]
        }
      }
    }
  }
}
```

### 字段约束

`name`(必填):字符串,全场景唯一。
`coord`(必填):`[x, y]` 整数数组,必须在 `maze.json` 的 `size` 范围内(当前 `[24, 27]`,即 x∈[0,23], y∈[0,26])。
`currently`(必填):字符串,开场状态描述。
`scratch`(必填):对象,含 `age`(整数)、`innate`、`learned`、`lifestyle`、`daily_plan`(字符串)。
`spatial`(必填):对象,含 `address` 和 `tree`。
- `address.living_area`:角色睡觉的地方,数组格式 `["the Ville", "<区域>", "<房间>"]`
- `tree`:空间树,**每一级地址必须存在于 maze.json**(见下方"地图地址表")

### 地图地址表(生成 spatial 时只能用这些地址)

以下是当前 `maze.json` 存在的地址层级(生成 `spatial.tree` 和 `address` 时,只能使用这些):

- `the Ville:投资咨询中心`(根)
- `the Ville:投资咨询中心:会议室`
- `the Ville:投资咨询中心:会议室:白板`
- `the Ville:投资咨询中心:会议室:会议讲台`
- `the Ville:投资咨询中心:会议室:会议座位`
- `the Ville:投资咨询中心:资料室`
- `the Ville:投资咨询中心:资料室:休息沙发`
- `the Ville:投资咨询中心:资料室:资料桌`
- `the Ville:投资咨询中心:资料室:文件柜`
- `the Ville:投资咨询中心:休息区`
- `the Ville:投资咨询中心:休息区:床`
- `the Ville:投资咨询中心:走廊`

> 若生成其他业务场景,需要换成该场景的 maze.json 地址表。生成器应先读 maze.json,提取地址表,再生成 spatial。

## 三、relationships.json(可选,定义角色关系)

```json
{
  "relations": [
    {
      "agents": ["老周", "沈砚之"],
      "type": "客户-顾问",
      "direction": "老周→沈砚之",
      "trigger": "每天下午3点，老周到会议室找沈砚之咨询行情",
      "frequency": "high"
    }
  ]
}
```

### 字段约束

`relations`(必填):数组。
每条:`agents`(必填,恰好两个角色名,**必须都存在于 agent.json**)、`type`(必填,关系类型)、`direction`(可选)、`trigger`(可选,互动约定)、`frequency`(可选,`high`/`medium`/`low`)。

## 四、story.json(可选,定义剧情事件/危机注入)

```json
{
  "events": [
    {
      "id": "s-001",
      "time": "09:50",
      "event_type": "市场波动",
      "content": "新能源板块盘中大幅波动，监管要求24小时内评估组合风险",
      "targets": ["all"],
      "expected": "各角色评估风险、给出建议",
      "importance": 10,
      "condition": {
        "type": "poignancy",
        "role": "老周",
        "min": 100
      }
    }
  ]
}
```

### 字段约束

`events`(必填):数组。
每条:`id`(必填,唯一)、`time`(必填,`HH:MM` 格式,模拟时钟走到此时触发)、`event_type`(必填)、`content`(必填,事件描述)、`targets`(可选,`"all"` 或角色名数组,**角色名必须存在**)、`expected`(可选,描述性)、`importance`(可选,1-10 整数,默认 10)。

### 条件触发(可选)

`condition`(可选):对象,定义"非时间触发"的条件。当前支持:
- `{"type": "poignancy", "role": "<角色>", "min": <整数>}`:指定角色重要性累计达到 min 时触发
- `{"type": "at_location", "role": "<角色>", "address": "<地址关键词>"}`:指定角色位于含该关键词的地址时触发

有 `condition` 时 `time` 可省略(纯条件触发);两者都填时,条件优先于时间。

## 五、生成与校验闭环

```
自然语言场景描述
   → AI/DS 解析,按本 schema 生成 agent.json(×N) + relationships.json + story.json
   → 框架配置校验器(framework.config.validator)逐层校验:
       语法(字段/类型)→ 地图一致性(coord 范围/spatial 地址)→ 角色交叉(引用存在)
   → 校验通过 → 加载运行
   → 校验失败 → 返回具体错误清单(哪个文件哪个字段),可要求重新生成或人工修正
```

**生成器的责任**:只使用地图存在的地址、只引用已生成的角色名、字段按本 schema 完整给出。**框架的责任**:校验兜底,不合格明确报错,不让坏配置进入运行。
