# mavisframework

English | [简体中文](./README_zh.md)

A self-developed generative multi-agent simulation framework (MAVIS) for
fine-grained business process simulation. Agents live, memorize, reflect,
decide and interact within a spatial environment; every step is configurable,
explainable and visualizable in real time.

The framework layer has zero rendering dependencies (it does not embed
Phaser, Unity, Flask or any frontend/server framework); frontends act purely
as consumers of protocol messages.

## 📚 Documentation

Learn mavisframework from scratch (with runnable examples):

| Tutorial | Content |
|---|---|
| [Config Loading & Validation](docs/tutorial-config-en.md) | role/scenario config, load_config, validate_all |
| [Runtime: Game & Simulator](docs/tutorial-game-en.md) | create agents, plug in LLM, run a step |
| [Message Protocol](docs/tutorial-protocol-en.md) | agent/time/chat_line contract, validate_message |
| [Decision Export](docs/tutorial-decisions-en.md) | simulation → decision event stream (for governance) |
| [Extension Surface](docs/tutorial-extension-en.md) | the stable API integrators may rely on; defaults, limits, what to do when a new need appears |

Chinese versions in the [docs/ directory](docs/).

## 📌 Extension surface (the contract with integrators)

mavisframework carries no business logic; integrators plug in through a small, stable set of
points — `load_config(...)`, `Game(..., timer=, governance=)`, `Simulator(..., external_state=,
interaction_request=, on_agent=, on_step=, on_story=, plugins=)`, `Simulator.register_condition(...)`,
per-role fields such as `role_directive` / `think.llm`, public `Agent` methods, the
process-wide `agent_core.chat_callback` (the old direct assignment still works; a new
`subscribe_chat_line` supports multiple consumers), and the generic plugin surface
`Plugin` / `PluginManager` discovered via the entry-point group `mavisframework.plugins`.

Two rules keep the framework usable for everyone:

1. **Off by default** — optional additions default to `None` / `False` / `""`; without
   configuration the behaviour is byte-identical to before.
2. **No business vocabulary** in the framework source — no case01, no role names, no company
   names. Generic demo vocabulary is fine.

Both are enforced by [`tests/test_extension_surface.py`](tests/test_extension_surface.py),
which also pins signatures and defaults. Read
[Extension Surface](docs/tutorial-extension-en.md) before adding a capability; if a need can
be met by configuration, by an existing extension point, or inside the integrator itself,
do that instead of changing the framework.

## 1. Installation

mavisframework is a standard Python package (pyproject.toml + setuptools),
Python >= 3.12. Works with any toolchain (pip / uv / poetry).

```bash
# Development/collaboration: editable install (code changes take effect
# immediately; after this repo updates, just `git pull` — no reinstall needed)
pip install -e .

# Release/pinned version: build wheel and install
pip install .                          # install from source directly
python -m build && pip install dist/mavisframework-1.3.0-py3-none-any.whl

# uv also works (optional; toolchain of your choice)
# uv pip install -e .
# uv build && uv pip install dist/mavisframework-1.3.0-py3-none-any.whl
```

Runtime dependencies are only `pydantic>=2.0` and `requests>=2.31`; there are
no hard dependencies on AI or rendering frameworks. LLMs are plugged in via
providers (Ollama / OpenAI) and are not mandatory — but **running a simulation
requires an LLM**: if none is configured, the framework raises a clear error
telling you to configure one.

## 2. Top-Level API

`mavisframework` exposes common entry points; users do not need to dig into
internal submodules:

```python
import mavisframework as mf

# config loading & validation
cfg = mf.load_config("20250213-09:30", 2, ["沈砚之", "老周"])   # new simulation config
cfg2 = mf.load_config_from_log("results/checkpoints/invest")     # resume from log
scenario = mf.load_scenario("scenarios/investment")              # scenario config
errs = mf.validate_all(agents, rels, story, maze)                # config validation

# runtime
game = mf.Game("demo", "frontend/static", cfg, {})               # game container
sim = mf.Simulator(max_workers=2, story=story)                   # parallel scheduler
llm = mf.create_llm_provider(cfg["agent_base"]["think"]["llm"])  # LLM provider

# message protocol
from mavisframework import validate_message, AgentState, ChatLineMsg

# core classes
from mavisframework import Agent, Timer, Maze
```

