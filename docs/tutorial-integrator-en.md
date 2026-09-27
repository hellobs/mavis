# Tutorial: Building on mavisframework

This page is the shortest path for people who want to build something *on top of*
mavisframework: you do not need to read the framework internals — you only need to rely on a
small set of stable entry points. It covers **what to do**: a minimal skeleton for each of the
four common shapes, what you must provide yourself, and the traps that cost the most time.
The contract details (signatures, defaults, known limits) live in
[Extension Surface](tutorial-extension-en.md); read the two together.

## 0. Install first

```bash
git clone https://github.com/hellobs/mavis.git
cd mavis
pip install -e .        # editable install while developing; python -m build + wheel for a release
pytest tests            # sealed suite: no Ollama needed, finishes in seconds
```

Python >= 3.12; the only runtime dependencies are `pydantic` and `requests`.

Keep one thing in mind: **mavisframework is a kernel — it ships no business logic and no
example assets** (agent directories, `maze.json`, story and relationship config). Whether a
simulation runs therefore depends on what *you* provide; that is exactly what separates the
four shapes below.

## 1. Decide which shape you are building

- **Shape 1, an integrator application / platform**: you provide assets, config and an LLM,
  drive the run with `Game` + `Simulator`, and decide how to display and persist it. The
  reference implementation is the Provenance platform's live service `live_fastapi.py`
  (FastAPI + WebSocket consuming the framework contract messages).
- **Shape 2, a plugin package**: you consume nothing but the "event dictionary" and never
  touch framework internals — observers, visualizers, recording and audit belong here. The
  reference implementation is `mavis-vizkit` (a standalone package that does **not** import
  the framework at runtime).
- **Shape 3, a frontend / rendering backend**: you only consume the message protocol
  (`mavisframework.runtime.protocol`), and pick your own transport (SSE / WebSocket). The
  current Phaser shell and the planned Unity shell are both of this kind.
- **Shape 4, the governance layer (optional)**: `governance` + `consequence_fn` let external
  institutional constraints influence an agent's value tendency; swapping in a real market
  model means replacing a single callable.

The shapes compose: a platform application can mount any number of plugins, write its own
frontend, and optionally attach the governance layer. The key convention is that **each layer
depends only on the stable surface of the layer below** — which is why changing the frontend
never touches the kernel, and adding a plugin never touches the business logic.

## 2. Shape 1: a minimal runnable application

```python
import mavisframework as mf

# 1) Assets you must provide: agent directories (agent.json), maze.json, story/relations
cfg = mf.load_config("20250213-09:30", 2, ["RoleA", "RoleB"],
                     assets_root="assets/village")

# 2) Always pass timer explicitly: otherwise the wall clock is used and real time affects the run
timer = mf.Timer(start=cfg["time"]["start"])
game = mf.Game("demo", "frontend/static", cfg, {}, timer=timer)
game.reset_game()          # the LLM provider is created on reset (see section 8)

sim = mf.Simulator(
    max_workers=len(game.agents),
    story=story_events,                                   # story events (may be empty)
    on_agent=lambda name, state, step, sim_time: None,    # read-only notification: persist / push
    on_step=lambda config: None,
    export_decisions=True, decisions_path="results/decisions.json",
)
sim.simulate(game, cfg, step=1, stride=2, start_step=0,
             checkpoints_folder="results/checkpoints/demo")
```

Three things the integrator always decides:

- **Assets and config**: the framework reads by path only and guesses no defaults;
  `assets_root` is a relative root appended to `static_root` (i.e.
  `frontend/static/assets/village/...`).
- **The LLM**: without one there is no usable provider, and the framework raises a clear
  error telling you to configure one (local Ollama, or any OpenAI-compatible API).
- **Pacing**: the framework never takes over the turn loop. Watch step by step by looping
  `simulate(step=1)`, or run to the end with `step=N`.

## 3. Shape 2: a minimal plugin package (start here)

A plugin only relies on three things: three optional methods, one event dictionary, and one
entry-point group name.

```python
from mavisframework.plugin import Plugin

class CountPlugin(Plugin):
    name = "count"

    def __init__(self, limit=3):
        self.limit = limit
        self.lines = []

    def setup(self, ctx=None):       # ctx is filled by the mounting side (game / config / simulator); the framework only passes it through
        if ctx and "limit" in ctx:
            self.limit = ctx["limit"]

    def on_event(self, evt):         # agent / time / chat_line / story
        if evt.get("type") == "chat_line":
            self.lines.append((evt["speaker"], evt["text"]))

    def teardown(self):              # wrap up: close connections / flush to disk
        print("lines:", len(self.lines))

sim = mf.Simulator(..., plugins=[CountPlugin()])   # pass instances: they carry their own config (recommended)
# ... run ...
sim.plugin_teardown()                              # explicit teardown (unsubscribes first, then tears down)
```

