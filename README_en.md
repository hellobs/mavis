# mavisframework

English | [简体中文](./README.md)

A self-developed generative multi-agent simulation framework (MAVIS) for
fine-grained business process simulation. Agents live, memorize, reflect,
decide and interact within a spatial environment; every step is configurable,
explainable and visualizable in real time.

The framework layer has zero rendering dependencies (it does not embed
Phaser, Unity, Flask or any frontend/server framework); frontends act purely
as consumers of protocol messages.

## 1. Installation

Recommended with [uv](https://docs.astral.sh/uv/), Python >= 3.12.

```bash
# Option A: build wheel and install (recommended, verified stable)
uv build
uv pip install dist/mavisframework-1.0.0-py3-none-any.whl

# Option B: editable install (for framework development)
uv venv --python 3.12
uv pip install -e .
```

Known issue: the editable install (`-e`) has an import quirk in the current
environment — the top-level `mavisframework` imports fine, but nested
submodules (e.g. `mavisframework.config.loader`) may fail to resolve after
changing the working directory. Use Option A (wheel) for production or
platform integration.

Runtime dependencies are only `pydantic>=2.0` and `requests>=2.31`; there are
no hard dependencies on AI or rendering frameworks. LLMs are plugged in via
providers (Ollama / OpenAI) and are not mandatory.

## 2. Module Layout

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

## 3. Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `MAVIS_PROMPT_DIR` | package `prompts/` | prompt template directory |
| `MAVIS_CONFIG_PATH` | `data/config.json` | agent_base config (LLM etc.) |
| `MAVIS_ASSETS_ROOT` | `assets/village` | static assets relative root |
| `MAVIS_STATIC_ROOT` | `frontend/static` | frontend static root (compressor) |
| `MAVIS_CHECKPOINTS_ROOT` | `results/checkpoints` | checkpoints root |

## 4. Layering

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

## 5. Message Protocol

Defined in `runtime/protocol.py`. Coordinates are grid-based; the protocol is
transport-agnostic (SSE / WebSocket both work).

| Message | Purpose | Consumer |
|---|---|---|
| `AgentState` | per-agent state (coord/path/action) | frontend/Unity |
| `TimeMsg` | simulation time | frontend clock |
| `ChatLineMsg` | dialogue lines | dialogue panel |
| `SnapshotMsg` | full snapshot | new connections catch-up |
| `DecisionEvent` | decision events | governance platform / expert UI |

## 6. Usage

The framework's `Game` + `Simulator` + `LiveCompressor` drive the complete
simulation (parallel thinking / checkpoints / decision export / WebSocket
push). The legacy implementation (`modules/`, and `start.py`/`live.py`/
`compress.py`/`replay.py`) has been removed; all logic now lives in the
framework (see git history).

A complete demo platform is [Provenance](https://github.com/hellobs/provenance):
its real-time service `live_fastapi.py` is a reference implementation of the
framework route (FastAPI + WebSocket consuming framework contract messages).

## 7. Unity Migration

```
framework core (agent/memory/pathfinding/decision export)  ← zero change
        ↓ protocol.py messages
transport: SSE (Phaser) → WebSocket (Unity)   ← transport only
frontend: Phaser → Unity                      ← rendering only (same protocol)
```

The framework is unaware of the concrete frontend implementation — this is the
structural guarantee that Phaser is not embedded in the framework.

## 8. Repository Layout

```
mavisframework/          # framework package (pip package; pyproject at repo root)
├── core/ scene/ runtime/ output/ config/ prompt/
└── prompts/             # prompt templates (shipped with the package)
config_tool/             # role configuration tool (standalone FastAPI service)
pyproject.toml           # build config (uv build / uv pip install)
```

config_tool belongs to the framework repository, but its outputs
(roles/relations/story) are written into the platform's frontend assets and
scenario directories. By default it probes the sibling directory `../provenance`
(the platform repo, compatible with both layouts where platform code lives in a
subdirectory or at the repo root); deployment can override via environment
variables:

- `MAVIS_ASSETS_ROOT` — platform frontend assets root (`frontend/static/assets/village`)
- `MAVIS_SCENARIOS_DIR` — platform scenario directory (`scenarios`)

## 9. Status

- Done: protocol / core / scene(maze) / runtime(llm, simulator) / output(decisions) / config(loader, validator)
- The framework runs standalone: full agent lifecycle, memory stores
  (SimpleStore / LlamaIndexStore optional), and prompt system have no external
  module dependencies
- Platform consumption: the Provenance platform's real-time service is driven
  by the framework's Game + Simulator; decision export is wired into the pipeline
- Next: business-layer config effects (relation/story injection), Unity frontend
