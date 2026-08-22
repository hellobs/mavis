# 角色定义模板(标准化 JSON)

本文档定义 MAVIS 框架读取的角色配置格式,供业务方/后端同学填写、AI 生成、框架校验加载。

**配置一个人 = 三层**:
- **行为层**(让 AI 像人、有故事):人设 / 关系 / 剧情
- **制度层**(让 AI 在组织里办事):组织 / 职责 / 权限 / 规则
- **价值层**(承载"AI 向善向上"叙事):目标权重 / 价值观 / 可干预点

> 制度层与价值层是 GTC"AI 价值形成过程可被人为改变"演示的核心——它们定义了"AI 的目标是什么、受什么规则约束、人能怎么影响它"。

## 一、文件清单

| 文件 | 作用 | 是否可 AI 生成 |
|---|---|---|
| `agents/<角色名>/agent.json` | 每个角色的完整定义(一个角色一个目录) | 是(核心) |
| `relationships.json` | 角色间关系(可选) | 是(可选) |
| `story.json` | 剧情事件/危机注入(可选) | 是(可选) |
| `maze.json` | 地图(格子/地址/碰撞) | 否(复用,地址是约束) |

**关键约束:角色配置里的"地址"和"坐标"必须与地图(maze.json)一致**,否则校验失败。

## 二、agent.json(核心,一个角色一份)

```json
{
  "name": "角色名(全场景唯一)",
  "role_type": "user",                 // ★ user=普通用户 / ai_tool=AI 工具角色(GTC 固定席位)
  "coord": [10, 6],
  "currently": "角色当前状态的一句话描述",

  "organization": "投资咨询中心",        // ★ 制度层:所属组织
  "duty": {                            // ★ 制度层:职责/权限/规则
    "position": "首席投资顾问",
    "responsibility": ["资产配置", "投资决策", "客户沟通"],
    "authority": [
      "可批准 100 万以内的投资",
      "不可单独批准风控例外"
    ],
    "rules": [
      "必须风控签字后放款",
      "止损线 -10%",
      "不承诺保本收益"
    ]
  },

  "goals": {                           // ★ 价值层:目标权重(总和 1.0)
    "收益最大化": 0.6,
    "风险规避": 0.4
  },
  "values": {                          // ★ 价值层:价值观底线/偏好
    "bottom_line": ["不内幕交易", "不误导客户", "不隐瞒风险"],
    "preferences": ["价值投资", "长期主义"]
  },
  "intervention": {                    // ★ 价值层:人的介入方式(承载"向善向上")
    "by_dialogue": ["用户可对话纠正投资建议"],
    "by_directive": ["可注入监管指令改变目标权重"],
    "by_review": ["专家可评判决策,反馈纳入记忆"]
  },

  "scratch": {                         // 行为层:人设
    "age": 35,
    "innate": "先天性格,如:谨慎、重视数据、情绪化",
    "learned": "后天习得,如:每天复盘交易记录",
    "lifestyle": "生活习惯,如:早睡早起、每天看盘",
    "daily_plan": "日常计划概述"
  },
  "spatial": {                         // 行为层:空间(睡觉地点/空间树)
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

**通用字段**:
- `name`(必填):字符串,全场景唯一。
- `role_type`(必填):`"user"`(普通用户)或 `"ai_tool"`(AI 工具角色)。**GTC 演示必须保留至少一个 `ai_tool` 角色**(它是被其他角色使用、价值可被改变的 AI)。
- `coord`(必填):`[x, y]` 整数数组,必须在 `maze.json` 的 `size` 范围内(当前 `[24, 27]`)。
- `currently`(必填):字符串,开场状态描述。
- `scratch`(必填):对象,含 `age`(整数)、`innate`、`learned`、`lifestyle`、`daily_plan`。
- `spatial`(必填):对象,含 `address` 和 `tree`;地址必须存在于 maze.json(见"地图地址表")。

**制度层字段(可选,但企业推演建议填)**:
- `organization`(可选):所属组织名称。
- `duty`(可选):对象,含 `position`(岗位)、`responsibility`(职责数组)、`authority`(权限数组)、`rules`(规则数组)。
  - `authority`:定义"能做什么/不能做什么",框架可据此做权限检查。
  - `rules`:定义红线/合规约束,框架可据此做规则检查。

**价值层字段(可选,承载"AI 向善向上"演示)**:
- `goals`(可选):对象,目标 → 权重(0~1),**权重总和应为 1.0**。AI 决策时目标权重影响其权衡。
- `values`(可选):对象,含 `bottom_line`(价值观底线数组)、`preferences`(偏好数组)。
- `intervention`(可选):对象,定义"人的介入方式",含 `by_dialogue`(对话纠正)、`by_directive`(指令注入)、`by_review`(专家评判)。

### 地图地址表(生成 spatial 时只能用这些地址)

当前 `maze.json` 存在的地址层级:

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

> 换业务场景时换成该场景的 maze.json 地址表。

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

字段约束:`relations`(必填)数组;每条 `agents`(必填,两个角色名,必须存在于 agent.json)、`type`(必填)、`direction`(可选)、`trigger`(可选)、`frequency`(可选,`high/medium/low`)。

## 四、story.json(可选,定义剧情事件/危机注入)

```json
{
  "events": [
    {
      "id": "s-001",
      "time": "10:00",
      "event_type": "市场波动",
      "content": "新能源板块盘中大幅波动，监管要求机构24小时内评估组合风险",
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

字段约束:`events`(必填)数组;每条 `id`(必填,唯一)、`time`(必填,`HH:MM`)、`event_type`(必填)、`content`(必填)、`targets`(可选,`"all"` 或角色名数组)、`expected`(可选)、`importance`(可选,1-10,默认 10)。

条件触发(可选):`condition` 支持 `{"type":"poignancy","role":"<角色>","min":<整数>}`(重要性达标触发)和 `{"type":"at_location","role":"<角色>","address":"<地址关键词>"}`(到达位置触发)。有 condition 时 time 可省略。

## 五、生成与校验闭环

```
业务方/后端填写 或 AI 按本模板生成
   → agent.json(×N) + relationships.json + story.json
   → 框架配置校验器(framework.config.validator)逐层校验:
       语法 → 地图一致性 → 角色交叉引用 → (新增:role_type/goals 权重和等)
   → 校验通过 → 加载运行
   → 校验失败 → 返回具体错误清单
```

**说明**:当前框架已实现行为层(人设/关系/剧情)的加载与生效;制度层(职责/权限/规则)与价值层(目标/价值观/干预)的**字段已在本模板定义,框架侧的"约束检查/目标引导"机制为下一步开发项**——先按模板填数,机制随后接入。
