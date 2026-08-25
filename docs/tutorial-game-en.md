# Tutorial: Runtime (Game & Simulator)

This tutorial runs a real simulation — from the `Game` container instantiating
roles, to the `Simulator` driving the full lifecycle. After reading, you'll
understand how agents are created, how the LLM is plugged in, and how to run a
complete simulation step.

## 1. Game: The Container

`Game` holds all agents and the map — the "stage" of the simulation. It needs
three things: config, a static assets root, and checkpoint conversation.

```python
import mavisframework as mf

# 1) prepare config (see the config tutorial)
names = ["沈砚之", "苏清越", "陈慕白", "林晚晴", "老周"]
cfg = mf.load_config("20250213-09:30", 2, names)

# 2) build the Game
game = mf.Game(
    name="demo",
    static_root="frontend/static",   # static assets root (roles/map)
    config=cfg,
    conversation={},                  # checkpoint conversation (empty for new sim)
    timer=mf.Timer(start=cfg["time"]["start"]),
)
game.reset_game()                     # reset to initial state

# 3) inspect agents
for name, agent in game.agents.items():
    print(name, agent.coord, agent.scratch.currently)
```

`static_root` is joined with the relative paths in `cfg`:
`frontend/static` + `assets/village/...` → actual files under
`frontend/static/assets/village/...`. That's why config uses relative paths.

## 2. Making One Agent Think

`game.agent_think(name, status)` drives one agent through a single thinking
step (movement + schedule + LLM output):

```python
status = {"coord": game.agents["老周"].coord}   # current coordinate
result = game.agent_think("老周", status)
result["plan"]   # thinking result (schedule/action plan)
result["info"]   # agent state snapshot (coord/action/dialogue)
```

> An LLM is required: agent thinking depends on the LLM (schedule generation,
> action description). Without a configured LLM the framework raises a clear
> error telling you to configure one — see "Configure the LLM" below.

## 3. Configure the LLM

LLMs are plugged in via providers; configured in `data/config.json` under
`agent.think.llm`:

```json
{
  "agent": {
    "think": {
      "llm": {
        "provider": "ollama",
        "model": "qwen3:4b-instruct-2507-q4_K_M",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": ""
      }
    }
  }
}
```

**Provider options:**

| provider | description | example model |
|---|---|---|
| `ollama` | local, free, recommended for development | `qwen3:4b-instruct-2507-q4_K_M` |
| `openai` | any OpenAI-compatible API | `deepseek-chat` (base_url pointing to DeepSeek) |

Create a provider in code:

```python
llm = mf.create_llm_provider(cfg["agent_base"]["think"]["llm"])
print(llm.is_available())   # True = connected
```

## 4. Simulator: The Parallel Scheduler

`Simulator` drives parallel thinking, dialogue, checkpoints and story injection
for all agents. Construct it, then call `simulate` for the desired steps:

```python
# load business config (relations injected into agents, story into simulator)
import json
rels = json.load(open("scenarios/investment/relationships.json", encoding="utf-8")).get("relations", [])
story = json.load(open("scenarios/investment/story.json", encoding="utf-8")).get("events", [])

sim = mf.Simulator(
    max_workers=max(1, len(game.agents)),  # concurrency = number of roles
    export_decisions=False,                 # whether to export the decision stream
    story=story,                            # story events (crisis injection)
    on_story=lambda ev: print("story:", ev),  # story trigger callback
)

sim.simulate(
    game,
    cfg,
    step=1,                  # run 1 step
    stride=2,
    start_step=0,
    checkpoints_folder="results/checkpoints/demo",  # checkpoint dir
    on_step=lambda *a: None,   # per-step callback
    on_agent=lambda *a: None,  # per-agent callback
)
```

**After the run**: the checkpoint dir contains `simulate-<time>.json` (per-agent
state/action/dialogue) and `conversation.json` (dialogue records).

## 5. Full Example: Run One Step

```python
import json
import mavisframework as mf

names = ["沈砚之", "苏清越", "陈慕白", "林晚晴", "老周"]
cfg = mf.load_config("20250213-09:30", 2, names)

game = mf.Game("demo", "frontend/static", cfg, {},
               timer=mf.Timer(start=cfg["time"]["start"]))
game.reset_game()

story = json.load(open("scenarios/investment/story.json", encoding="utf-8")).get("events", [])
sim = mf.Simulator(max_workers=2, export_decisions=False, story=story)

sim.simulate(game, cfg, step=1, stride=2, start_step=0,
             checkpoints_folder="results/checkpoints/demo")

print("done: 1 step simulated; checkpoints under results/checkpoints/demo")
```

## Summary

- `Game` is the stage: agents + map; `game.agent_think` drives a single agent
- An LLM must be configured (ollama or openai-compatible), otherwise the
  framework raises a clear error
- `Simulator.simulate` drives all roles in parallel, with checkpoints and
  story injection
- Checkpoints are the basis for resume (`load_config_from_log`)

Next: read the [message protocol tutorial](tutorial-protocol-en.md) to see how
frontends/Unity consume simulation output.