The full symbol list is in `mavisframework/__init__.py` (`__all__`). Internal
submodule paths (e.g. `mavisframework.config.loader`) remain available.

## 3. Module Layout

```
mavisframework/
├── core/                 # pure logic layer (zero rendering/communication deps)
│   ├── event.py          # event model
│   ├── action.py         # action (time-injected)
│   ├── spatial.py        # spatial memory (address tree)
│   ├── schedule.py       # schedule (time-injected)
│   ├── timer.py          # simulation clock (injectable, zero global state)
│   ├── memory.py         # associative memory + three-factor retrieval
│   ├── store.py          # memory store abstraction (SimpleStore / LlamaIndexStore)
│   ├── associate.py      # associative memory (events/dialogues/thoughts + retrieval)
│   ├── agent_core.py     # full agent lifecycle (component-injected)
│   └── prompts/          # prompt templates (shipped with the package)
├── scene/
│   └── maze.py           # spatial/collision/pathfinding/address index
├── runtime/
│   ├── protocol.py       # message protocol (unified contract for frontends)
│   ├── llm.py            # LLM adapter interface (pluggable)
│   ├── llm_providers.py  # provider implementations (self-contained)
│   ├── game.py           # game container (agents + maze + conversation)
│   ├── simulator.py      # parallel scheduling/callbacks/checkpoints/decision export
│   └── compressor.py     # real-time compressor (agent states/replay frames)
├── output/
│   └── decisions.py      # decision event export
└── config/
    ├── loader.py         # scenario & simulation config loading
    └── validator.py      # config validation (syntax/map/role consistency)
```

## 4. Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `MAVIS_PROMPT_DIR` | package `prompts/` | prompt template directory |
| `MAVIS_CONFIG_PATH` | `data/config.json` | agent_base config (LLM etc.) |
| `MAVIS_ASSETS_ROOT` | `assets/village` | static assets relative root |
| `MAVIS_STATIC_ROOT` | `frontend/static` | frontend static root (compressor) |
| `MAVIS_CHECKPOINTS_ROOT` | `results/checkpoints` | checkpoints root |

## 5. Layering

```
scenarios/          business layer (change business = change config)
   ↓ load
mavisframework/     framework layer (pure logic, zero rendering)
   ↓ produce
runtime/protocol.py message protocol (agent/time/chat_line/decision...)
   ↓ consume
frontend/phaser     frontend shell (current, browser)
frontend/unity      frontend shell (planned, consumes the same protocol)
governance platform consumes DecisionEventStream
```

## 6. Message Protocol

Defined in `runtime/protocol.py`. Coordinates are grid-based; the protocol is
transport-agnostic (SSE / WebSocket both work).

| Message | Purpose | Consumer |
|---|---|---|
| `AgentState` | per-agent state (coord/path/action) | frontend/Unity |
| `TimeMsg` | simulation time | frontend clock |
| `ChatLineMsg` | dialogue lines | dialogue panel |
| `SnapshotMsg` | full snapshot | new connections catch-up |
| `DecisionEvent` | decision events | governance platform / expert UI |

## 7. IVD: Value Formation & Governance (experimental)

The framework supports a governance layer where an agent's *value tendency*
(what it has internalized from experience) can be observed and influenced by
external *institutional constraints* — without directly manipulating behavior.

Three sources shape `value_tendency` (a normalized `{goal: weight}` map):

| Source | Where | Role |
|---|---|---|
| `initial_tendency` | `agent.json` (agent body) | persona baseline — who the agent *is* (e.g. an impulsive trader) |
| constraints | `governance.json` (institutional layer) | what the institution *expects* (expert-adjustable) |
| experience | `ConsequenceEngine` feedback → sliding window | what the agent *learns* from action outcomes |

