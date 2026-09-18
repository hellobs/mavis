# Tutorial: Extension Surface (the stable API integrators may rely on)

mavisframework ships no business logic of its own: roles, scenes, story events and external
world facts all come from the integrator. This document defines **the layer integrators may
rely on** — the contract between mavis and the business on top of it.

Why a separate document: integrators (a controlled experiment such as case01, or the
provenance platform) only need a small, stable set of entry points. Without an explicit
list, every new requirement turns into a fresh negotiation about "may I do this?".
With the list written down, the integrator's room to manoeuvre is this **already existing**
surface — no framework change needed for most new needs.

Companion: `tests/test_extension_surface.py` turns these points into contract tests
(signatures, defaults, registries, purity), so framework edits break the tests first.

## 1. Overview

- Config: `load_config(...)`, per-role `agent.json`, a few environment variables.
- Container: `Game(...)`.
- Scheduling: `Simulator(...)` and `Simulator.register_condition(...)`.
- Role: public `Agent` methods plus three optional fields.
- Process-wide: `mavisframework.core.agent_core.chat_callback`.

## 2. Item by item

### 2.1 `load_config(...)`

```
load_config(start_time="20240213-09:30", stride=15, agents=None,
            config_path=None, assets_root=None) -> dict
```

- `start_time` is the simulated clock origin (`YYYYMMDD-HH:MM`) and is one of the keys to a
  reproducible run: it is independent of wall-clock time (unlike `Game`'s default `Timer()`).
- `agents` selects which roles to load; omitting it loads every role under the assets root.
- Environment variables: `MAVIS_ASSETS_ROOT`, `MAVIS_CONFIG_PATH`, `MAVIS_CHECKPOINTS_ROOT`.

### 2.2 `Game(...)`

```
Game(name, static_root, config, conversation, timer=None, logger=None,
     governance=None, consequence_fn=None)
```

- Pass an explicit `Timer("<YYYYMMDD-HH:MM>")` whenever simulated time must be controlled;
  otherwise the wall clock is used and run outcomes depend on the real time of day
  (for example, conversations are not started after 23:00).
- `governance` / `consequence_fn` are the IVD hooks; omitting them means zero involvement.

### 2.3 `Simulator(...)`

```
Simulator(on_agent=None, on_step=None, on_chat_line=None, on_story=None,
          max_workers=5, llm_concurrency=0, export_decisions=False,
          decisions_path="", roles=None, story=None, stride=2,
          external_state=None, interaction_request=None)
```

- `on_agent(name, state, step, sim_time)`, `on_step(config)`, `on_story(event_dict)` are
  read-only notifications for visualisation or persistence.
- `external_state(name, step, sim_time, game) -> dict` injects step-level state into the
  prompt only, never into memory. Not passing it reproduces historical behaviour exactly.
- `interaction_request(step, sim_time, game) -> [{from, to, focus}]` asks for an interaction
  from outside; it bypasses the cooldown and the "do I feel like talking" probability gate,
  while all other preconditions still apply. Line content is still produced by the LLM.

`Simulator.simulate(game, config, step, stride=0, start_step=0, checkpoints_folder="",
on_step=None, on_agent=None)` advances a number of steps.

### 2.4 `Simulator.register_condition("<type>")`

```
@Simulator.register_condition("my_condition")
def _check(game, ev) -> bool: ...
```

A story event carrying `{"condition": {"type": "my_condition", ...}}` is then gated by your
function. The registry is `Simulator.CONDITION_CHECKERS`. The framework ships no business
condition types of its own; each event fires only once (deduplicated by id).

### 2.5 Role configuration fields

- `role_directive` (string): when non-empty it is appended as a block to that role's prompt.
- `coord` / `path`: starting position and path.
- `think.llm`: per-role LLM override, so different roles may use different backends.
- `transfer.enabled` (boolean, **default `False`**): location transfer, a demo/visualisation
  feature. Controlled experiments should keep it off and pin coordinates instead.

### 2.6 Public `Agent` methods

`set_step_context(dict)` / `step_context()`, `request_interaction(other, focus="")`,
`move(coord, path=None)`, `make_schedule()`, `find_path(agents)`, `to_dict(with_action=True)`,
`get_tendency()`, `get_constraints()`, `goal_alignment(action)`,
`attach_governance(governance, consequence_fn=None)`, `inject_story_event(event)`,
`recent_story_events(topk=2)`, `is_awake()`, `llm_available()`.

### 2.7 Process-wide hook: `agent_core.chat_callback`

```
import mavisframework.core.agent_core as agent_core
agent_core.chat_callback = lambda speaker, text: ...
```

Called once per produced line (used for live streaming). Default `None`. It is a
process-wide global, so a single process can host only one consumer.

## 3. Two hard rules for new capabilities

1. **Off by default**: optional additions default to `None`, `False` or an empty string;
   without configuration the behaviour must be bit-for-bit identical to before.
2. **No business vocabulary** in the framework source (no case01, no role names, no company
   names). Generic demo vocabulary is not a violation.

Both are enforced by `tests/test_extension_surface.py`.

## 4. What to do when a new need appears

1. Can configuration solve it?
2. Can an existing extension point solve it?
3. Can the integrator solve it on its own? — Usually yes. Example: mavis uses the global
   `random` module with no seed parameter, so `random.seed(n)` inside the integrator's process
   already controls most randomness without touching the framework. Another example: the
   experiment's pacing is entirely up to the integrator, which calls `simulate(step=1)` itself.
4. Only then consider adding a new generic extension point (purely additive, off by default,
   semantics-neutral, with its own tests).
5. **If it requires changing existing behaviour semantics — don't.** Work around it in the
   integrator instead; that is the basis of mavis's promise to its users.

## 5. Known limitations (stated honestly, not as promises)

- **No seed parameter**: the framework uses the global `random` module and `Agent.think` runs
  on a thread pool, so the order in which roles consume randomness is not deterministic.
  Seeding converges but does **not** give sample-by-sample reproducibility.
- **Private members are not part of the contract**: `agent._timer`, `agent._governance`,
  `associate._index` and friends carry no stability guarantee. The provenance live service
  currently reads `_timer` / `_governance`, and the case01 isolation probe reads
  `associate.memory` / `_index`; these are out-of-bounds usages pending adoption — either
  promote them to public read APIs or document the semantics before relying on them.
- **Lazy LLM provider in `Game`**: the provider is created inside `Agent.reset()`. Calling
  `reset_game()` once after construction is required; do not assume a freshly built `Game`
  can run immediately.

## 6. Checklist when editing the framework

1. `pytest tests` is green, contract tests included.
2. New additions are off by default and byte-identical when unconfigured.
3. The source still contains no business vocabulary.
4. If you added an extension point, update this document and the README section.
5. If you fixed a bug, add a test that reproduces it.
