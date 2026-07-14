# Double Crux Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement issue #27 — Double Crux, a disagreement-resolution method that searches for the underlying factual claim (the *crux*) that actually drives a disagreement, tests evidence against that crux alone, and reports either a resolution or a clean map ("the disagreement reduces to X" / "this is a values difference").

**Architecture:** One new `DiscussionMethod` (`consensus/methods/double_crux.py`) assembled from four new composable `PhaseHandler`s plus the reused (newly parametrized) `StatePositionsHandler`, backed by a shared pure-function helper module `_crux_helpers.py`. Three phases force structured output tools per the issue-#23 pattern (`submit_cruxes`, `submit_crux_selection`, `submit_resolution`); free-text `process_response` paths remain the human/fallback layer. The crux-identification phase uses the issue-#22 `next_phase` hook to loop back to crux hunting when no shared crux is found yet (bounded by `MAX_CRUX_SEARCH_ROUNDS`), jump straight to resolution on a values difference, or continue linearly to crux testing on a factual crux. Belief shift on the crux (issue: "the success metric") is captured as participant-stated probabilities at hunt time and resolution time and reported deterministically — the model never computes the numbers. A machine-readable `method_state["crux_map"]` artifact (mirroring MCDA's `decision_artifact`) records the outcome.

**Tech Stack:** Python 3 (stdlib only), pytest, `uv` for environment management.

## Global Constraints

- **`uv` only** — never call `pip` directly. Tests run with `uv run pytest …`.
- **TDD** — each task writes the failing test first, verifies it fails, implements, verifies it passes, commits.
- **Docstrings and type hints mandatory** on every function/method (`docs/llm/golden_rules.md`).
- **No magic numbers** — all thresholds/caps are module constants.
- **Files under ~500 lines.**
- **HANDOVER.md conventions:**
  - Structured-phase conversions keep `process_response` (humans type free text; the structured path falls back after exhausted retries).
  - Every condition-based phase (`rounds=0`) and parse-gated phase needs a give-up cap (`MAX_*` constants) that logs a warning when tripped.
  - Structured tools include a required `reasoning` field rendered before the data display.
  - Never derive a phase turn order by filtering the incoming `entity_ids`; moderator-only phases return `[discussion.moderator_id]`.
  - Structured items are `.strip().rstrip('.')`-normalised to match the regex paths.
  - New `method_state` bookkeeping that must survive a method switch must be added to the preserved set in `app_discussion_flow.switch_discussion_method` (not needed here — Double Crux state is method-local).
- **Branch:** work happens on the current worktree branch `claude/handover-instructions-86694a`; commit after each task.

## File Structure

