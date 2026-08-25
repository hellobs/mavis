# Tutorial: Decision Export

This tutorial covers **decision event export** — turning every agent action
during a simulation into a structured event stream (`decisions.json`) for
governance platforms / expert UI. This is the key to "AI value formation is
observable".

## What a Decision Event Looks Like

Each event = time + agent + role + action + location + involves + importance:

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
  "poignancy": 5,          # event importance score
  "involves": [],          # others involved (dialogue/collaboration)
  "has_conversation": False,
  "category": None,        # classification (platform-owned, left empty by export)
  "risk_level": None,      # risk level (platform-owned, left empty)
  "tags": []
}
```

## Exporting the Decision Event Stream

`export_decision_stream` generates the full event-stream JSON from a
checkpoint folder:

```python
import mavisframework as mf

out_path = mf.export_decision_stream(
    checkpoints_folder="results/checkpoints/invest-live",
    output_path="results/decisions/invest-decisions.json",
    simulation="invest-live",
    stride=2,
    roles={"沈砚之": "首席投资顾问", "老周": "资深散户"},   # roles provided by the business layer
)
print("exported to:", out_path)
```

**Output structure** (DecisionEventStream):

```python
{
  "simulation": "invest-live",
  "start_time": "20250213-09:30",
  "stride": 2,
  "total_steps": 12,
  "events": [ ... ]   # DecisionEvent list, in checkpoint order
}
```

**Key points**:

- Each `simulate-*.json` in `checkpoints_folder` corresponds to one step;
  events are generated in checkpoint order
- Dialogue participants: `conversation.json` records "who said what at what
  time"; `involves` is extracted from both sides of a dialogue
- `category`/`risk_level` are classified by the governance platform; the
  framework leaves them empty — the framework only records "what happened"

## Involvement & Conversation

`has_conversation` / `involves` reflect whether an action involves others:

```python
# conversation.json example
{
  "20250213-09:32": [
    {"老周 -> 沈砚之 @ 投资咨询中心:资料室": [["老周", "帮我看看这个仓位"], ["沈砚之", "好的"]]}
  ]
}
```

This dialogue makes the events at 09:32:
- `involves` = `["老周", "沈砚之"]`
- `has_conversation` = `True`

## Auto-export with the Simulator

Set `export_decisions=True` when constructing the `Simulator` to export
automatically each round:

```python
sim = mf.Simulator(
    max_workers=2,
    export_decisions=True,      # enable auto-export
    story=story,
)
# after simulate(), decisions.json is generated next to the checkpoint folder
```

## How a Governance Platform Uses It

After importing `decisions.json`, the platform can:

1. **Filter by role**: `agent` + `role` fields
2. **Slice by time**: `time` / `step` fields build a timeline
3. **Identify involving events**: `has_conversation` / non-empty `involves` =
   decisions involving collaboration/dialogue
4. **Classify & assess risk**: `category` / `risk_level` filled by the platform
   (framework leaves empty)
5. **Rank by importance**: `poignancy` score

## Summary

- Decision export turns a simulation into a structured event stream
  (time/agent/action/involves/importance)
- `export_decision_stream` generates from checkpoints; `export_decisions=True`
  auto-exports
- The framework records "what happened"; classification and risk are done by
  the governance platform
- `involves`/`has_conversation` reflect involvement — the core signal of
  decision traceability

---

## Tutorial Index

- [Configuration Loading & Validation](tutorial-config-en.md)
- [Runtime: Game & Simulator](tutorial-game-en.md)
- [Message Protocol](tutorial-protocol-en.md)
- Decision Export (this page)