Key semantics:

- **ai_tool roles** (e.g. an AI advisor product) start at the constraints:
  the institution *built* them that way. **user roles** start at their
  `initial_tendency` persona baseline (or uniform if unset), then experience
  modulates it.
- **Inertia blend**: `tendency = α·persona + (1−α)·experience` with an
  *adaptive transition period*: `α = max(0.1, 1 − experiences / window_size)`,
  where `window_size` is the memory-window capacity (`think.tendency_window`,
  default 15). The transition ≈ one full window of experiences instead of a
  magic constant; a smaller window converges faster, a larger one more slowly.
  The 0.1 floor keeps a 10% character residue (personality is sticky). The
  current `α`/decay bound/observed count are recorded per update in
  `tendency_meta` for audit/interpretability.
- **Sampling**: feedback is recorded at *action-change points* plus a periodic
  refresh (`tendency_refresh`, default 5 steps) so a persistent action still
  reinforces the tendency instead of freezing the curve.
- **Constraints are expectations, not controls**: they never enter the prompt
  and never force action regeneration. They only weight the consequence
  feedback, so an expert adjustment is *felt* by the agent only through later
  experience (lagged convergence = internalization evidence).
- **Auditable**: `goal_alignment` (instant), `value_tendency` (accumulated),
  and `interventions.json` (expert edits, with the simulation time of each
  intervention) are all exported for audit.
- **Resume continuity**: `--resume` restores `value_tendency` and the
  experience count from the checkpoint (instead of resetting to the persona
  baseline), so the tendency curve stays continuous across restarts and the
  inertia alpha does not rewind.

Consequence feedback measures the **semantic similarity** between the action
text and each constrained goal via embeddings, then takes a relative share
(softmax-like) weighted by the constraint — a light stand-in for a market
model (see `runtime/consequence.py`). The interface is a plain callable
`(agent, action_desc) -> {goal: feedback}`, so it can be swapped for a real
simulated market later. Steady-state intuition: tendency converges to
(weight × behavior–value coupling) normalized — institutional emphasis scales
a value's share, but behavior that never touches a value cannot be pulled by
weight alone (the boundary of governance).

## 8. Usage

The framework's `Game` + `Simulator` + `LiveCompressor` drive the complete
simulation (parallel thinking / checkpoints / decision export / WebSocket
push). The legacy implementation (`modules/`, and `start.py`/`live.py`/
`compress.py`/`replay.py`) has been removed; all logic now lives in the
framework (see git history).