Event types and keys: `time` / `chat_line` / `story` match `mavisframework.runtime.protocol`;
`agent` is a **native framework subset** (`name` / `coord` / `path` / `time`, plus the raw
`state`); `init` / `snapshot` are never produced by the framework and must be constructed by
the visualization layer. Event dicts may share references with `on_*` callbacks and
`config["agents"]`, and the framework keeps mutating them in later steps — so plugins should
**treat events as read-only**: do not mutate them in place, and do not hold on to them as
stable snapshots.

To ship a *discoverable* plugin package, declare an entry point in your own `pyproject.toml`:

```toml
[project.entry-points."mavisframework.plugins"]
count = "my_plugin.count:CountPlugin"
```

Discovery and instantiation are **two separate steps** (the framework never auto-mounts for you):

```python
from mavisframework.plugin import PluginManager

PluginManager.discover()                    # reads entry points; registers only no-arg-constructible factories
pmgr = PluginManager()
pmgr.mount_by_name("count", limit=5)        # plugins with required config get it explicitly here
sim = mf.Simulator(..., plugins=pmgr)       # a PluginManager may be passed directly
```

A plugin that requires configuration is **never** auto-instantiated by entry-point discovery
(a signature with a required parameter makes it skip, with a warning logged); pass an instance
or call `mount_by_name(..., **config)` instead.

Fault isolation: an exception raised by one plugin in `setup` / `on_event` / `teardown` only
logs a warning — it never breaks the other plugins or the main loop. The reverse also holds:
plugins must **not** swallow exceptions silently; "nothing on the page and nothing in the log"
is the hardest failure to debug.

Reference implementation: `mavis-vizkit` (a standalone package published from the platform
repository under `packages/mavis-vizkit`). It ships its own registry
`mavis_vizkit.register(name, factory)` and a `Fanout` (multi-plugin dispatch that reports
errors instead of swallowing them), bundles four built-in visualizers —
`console` / `report` / `town` / `live` — and offers a third-party entry-point group
`mavis_vizkit.plugins`. **Adding a visualizer = writing one `Visualizer` subclass + one
`register(...)` line** — it is both a worked example of the plugin surface and a sample of
"packages share nothing but the event dictionary".

## 4. Shape 3: a protocol-only frontend

A rendering layer needs no framework import. The contract messages are defined in
`mavisframework.runtime.protocol`: `AgentState` (coord / path / action), `TimeMsg`,
`ChatLineMsg`, `SnapshotMsg` (catch-up for new connections) and `DecisionEvent` (for
governance platforms and expert UIs). Consumers may validate incoming messages with
`validate_message`. The protocol is transport-agnostic: SSE and WebSocket carry the same
messages, and the Phaser shell and the planned Unity shell are two renderings of that one
protocol.

## 5. Shape 4: the governance layer (optional)

`governance` (institutional constraints, `{goal: weight}`) and `consequence_fn` (consequence
feedback, `(agent, action_desc) -> {goal: feedback}`) are two injectable points — **omit them
and the framework does not intervene at all**. Constraints never enter the prompt and never
force actions; they only weight the consequence feedback, so a constraint change is *felt* by
the agent only through later experience (that lagged convergence is itself the evidence of
internalization). The default implementation uses embedding similarity as a light stand-in;
to attach a real market model, replace that callable and the rest of the chain is unchanged.
Mechanics and update math are in
[IVD Value Governance](tutorial-ivd-en.md).

## 6. When a new need appears, follow this order

1. **Can configuration solve it?** (`agent.json` / `maze.json` / `story.json` / run parameters)
2. **Can an existing extension point solve it?** (the list in
   [Extension Surface](tutorial-extension-en.md))
3. **Can you solve it on your own side?** — usually yes (e.g. seed your own randomness with
   `random.seed(n)`; drive the pace yourself with repeated `simulate(step=1)`, since the
   framework never takes over the turn loop).
4. Only if none of the above works, consider adding a **generic** extension point to the
   framework: purely additive, off by default, semantically neutral, with unit tests.
5. **Needing to change existing behaviour semantics -> don't.** Work around it on your side
   instead; this is the foundation of the framework's promise to integrators.

## 7. Checklist before handing off a change

- `pytest tests` is green, including `tests/test_extension_surface.py`, which pins signatures,
  defaults and the two purity rules;
- with the new capability unconfigured, behaviour is byte-identical to before (off by default);
- no business vocabulary from your side appears in framework source (case01 / role names /
  company names);
- if you changed the framework, update [Extension Surface](tutorial-extension-en.md) and the
  extension-surface section of the README.

## 8. Known limits (written honestly; not promises)

- Randomness uses the global `random` module and has no seed parameter; `Agent.think` runs in
  a thread pool, so the order in which roles consume randomness is not deterministic —
  `random.seed()` narrows it but cannot make runs reproducible sample by sample.
- Underscore members (`agent._timer`, `agent._governance`, ...) are not part of the contract
  and are not guaranteed to be stable.
- `Game`'s LLM provider is lazy: without calling `reset_game()` once after construction, the
  first use reports "no usable LLM".
- The framework ships no assets: without agent directories and a `maze.json` there is no
  runnable simulation.