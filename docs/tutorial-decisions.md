# 教程:决策导出

本教程讲解**决策事件导出**——把模拟过程的每个 Agent 动作沉淀为结构化事件流
(`decisions.json`),供决策平台 / 专家界面分析。这是"AI 价值形成过程可解释"的关键一环。

## 决策事件长什么样

每条决策事件 = 时间 + 角色 + 职位 + 动作 + 地址 + 涉他 + 重要性:

```python
{
  "id": "e-0001",
  "step": 1,
  "time": "20250213-09:32",
  "agent": "沈砚之",
  "role": "首席投资顾问",
  "action": "正在整理客户的资产配置方案",
  "location": "投资咨询中心,资料室",
  "predicate": "正在",
  "poignancy": 5,          # 事件重要性分
  "involves": [],          # 涉他(对话/协作对象)
  "has_conversation": False,
  "category": None,        # 分类(平台全权,导出留空)
  "risk_level": None,      # 风险等级(平台全权,导出留空)
  "tags": []
}
```

## 导出决策事件流

`export_decision_stream` 从存档目录生成完整事件流 JSON:

```python
import mavisframework as mf

out_path = mf.export_decision_stream(
    checkpoints_folder="results/checkpoints/invest-live",
    output_path="results/decisions/invest-decisions.json",
    simulation="invest-live",
    stride=2,
    roles={"沈砚之": "首席投资顾问", "老周": "资深散户"},   # 业务层提供职位
)
print("导出到:", out_path)
```

**输出结构**(DecisionEventStream):

```python
{
  "simulation": "invest-live",
  "start_time": "20250213-09:30",
  "stride": 2,
  "total_steps": 12,
  "events": [ ... ]   # DecisionEvent 列表,按存档顺序
}
```

**关键点**:

- `checkpoints_folder` 里每个 `simulate-*.json` 对应一个 step,events 按存档顺序生成
- 对话参与者的提取:`conversation.json` 里记录"谁在什么时间说了什么",
  `involves` 从对话双方提取(涉他)
- `category`/`risk_level` 由决策平台分类,框架导出时留空——框架只负责"发生了什么"

## 涉他与对话

`has_conversation` / `involves` 反映"这件事是否涉及他人":

```python
# conversation.json 示例
{
  "20250213-09:32": [
    {"老周 -> 沈砚之 @ 投资咨询中心:资料室": [["老周", "帮我看看这个仓位"], ["沈砚之", "好的"]]}
  ]
}
```

这段对话会让 09:32 这个时间点的事件:
- `involves` = `["老周", "沈砚之"]`
- `has_conversation` = `True`

## 结合 Simulator 自动导出

在 `Simulator` 构造时打开 `export_decisions=True`,每轮模拟自动生成决策导出:

```python
sim = mf.Simulator(
    max_workers=2,
    export_decisions=True,      # 开启自动导出
    story=story,
)
# simulate 完成后,存档目录旁生成 decisions.json(路径由模拟名决定)
```

## 决策平台怎么用

决策平台/专家界面导入 `decisions.json` 后:

1. **按角色过滤**:`agent` + `role` 字段
2. **按时间切片**:`time` / `step` 字段构建时间线
3. **识别涉他事件**:`has_conversation` / `involves` 非空 = 涉及协作/对话的决策
4. **分类与风险**:`category` / `risk_level` 由平台填充(框架留空)
5. **重要性排序**:`poignancy` 分数

## 小结

- 决策导出把模拟过程变成结构化事件流(时间/角色/动作/涉他/重要性)
- `export_decision_stream` 从存档生成;`export_decisions=True` 可自动导出
- 框架负责"记录发生了什么";分类与风险由决策平台完成
- `involves`/`has_conversation` 反映涉他性,是"决策留痕"的核心信号

---

## 教程索引

- [配置加载与校验](tutorial-config.md)
- [运行时:Game 与 Simulator](tutorial-game.md)
- [消息协议](tutorial-protocol.md)
- 决策导出(本文)