A complete demo platform is [Provenance](https://github.com/hellobs/provenance):
its real-time service `live_fastapi.py` is a reference implementation of the
framework route (FastAPI + WebSocket consuming framework contract messages).

### 8.1 Companion tools in this repo

- **`config_tool/`** — form-based role/scenario config generator (port 8060).
  Business users fill role/duty/goal/relationship/story forms; it produces
  `agent.json` / `relationships.json` / `story.json` validated by this
  framework's validator, written into the Provenance platform's `agents/` and
  `scenarios/` directories. Engine access lives in `config_tool/engine_bridge.py`
  and requires `CASE_ENGINE_DIR`. See `config_tool/README.md`.
- **`../provenance/tools/tilemap_to_maze.py`** — CLI converter (Tiled map →
  `maze.json`) for the Provenance platform; no external deps. Its output is
  consumed by `mavisframework/scene/maze.py`. See
  `../provenance/tools/tilemap_to_maze_README.md`.

## 9. Unity Migration

```
framework core (agent/memory/pathfinding/decision export)  ← zero change
        ↓ protocol.py messages
transport: SSE (Phaser) → WebSocket (Unity)   ← transport only
frontend: Phaser → Unity                      ← rendering only (same protocol)
```

The framework is unaware of the concrete frontend implementation — this is the
structural guarantee that Phaser is not embedded in the framework.

## 10. Repository Layout

```
mavisframework/          # framework package (pip package; pyproject at repo root)
├── core/ scene/ runtime/ output/ config/ prompt/
└── prompts/             # prompt templates (shipped with the package)
config_tool/             # role configuration tool (standalone FastAPI service)
pyproject.toml           # build config (uv build / uv pip install)
```

config_tool belongs to the framework repository, but it is a **companion tool for the
engine side**: it reads the engine registry/schema for validation and self-check runs,
and its outputs (roles/relations/story/scenarios) are written into the platform's
frontend assets and scenario directories. Both locations are **declared explicitly**
(since 2026-09-22 it no longer probes the sibling directory `../provenance`):

- `CASE_ENGINE_DIR` — directory containing the engine package (`case_engine/`). If it is
  not set the tool still starts, but engine-dependent features (the *Engines* page,
  *Composition* runs, deep schema validation) are greyed out and the reason is shown on
  the page and printed at startup — never silently degraded.
- `MAVIS_PLATFORM_DIR` — platform repo root (output paths, maze, live-entry switching);
  falls back to `CASE_ENGINE_DIR`.
- `MAVIS_ASSETS_ROOT` — platform frontend assets root (`frontend/static/assets/village`)
- `MAVIS_SCENARIOS_DIR` — platform scenario directory (`scenarios`)
- `MAVIS_MAZE_PATH` — default maze file
- `CASE_ENGINE_CASES_ROOT` — scenario root (same variable the engine uses)

Startup example (Windows):

```bat
cd D:\zzr\mavis\config_tool
set CASE_ENGINE_DIR=D:\zzr\provenance\provenance
python app.py            :: http://127.0.0.1:8060/
```

Every engine contact point is consolidated in `config_tool/engine_bridge.py`; the other
modules only call into it. See `config_tool/README.md`.

## 11. Status

- Done: protocol / core / scene(maze) / runtime(llm, simulator) / output(decisions) / config(loader, validator)
- The framework runs standalone: full agent lifecycle, memory stores
  (SimpleStore / LlamaIndexStore optional), and prompt system have no external
  module dependencies
- Platform consumption: the Provenance platform's real-time service is driven
  by the framework's Game + Simulator; decision export is wired into the pipeline
- Next: business-layer config effects (relation/story injection), Unity frontend

## 12. Versioning

**API stability promise**: once published, the top-level API (Section 2) stays
backward-compatible. New capabilities must not break existing signatures; any
breaking change requires a major version bump and a migration note here.

**Semantic versioning** ([semver](https://semver.org/)):

| Change type | Version example |
|---|---|
| Breaking API change | 2.0.0 |
| New feature (backward-compatible) | 1.1.0 |
| Bug fix (backward-compatible) | 1.0.1 |

**Behavior note (1.3.0)**: the always-on ("no_sleep") idle text is no longer hard-coded in
the kernel. It now comes from a single source (`mavisframework.idle_text`) and is overridable
per scenario via `config["idle_text"]`; the legacy cleanup of stored Chinese idle strings
requires an explicit table `config["idle_text_map"] = {"<old fragment>": "<replacement>"}`
(empty by default = nothing is rewritten). Consumers that relied on the previous unconditional
cleanup must add that map to their agent config.

**Version history**:

- `1.3.0` — always-on idle text becomes a single overridable source (`mavisframework.idle_text`,
  config keys `idle_text` / `idle_text_map`); no top-level API signature removed.
- `1.2.1` — absolute `assets_root` resolution fix (root/drive no longer swallowed).
- `1.2.0` — generic plugin surface + three default-off injection hooks.

**Update flow** (for consumers):

- **Development/collaboration**: install with `pip install -e .`; after this
  repo updates, `git pull` takes effect immediately — no reinstall needed
- **Release**: bump the version per semver and build the wheel; consumers
  update `mavisframework==X.Y.Z` in their requirements and reinstall
- **Version sync**: the platform (Provenance) pins the dependency version in
  its `requirements.txt`; `pyproject.toml` in this repo is the single source of
  truth for the version, updated together at each release