| File | Responsibility |
|------|----------------|
| `consensus/methods/phases/state_positions.py` (modify) | Add `context_label` constructor param so the handler is reusable outside Adversarial Collaboration |
| `consensus/methods/phases/_crux_helpers.py` (create) | Constants, JSON Schemas, payload validators, record/extract/format pure functions, `build_crux_map` |
| `consensus/methods/phases/hunt_cruxes.py` (create) | Phase 2 handler: participants submit candidate cruxes with belief probabilities (`submit_cruxes`), abort-on-no-cruxes |
| `consensus/methods/phases/identify_crux.py` (create) | Phase 3 handler: moderator-only shared-crux selection (`submit_crux_selection`), verdict routing / hunt loop |
| `consensus/methods/phases/test_crux.py` (create) | Phase 4 handler: free-text evidence-focused discussion of the shared crux |
| `consensus/methods/phases/resolve_crux.py` (create) | Phase 5 handler: structured resolution + belief restatement (`submit_resolution`), builds `crux_map` |
| `consensus/methods/double_crux.py` (create) | `DoubleCrux` method assembly + conclusion prompt |
| `consensus/methods/__init__.py` (modify) | Register `"double_crux"` |
| `consensus/methods/recommender.py` (modify) | `_TAXONOMY` gains a Double Crux line |
| `tests/test_crux_helpers.py` (create) | Helper-module unit tests |
| `tests/test_phases_double_crux.py` (create) | Handler prompts/free-text/advancement/loop-routing/method-level tests |
| `tests/test_double_crux_structured.py` (create) | Structured-output conversion tests (per-#23 convention) |
| `docs/devel/15-discussion-methods.md` (modify) | File list + method table |
| `docs/user_manual/05_discussion_methods.md` (modify) | Method section + "Choosing a Method" row |
| `HANDOVER.md` (modify) | Mark #27 done; record follow-ups |

**Method state keys** (contributed by handler `init_state`, no collisions):
- `positions: dict[str, str]` — entity name → position summary (StatePositionsHandler, existing key)
- `cruxes: list[dict]` — `{"id": int (1-based), "entity_id": int, "entity_name": str, "claim": str, "belief": float | None, "why_pivotal": str}` (HuntCruxesHandler)
- `crux_verdict: str` — `""` (undecided) / `"factual"` / `"values"` / `"none"`; `shared_crux: dict` — `{"claim": str, "description": str, "source_crux_ids": list[int], "initial_beliefs": {name: float}}` or `{}`; `identify_attempts: int`; `crux_search_rounds: int` (IdentifyCruxHandler)
- `resolutions: list[dict]` — `{"entity_id": int, "entity_name": str, "stance": "updated"|"unchanged", "position": str, "crux_belief": float | None, "reasoning": str}`; `crux_map: dict` (ResolveCruxHandler)

**Phase flow** (loop guard: 5 phases × 5 = 25 entries, far above worst case):

```
positions → hunt_cruxes → identify_crux ──factual──→ test_crux → resolve → (end)
                 ↑              │ values ────────────────────────↗
                 └── none (< MAX_CRUX_SEARCH_ROUNDS) ┘   none (exhausted) → resolve
```

---

### Task 1: Parametrize `StatePositionsHandler` (issue #27 asks to reuse it)

**Files:**
- Modify: `consensus/methods/phases/state_positions.py`
- Test: `tests/test_phases_double_crux.py` (new file, first test class)

**Interfaces:**
- Produces: `StatePositionsHandler(context_label: str = "an Adversarial Collaboration")` — the label is interpolated into the system prompt ("participating in {context_label}"). Default preserves current Adversarial Collaboration wording so existing tests keep passing.

- [ ] **Step 1: Write the failing test** — new file `tests/test_phases_double_crux.py`:

```python
"""Tests for the Double Crux phase handlers (issue #27)."""

from consensus.methods.phases.state_positions import StatePositionsHandler
from consensus.models import Discussion, Entity, EntityType


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _adv_discussion() -> Discussion:
    return Discussion(topic="Test topic", discussion_method="adversarial_collab",
                      moderator_id=99)


class TestStatePositionsContextLabel:
    def test_default_label_preserved(self):
        prompt = StatePositionsHandler().get_system_prompt(
            _entity(), _adv_discussion())
        assert "Adversarial" in prompt

    def test_custom_label(self):
        handler = StatePositionsHandler(context_label="a Double Crux session")
        prompt = handler.get_system_prompt(_entity(), _adv_discussion())
        assert "Double Crux session" in prompt
        assert "Adversarial" not in prompt
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_phases_double_crux.py -v` → FAIL (`__init__` takes no arguments).
- [ ] **Step 3: Implement** — in `state_positions.py`, add an `__init__(self, context_label: str = "an Adversarial Collaboration")` storing `self._context_label`, and change the system prompt's first line to `f"You are {entity.name}, participating in {self._context_label}.\n"`.
- [ ] **Step 4: Run tests** — the new file plus `uv run pytest tests/ -k "state_positions or adversarial" -v` → PASS.
- [ ] **Step 5: Commit** — `feat(methods): parametrize StatePositionsHandler context label (#27)`.

---

### Task 2: Crux helper module (`_crux_helpers.py`)

**Files:**
- Create: `consensus/methods/phases/_crux_helpers.py`
- Test: `tests/test_crux_helpers.py`

**Interfaces:**
- Consumes: `consensus.methods.parsing.extract_json_block`, `word_overlap_similar`
- Produces (used by Tasks 3–6):
  - Constants: `MIN_CLAIM_LENGTH = 10`, `SIMILARITY_THRESHOLD = 0.7`, `MAX_HUNT_ROUNDS = 3`, `MAX_CRUX_SEARCH_ROUNDS = 3`, `MAX_IDENTIFY_ATTEMPTS = 3`, `MAX_RESOLVE_ROUNDS = 3`, `TEST_CRUX_ROUNDS = 2`, verdicts `VERDICT_FACTUAL/VERDICT_VALUES/VERDICT_NONE`
  - Schemas: `CRUXES_TOOL_PARAMETERS`, `CRUX_SELECTION_TOOL_PARAMETERS`, `RESOLUTION_TOOL_PARAMETERS`
  - `validate_cruxes_payload(payload: dict) -> str`
  - `record_cruxes(state: dict, entity: Entity, items: list[dict]) -> list[dict]` — per-entity word-overlap dedupe, 1-based ids, claims `.strip().rstrip('.')`-normalised, belief clamped-or-None
  - `extract_cruxes(content: str) -> list[dict]` — JSON block with `"cruxes"` first, else numbered-list claims (belief `None`)
  - `validate_crux_selection_payload(payload: dict, valid_ids: set[int]) -> str`
  - `record_crux_selection(state: dict, payload: dict) -> None` — sets `crux_verdict` + `shared_crux`, snapshots `initial_beliefs` from the referenced cruxes
  - `extract_crux_selection(content: str) -> dict | None` — JSON block with a `"verdict"` key
  - `validate_resolution_payload(payload: dict, require_belief: bool) -> str`
  - `record_resolution(state: dict, entity: Entity, payload: dict) -> None` — one per entity, resubmission replaces own
  - `extract_resolution(content: str) -> dict | None`
  - `entities_with_resolutions(state: dict) -> set[int]`
  - `build_crux_map(state: dict) -> dict` — deterministic outcome artifact incl. belief shifts
  - Formatters: `format_positions(state)`, `format_cruxes(state)`, `format_shared_crux(state)`, `format_belief_shifts(state)`, `format_resolutions(state)`

Schemas (verbatim):

```python
CRUXES_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "cruxes": {
            "type": "array", "minItems": 1, "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string",
                              "description": ("A specific, checkable factual "
                                              "claim that, if you were wrong "
                                              "about it, would change your "
                                              "mind on the topic.")},
                    "belief": {"type": "number", "minimum": 0, "maximum": 1,
                               "description": ("Your current probability "
                                               "that the claim is true.")},
                    "why_pivotal": {"type": "string",
                                    "description": ("Why your position "
                                                    "depends on this claim.")},
                },
                "required": ["claim", "belief", "why_pivotal"],
            },
        },
        "reasoning": {"type": "string",
                      "description": ("How you traced your position back to "
                                      "these load-bearing claims.")},
    },
    "required": ["cruxes", "reasoning"],
}

CRUX_SELECTION_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["factual", "values", "none"],
                    "description": ("'factual': a shared factual crux exists; "
                                    "'values': the disagreement reduces to a "
                                    "value difference; 'none': no shared crux "
                                    "found yet.")},
        "crux_ids": {"type": "array", "items": {"type": "integer"},
                     "description": ("verdict 'factual': ids of the submitted "
                                     "cruxes that express the shared crux — "
                                     "ideally from at least two different "
                                     "participants.")},
        "claim": {"type": "string",
                  "description": ("verdict 'factual': the shared crux as one "
                                  "neutral, checkable claim.  verdict "
                                  "'values': the value difference the "
                                  "disagreement reduces to.")},
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "reasoning"],
}

RESOLUTION_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": ["updated", "unchanged"],
                   "description": "Did crux testing change your position?"},
        "position": {"type": "string",
                     "description": "Your current position, stated fully."},
        "crux_belief": {"type": "number", "minimum": 0, "maximum": 1,
                        "description": ("Your current probability that the "
                                        "shared crux claim is true (required "
                                        "when a factual crux was tested).")},
        "reasoning": {"type": "string",
                      "description": ("What moved you, or why the evidence "
                                      "did not move you.")},
    },
    "required": ["stance", "position", "reasoning"],
}
```

Key semantics to encode (each with a test):
- `validate_cruxes_payload`: non-empty list of dicts; each `claim` a string ≥ `MIN_CLAIM_LENGTH`; each `belief` a bool-rejected number in [0, 1]; each `why_pivotal` non-empty; `reasoning` non-empty.
- `record_cruxes`: skips claims < `MIN_CLAIM_LENGTH`; skips claims word-overlap-similar to one of the *same entity's* earlier cruxes (different entities may — must — submit similar claims: overlap is the shared-crux signal); belief coerced to float and clamped to [0, 1], `None` allowed (free-text path); returns accepted dicts.
- `validate_crux_selection_payload`: verdict must be one of the enum; `"factual"` requires non-empty `crux_ids` all in `valid_ids` **and** a claim ≥ `MIN_CLAIM_LENGTH`; `"values"` requires a claim ≥ `MIN_CLAIM_LENGTH`; `"none"` requires nothing extra; `reasoning` non-empty always.
- `record_crux_selection`: sets `crux_verdict`; for factual builds `shared_crux` with `initial_beliefs` = `{entity_name: belief}` from each referenced crux whose belief is not `None` (last write wins per entity); for values stores claim as `description`; for none stores `{}`.
- `validate_resolution_payload(…, require_belief=True)`: missing/`None` `crux_belief` rejected; `require_belief=False` accepts absence but still range-checks a supplied value; stance must be in enum; position ≥ `MIN_CLAIM_LENGTH`; reasoning non-empty.
- `record_resolution`: replaces the same entity's earlier resolution (resubmission), appends otherwise.
- `build_crux_map`: `{"verdict", "shared_crux", "positions", "cruxes", "resolutions", "belief_shifts", "caveats"}` where `belief_shifts` = `{name: {"initial": float|None, "final": float|None, "shift": float|None}}` for every entity appearing in `initial_beliefs` or `resolutions` (shift only when both ends known, rounded to 2 dp); caveat strings when verdict is `"none"`, when no resolutions were recorded, or when no belief shift could be computed for a factual crux.
- `extract_cruxes` numbered fallback uses `parse_numbered_list(content, min_length=MIN_CLAIM_LENGTH)` mapped to `{"claim": t, "belief": None, "why_pivotal": ""}`.

- [ ] **Step 1: Write failing tests** — `tests/test_crux_helpers.py` covering every bullet above (validators happy/sad paths, dedupe within vs. across entities, belief clamp, selection snapshot, resolution replacement, map shifts/caveats, extract fallbacks, formatters non-empty and containing ids/names).
- [ ] **Step 2: Verify fail** — `uv run pytest tests/test_crux_helpers.py -v` → import error.
- [ ] **Step 3: Implement** `_crux_helpers.py` per the interface above (mirror `_ngt_helpers.py` style: logging, module docstring, type hints).
- [ ] **Step 4: Verify pass.**
- [ ] **Step 5: Commit** — `feat(methods): Double Crux shared helpers (#27)`.

---

### Task 3: Crux hunting handler (`hunt_cruxes.py`)

**Files:**
- Create: `consensus/methods/phases/hunt_cruxes.py`
- Test: `tests/test_phases_double_crux.py` (extend)

**Interfaces:**
- Produces: `HuntCruxesHandler` — `phase.name == "hunt_cruxes"`, `rounds=1`, `requires_structured_output = True`, tool `submit_cruxes`.

Behaviour (mirrors `generate_ideas.py`):
- `init_state` → `{"cruxes": []}`.
- System prompt explains Double Crux and asks the canonical question: *"What factual claim, if you were wrong about it, would change your mind?"* Names the `submit_cruxes` tool; on later search rounds (`crux_search_rounds > 1`) additionally instructs participants to engage with the cruxes others proposed and look for a shared one.
- `process_response` (free-text/human): `extract_cruxes` → `record_cruxes`; warn when nothing extracted.
- `process_structured_response`: `record_cruxes(payload["cruxes"])`; display = reasoning + numbered accepted cruxes with beliefs.
- `should_advance`: give up (warn) when `phase_round > MAX_HUNT_ROUNDS`; else `bool(cruxes) and phase_round > 1`.
- `next_phase`: abort the method (`None`) when the give-up tripped with zero cruxes recorded — every later phase is degenerate without cruxes. `get_method_complete_message` explains the early end.

- [ ] **Step 1: Failing tests** — spec name/params, prompts name the tool, structured/free-text parity into `state["cruxes"]`, advancement (no cruxes → stay; cruxes + round 2 → advance; round > cap → advance with warning), abort routing (`next_phase() is None` iff zero cruxes past cap), completion message non-empty only when aborting.
- [ ] **Step 2: Verify fail.** **Step 3: Implement.** **Step 4: Verify pass.**
- [ ] **Step 5: Commit** — `feat(methods): Double Crux hunt phase (#27)`.

---

### Task 4: Crux identification handler (`identify_crux.py`)

**Files:**
- Create: `consensus/methods/phases/identify_crux.py`
- Test: `tests/test_phases_double_crux.py` (extend)

**Interfaces:**
- Produces: `IdentifyCruxHandler` — `phase.name == "identify_crux"`, `rounds=0` (condition-based), moderator-only (`get_turn_order -> [discussion.moderator_id]`), `requires_structured_output = True`, tool `submit_crux_selection`.

Behaviour (mirrors `cluster_ideas.py` + the #22 loop mechanism):
- `init_state` → `{"crux_verdict": "", "shared_crux": {}, "identify_attempts": 0, "crux_search_rounds": 1}`.
- System prompt shows `format_positions` + `format_cruxes` and defines the three verdicts; validation via `validate_crux_selection_payload` with `valid_ids` from `state["cruxes"]`.
- `process_response` (fallback): `extract_crux_selection`; on a dict that passes validation → record; else increment `identify_attempts` and warn.
- `should_advance`: `crux_verdict != ""` or `identify_attempts >= MAX_IDENTIFY_ATTEMPTS` (warn on give-up).
- `next_phase` routing:
  - verdict `"factual"` → `LINEAR_NEXT` (test_crux is the linear successor);
  - verdict `"values"` → `"resolve"` (nothing factual to test);
  - verdict `"none"` (explicit, or give-up with no verdict): if `crux_search_rounds < MAX_CRUX_SEARCH_ROUNDS` → increment `crux_search_rounds`, reset `crux_verdict` to `""`, return `"hunt_cruxes"` (loop); else finalise `crux_verdict = "none"` and return `"resolve"` (the method still reports a clean disagreement map).
- Transition message summarises how many cruxes were collected.

- [ ] **Step 1: Failing tests** — moderator-only turn order (full roster in → `[moderator_id]` out), spec/prompt naming, structured recording for all three verdicts (incl. `initial_beliefs` snapshot), fallback attempts counter, advancement conditions, and the four routing outcomes of `next_phase` (factual→LINEAR_NEXT, values→"resolve", none-with-rounds-left→"hunt_cruxes" + state reset, none-exhausted→"resolve" + verdict finalised).
- [ ] **Step 2: Verify fail.** **Step 3: Implement.** **Step 4: Verify pass.**
- [ ] **Step 5: Commit** — `feat(methods): Double Crux identification phase with hunt loop (#27)`.

---

### Task 5: Crux testing + resolution handlers (`test_crux.py`, `resolve_crux.py`)

**Files:**
- Create: `consensus/methods/phases/test_crux.py`, `consensus/methods/phases/resolve_crux.py`
- Test: `tests/test_phases_double_crux.py` (extend)

**Interfaces:**
- `TestCruxHandler` — `phase.name == "test_crux"`, `rounds=TEST_CRUX_ROUNDS`, free text (no structured tool). System prompt shows `format_shared_crux` and directs *all* evidence and reasoning at the crux alone (explicitly: do not re-litigate the broader topic; use available research/document tools where they help — the natural #28 hook, noted in HANDOVER as follow-up).
- `ResolveCruxHandler` — `phase.name == "resolve"`, `rounds=1`, `requires_structured_output = True`, tool `submit_resolution`.
  - `init_state` → `{"resolutions": [], "crux_map": {}}`.
  - Prompts branch on `crux_verdict`: factual → restate belief on the crux (`crux_belief` required) + stance; values/none → final position + what the disagreement reduces to.
  - `validate_output` calls `validate_resolution_payload(payload, require_belief=(verdict == VERDICT_FACTUAL))`.
  - `process_response` fallback: `extract_resolution` JSON block → validate (without belief requirement) → record; warn otherwise.
  - `should_advance`: give up (warn) when `phase_round > MAX_RESOLVE_ROUNDS`; else `bool(resolutions) and phase_round > 1`.
  - `next_phase`: build and store `state["crux_map"] = build_crux_map(state)`, then `LINEAR_NEXT` (resolve is last — the method ends).

- [ ] **Step 1: Failing tests** — test_crux prompt contains the shared-crux claim and "crux"-focus instruction; resolve prompt branches per verdict; structured/free-text parity into `resolutions`; belief-required-iff-factual validation; resubmission replaces; give-up advancement; `crux_map` built exactly once on `next_phase` and containing verdict + shifts.
- [ ] **Step 2: Verify fail.** **Step 3: Implement.** **Step 4: Verify pass.**
- [ ] **Step 5: Commit** — `feat(methods): Double Crux testing and resolution phases (#27)`.

---

### Task 6: Method assembly, registration, recommender (`double_crux.py`)

**Files:**
- Create: `consensus/methods/double_crux.py`
- Modify: `consensus/methods/__init__.py`, `consensus/methods/recommender.py`
- Test: `tests/test_phases_double_crux.py` (method-level), `tests/test_double_crux_structured.py` (new, #23 convention), `tests/test_recommender.py` (taxonomy line)

**Interfaces:**
- Produces: `DoubleCrux(DiscussionMethod)` — `name="double_crux"`, `display_name="Double Crux"`, handlers `(StatePositionsHandler(context_label="a Double Crux session"), HuntCruxesHandler(), IdentifyCruxHandler(), TestCruxHandler(), ResolveCruxHandler())`; `get_conclusion_prompt` branches on `crux_verdict` and embeds `format_shared_crux`, `format_belief_shifts`, `format_resolutions`, instructing the moderator to report either the resolution (who updated, on what evidence) or the clean map ("the disagreement reduces to X" / "this is a values difference, not a factual one"), plus preserved disagreement and suggested next steps.
- Registry: `"double_crux": DoubleCrux` in `_METHODS`, class in `__all__` + import.
- Recommender `_TAXONOMY` line (before the Open Discussion line): `- Resolving disagreements by finding the pivotal factual claim beneath them → Double Crux`.

- [ ] **Step 1: Failing tests** —
  - method-level: `get_method("double_crux")` resolves; five phases in order `positions, hunt_cruxes, identify_crux, test_crux, resolve`; `requires_structured_output()` is True; `init_state` contains every documented key; conclusion prompt mentions the crux claim for a factual verdict and "values" for a values verdict; full loop routing through `advance_phase` (none-verdict returns to `hunt_cruxes` and the loop guard never trips in the worst case).
  - structured file mirrors `test_ngt_structured.py`: flags per handler (positions + test_crux False, other three True), spec identity with the schema constants, prompts name their tools, structured/free-text state parity per phase.
  - recommender: taxonomy contains "Double Crux".
- [ ] **Step 2: Verify fail.** **Step 3: Implement.** **Step 4: Run the full suite** — `uv run pytest tests/ -q` → all pass (1994 + new).
- [ ] **Step 5: Commit** — `feat(methods): Double Crux method (#27)`.

---

### Task 7: Docs, HANDOVER, PR

**Files:**
- Modify: `docs/devel/15-discussion-methods.md` (file list + method table row), `docs/user_manual/05_discussion_methods.md` (method section + "Choosing a Method" row), `HANDOVER.md` (mark #27 done, pin the PR, record follow-ups: real-pipeline flow test still owed for NGT/MCDA/Double Crux; #28 evidence-gating hook noted in test_crux)

- [ ] **Step 1: Update the three docs** following the Weighted Decision Matrix precedent (commit 5263bec).
- [ ] **Step 2: Full suite** — `uv run pytest tests/ -q` → all pass.
- [ ] **Step 3: Commit** — `docs: document Double Crux; update handover (#27)`.
- [ ] **Step 4: Push branch and open a PR** titled `feat: Double Crux discussion method (#27)` against `main`, body summarising phases, loop routing, belief-shift metric, crux_map artifact.

## Self-Review

- **Spec coverage:** issue steps 1–4 map to phases 1–5 (positions → hunt+identify → test → resolve); "reuse state_positions" → Task 1; "iterate until shared crux or explicit value difference" → identify loop + values verdict; "focuses evidence on the crux alone" → TestCruxHandler prompt; "clean map" → crux_map + conclusion; "belief shift as success metric" → initial_beliefs snapshot + crux_belief + belief_shifts. Citations requirement is deliberately deferred to #28 (cross-cutting), noted in prompts/docs.
- **Type consistency:** state-key shapes declared once in File Structure and referenced by every task; validator/record signatures fixed in Task 2 and consumed verbatim in Tasks 3–5.
- **Placeholders:** condensed handler tasks intentionally reference the two fully-specified precedent files (`generate_ideas.py`, `cluster_ideas.py`) they mirror; all novel logic (routing table, schemas, validation semantics, crux_map shape) is spelled out.
