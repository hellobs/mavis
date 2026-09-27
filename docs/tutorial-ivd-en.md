# Tutorial: The IVD Value-Governance Layer (institution → experience → internalization → audit)

> For integrators: how to use mavisframework's value-governance layer — and how
> to **test** it. Prerequisites: `tutorial-extension-en.md` (extension-surface
> contract), `tutorial-decisions-en.md` (decision export).

## 1. The picture first: four levers

IVD (Internal Value Development) governs an AI not by directly editing its
behavior, but by changing the institutional environment so that values form
through experience. Four levers:

| Layer | Carrier | Operated by | Latency |
|---|---|---|---|
| Institution | `governance.json` (expected goal weights) | experts/governors | slow (needs new experience) |
| Content | story events / dialogue (`interaction_request` hook) | scenario/injector | medium |
| Behavior | `ConsequenceEngine` consequence feedback | automatic | instant–medium |
| Audit | `interventions.json` + `decisions.json` + tendency curve | read-only | after the fact |

One-line summary: **weights set *where to drift*, experience supplies *how
exactly*, audit keeps the whole chain inspectable.** Weights without experience
produce no internalization; experience without weights has no direction.

## 2. Institution layer: governance.json

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

Key points:
- Goal names are **behavior-bound** — chosen so embedding scoring can
  distinguish them (e.g. "Risk Control" matches "stress-tested the client's
  portfolio");
- Weights are **expectations, not controls**: they never enter the prompt and
  never force regeneration; they only weight consequence feedback, so an
  expert adjustment is *felt* through later experience (lagged convergence =
  internalization evidence);
- `Σ weights = 1` (relative preference — cannot express hard red lines; a
  rules layer is a known extension direction).

API (`mavisframework.runtime.governance.Governance`): `load(path)` / `save()` /
`get_constraints(name)` / `all_constraints()` / `set_constraints(name, goals)`
(sets + saves). **Integrator note**: `save()` is a plain whole-file write —
add your own locking/atomicity for high-frequency writes (see §6).

## 3. Behavior layer: ConsequenceEngine (consequence feedback)

```python
from mavisframework.runtime.consequence import ConsequenceEngine
engine = ConsequenceEngine()          # or ConsequenceEngine(scorer=your_scorer)
feedback = engine.feedback(agent, "stress-tested the client's portfolio")
# → {"Risk Control": 0.41, "Data Rigor": 0.33, ...}   (softmax share × weight)
```

- Score = **embedding cosine similarity** between the action text and each goal
  name (GoalScorer, default local Ollama `qwen3-embedding:0.6b-q8_0`),
  softmax-normalized then weighted by the constraint;
- Negative similarities are preserved: an action that clearly violates a goal
  yields a small share for it (weak internalization), instead of being clipped
  to zero;
- **Embedding unavailable → degrade to neutral feedback** (= constraint
  weights), counted in `engine.health()` degraded stats — degradation is
  observable, never silent; sustained degradation shows up as flat curve
  segments;
- The scorer is injectable (`scorer=`) — pass a fake in tests for zero network.

## 4. Agent side: how the value tendency updates

After `agent.attach_governance(governance, consequence_fn)`, each
`observe_consequence(action_desc)`:

1. Samples: window entries at **action-change points** plus a periodic refresh
   (`think.tendency_refresh`, default 5) so persistent actions keep
   reinforcing;
2. Sliding window: entries `{action, alignment, feedback, time}`, capacity
   `think.tendency_window` (default 15), recency decay by **simulation time**
   (`decay_per_hour ** age_hours`, default 0.6/hour);
3. Blends: `tendency = α·persona + (1−α)·experience`,
   `α = max(0.1, 1 − experiences / window_capacity)` (0.1 floor = 10%
   character residue; transition ≈ one full window);
4. **Constraint filtering**: goals removed by the institution disappear from
   the tendency and the window (no shadow goals);
5. **Normalization**: Σ(value_tendency) ≡ 1 (property-tested to ~1e-9);
6. Audit: `status["tendency_meta"]` (α/window/obs) and
   `status["tendency_window"]` persist with checkpoints; `--resume` continues.

**Sealed math properties** (`tests/test_ivd_regression.py`): sum conservation,
α floor 0.1, dropped-dimension extinction. Read these three tests before
touching the update logic.

## 5. Interventions & audit: what the expert changed, and where it landed

```json
{
  "time": "2026-09-27 10:00:00",        // wall clock (sort fallback)
  "sim_time": "20250213-12:02",         // simulation time (curve marker)
  "simulation": "stock-en8",            // owning simulation (cross-sim isolation)
  "agent": "AI Advisor",
  "old_constraints": {...}, "new_constraints": {...},
  "operator": "expert",                 // "undo" for rollbacks (original marked revoked)
  "note": "expert rationale (recommended — needed for intervention analysis)",
  "intervention": "goals"               // modality marker (for modality comparison)
}
```

Write-path caveat: **the in-memory sync is critical** — `agent._governance`
points at the same `game.governance` object; changing only the file leaves
consequence feedback on old constraints and the tendency curve frozen.
Undo semantics: roll back to `old_constraints`, mark the original `revoked`,
and append the undo itself to the chain (history is never erased).

## 6. Testing pattern: sealed (Ollama-free) is a hard requirement

The IVD chain involves embedding scoring, and Ollama may be offline. **War
story**: one regression test implicitly constructed a real GoalScorer — 2s
with Ollama online, 641s of dead timeouts across the suite offline. Rules:

- Inject a fake scorer in tests:

```python
class _NullScorer:
    def alignment(self, action, goals):   # equivalent to the offline fallback
        return {}

agent._goal_scorer = _NullScorer()        # or ConsequenceEngine(scorer=FakeScorer())
```

- Or inject a fake `consequence_fn` returning `{goal: feedback}` directly —
  sampling/blending/normalization are still covered, fully deterministic;
- Property tests (Σ=1, α floor, dimension extinction) need only ~40 rounds of
  random feedback — see
  `tests/test_ivd_regression.py::TestTendencyMathProperties`.

## 7. Runtime environment checklist

- Ollama + `qwen3-embedding:0.6b-q8_0` (consequence scoring; offline = full
  degradation and flat curves — `engine.health()["degrade_rate"]` reports it
  honestly);
- The thinking LLM (agent roles) can be any compatible endpoint; for
  judging/routing tasks with reasoning models, raise `max_tokens` (reasoning
  burns the budget first and `content` can come back empty).
