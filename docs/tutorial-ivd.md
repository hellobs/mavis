# 教程:IVD 价值治理层(制度 → 体验 → 内化 → 审计)

> 面向接入方的教学文档:如何用 mavisframework 的价值治理层,以及如何**测试**它。
> 前置阅读:`tutorial-extension.md`(扩展面契约)、`tutorial-decisions.md`(决策导出)。

## 一、这张图先看懂:四层杠杆

IVD(Internal Value Development,价值内生发展)的核心主张是:对 AI 的治理
不靠直接改行为,而是**改它所处的制度环境,让价值在经历中内生**。四层杠杆:

| 层 | 载体 | 谁来操作 | 生效速度 |
|---|---|---|---|
| 制度层 | `governance.json`(期望目标权重) | 专家/治理者 | 慢(要等新体验累积) |
| 内容层 | story 事件 / 对话(`interaction_request` 钩子) | 场景/注入方 | 中 |
| 行为层 | `ConsequenceEngine` 后果反馈 | 自动运行 | 即时~中 |
| 审计层 | `interventions.json` + `decisions.json` + 倾向曲线 | 只读 | 事后 |

一句话记住分层语义:**权重定"往哪偏",体验给"具体怎么偏",审计让全程可查。**
只有权重没有体验,内化不发生;只有体验没有权重,内化无方向。

## 二、制度层:governance.json

```json
{
  "roles": {
    "AI Advisor": {
      "Serve Users": 0.35, "Compliance Rigor": 0.3,
      "Risk Control": 0.2, "Data Rigor": 0.15
    }
  }
}
```

要点:
- 目标名是**行为绑定**的——起名时保证 embedding 打分能区分(如"Risk Control"
  能被"压力测试客户资产"这类行动文本对上);
- 权重是**期望不是控制**:永远不进 prompt、不强制重生成,只加权后果反馈;
  专家调整通过"后续体验"被 agent 感受到(滞后收敛 = 内化证据);
- `Σ权重 = 1`(相对偏好,表达不了硬红线——rules 红线层是已知的扩展方向)。

API(`mavisframework.runtime.governance.Governance`):
`load(path)` / `save()` / `get_constraints(name)` / `all_constraints()` /
`set_constraints(name, goals)`(内部即 save)。**接入方注意**:`save()` 是普通
整文件写——高频写入请自行加锁/原子化(见 §六)。

## 三、行为层:ConsequenceEngine(后果反馈)

```python
from mavisframework.runtime.consequence import ConsequenceEngine
engine = ConsequenceEngine()          # 或 ConsequenceEngine(scorer=你的打分器)
feedback = engine.feedback(agent, "压力测试了客户的投资组合")
# → {"Risk Control": 0.41, "Data Rigor": 0.33, ...}   (softmax 占比 × 约束权重)
```

- 打分 = 行动文本与各目标名的 **embedding 余弦相似度**(GoalScorer,默认走
  本地 Ollama `qwen3-embedding:0.6b-q8_0`),softmax 相对化后乘约束权重;
- 负相似度保留:行动明确违背某目标时,该目标反馈占比小(内化弱),而非截断为 0;
- **embedding 不可用时降级为中性反馈**(= 约束权重),并计入 `engine.health()`
  的 degraded 计数——降级可观测,不静默;持续降级会让倾向曲线出现"平段";
- 打分器可注入(`scorer=` 参数),测试时传假打分器即可零网络。

## 四、agent 侧:价值倾向如何被更新

`agent.attach_governance(governance, consequence_fn)` 之后,每次行动的
`observe_consequence(action_desc)` 会:

1. 采样:行动**变化点**立即入窗,同一行动每 `think.tendency_refresh`(默认 5)
   步刷新一次(持续做 = 持续强化,曲线不冻结);
2. 滑窗:窗口记录 `{action, alignment, feedback, time}` 三件套,容量
   `think.tendency_window`(默认 15),按**模拟时间**做 recency 衰减
   (`decay_per_hour ** age_hours`,默认 0.6/时);
3. 混合:`tendency = α·persona + (1−α)·experience`,
   `α = max(0.1, 1 − 累计体验数 / 窗口容量)`(0.1 下限 = 10% 性格残余;
   过渡期 ≈ 装满一个记忆窗口);
4. **约束过滤**:制度删除的目标从倾向与窗口中一并清除(不留"影子目标");
5. **归一化**:Σ(value_tendency) ≡ 1(性质测试钉死,最大误差 1e-9 量级);
6. 审计:`status["tendency_meta"]`(α/窗口/观测数)与
   `status["tendency_window"]` 随 checkpoint 持久化,`--resume` 连续。

**已固化的数学性质**(`tests/test_ivd_regression.py`):
Σ=1 守恒、α 触底 0.1、被删维度消退至 0。改倾向更新逻辑前先读这三个测试。

## 五、干预与审计:expert 动了什么,记录到哪

```json
{
  "time": "2026-09-27 10:00:00",        // 墙钟(排序兜底)
  "sim_time": "20250213-12:02",         // 模拟时刻(曲线画竖线用)
  "simulation": "stock-en8",            // 归属模拟(跨模拟隔离)
  "agent": "AI Advisor",
  "old_constraints": {...}, "new_constraints": {...},
  "operator": "expert",                 // 撤销时为 "undo"(原记录打 revoked)
  "note": "专家理由(建议必填——干预语义分析靠它)",
  "intervention": "goals"               // 干预模态标记(模态对比研究用)
}
```

写路径注意:**内存同步是关键**——`agent._governance` 指向 `game.governance`
同一对象,只改文件不改内存,后果反馈仍按旧约束算,倾向曲线"不动"。
撤销(undo)语义:回滚到 old_constraints,原记录打 `revoked`,撤销本身也入链
(不抹除历史)。

## 六、测试模式:密封(不依赖 Ollama)是硬要求

IVD 链路涉及 embedding 打分,而 Ollama 可能不在线。**教训**:某回归测试
隐式构造了真 GoalScorer,Ollama 在线时 2s、离线时每个文本等死超时
(整套件 641s)。规则:

- 测试中给 agent 注入假打分器,例如:

```python
class _NullScorer:
    def alignment(self, action, goals):   # 与"Ollama 不可达"回退路径等价
        return {}

agent._goal_scorer = _NullScorer()        # 或 ConsequenceEngine(scorer=FakeScorer())
```

- 或者注入假 `consequence_fn`(直接返回 `{goal: feedback}`)——倾向更新的
  采样/混合/归一逻辑照样被覆盖,且完全确定;
- 性质测试(Σ=1、α 下限、维度消退)用随机反馈跑 40 轮即可,见
  `tests/test_ivd_regression.py::TestTendencyMathProperties`。

## 七、运行环境清单

- Ollama + `qwen3-embedding:0.6b-q8_0`(后果反馈打分;不在线 = 全量降级,
  曲线平段——`engine.health()["degrade_rate"]` 会如实暴露);
- LLM(角色思考)可换任意兼容端点;判定/分流类任务建议推理模型把
  `max_tokens` 放大(reasoning 先烧 token,content 可能被挤空)。
