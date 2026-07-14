# Tree of Thoughts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement issue #26 — Tree of Thoughts, an iterative generative method: participants propose parallel solution approaches, everyone scores them on feasibility/impact/risk, a deterministic beam prune keeps the top 2–3, survivors get a deep-dive round, and the score→prune→expand loop repeats until the beam stabilises or a depth budget is spent, then the moderator synthesises.

**Architecture:** One new `DiscussionMethod` (`consensus/methods/tree_of_thoughts.py`) assembled from five new composable `PhaseHandler`s over a shared pure-function helper module `_tot_helpers.py`. Three phases force structured output tools per the issue-#23 pattern (`submit_thoughts`, `submit_thought_scores`, `submit_expansions`); free-text `process_response` paths remain the human/fallback layer. The prune phase uses the issue-#22 `next_phase` hook to loop back through expand→score or jump to synthesis; the beam, composites, convergence verdict, and final `tot_artifact` are all computed deterministically in code — never by the model. Design spec: `docs/superpowers/specs/2026-07-14-tree-of-thoughts-design.md`.

**Tech Stack:** Python 3 (stdlib only), pytest, `uv` for environment management.

## Global Constraints

- **`uv` only** — never call `pip` directly. Tests run with `uv run pytest …`.
- **TDD** — each task writes the failing test first, verifies it fails, implements, verifies it passes, commits.
- **Docstrings and type hints mandatory** (`docs/llm/golden_rules.md`); **no magic numbers** (module constants); **files under ~500 lines**.
- **HANDOVER.md conventions:**
  - Structured phases keep `process_response` as the human/fallback path.
  - Structured tools include a required `reasoning` field rendered before the data display.
  - Every parse-gated / condition-based phase has a give-up cap that logs a warning.
  - Moderator-only phases return `[discussion.moderator_id]` from `get_turn_order`; never filter the incoming `entity_ids`.
  - Structured items are `.strip().rstrip('.')`-normalised to match the regex paths.
  - All ToT state is method-local — nothing added to the `switch_discussion_method` preserved set.
- **Branch:** current worktree branch `claude/handover-instructions-80a7dd`; commit after each task.

## File Structure

| File | Responsibility |
|------|----------------|
| `consensus/methods/phases/_tot_helpers.py` (create) | Constants, JSON Schemas, payload validators, record/extract/format pure functions, composite/beam computation, `build_tot_artifact` |
| `consensus/methods/phases/propose_thoughts.py` (create) | Phase 1: anonymised independent approach generation (`submit_thoughts`), abort-on-empty |
| `consensus/methods/phases/score_thoughts.py` (create) | Phase 2: everyone scores eligible thoughts on feasibility/impact/risk (`submit_thought_scores`) |
| `consensus/methods/phases/prune_thoughts.py` (create) | Phase 3: moderator presents the deterministic beam; `next_phase` routing (loop / converge / depth budget / degenerate) |
| `consensus/methods/phases/expand_thoughts.py` (create) | Phase 4: deep-dive of beam survivors (`submit_expansions`); always loops back to `score` |
| `consensus/methods/phases/synthesise_thoughts.py` (create) | Phase 5: moderator-only presentation of the outcome |
| `consensus/methods/tree_of_thoughts.py` (create) | `TreeOfThoughts` method assembly + conclusion prompt |
| `consensus/methods/__init__.py` (modify) | Register `"tree_of_thoughts"` |
| `consensus/methods/recommender.py` (modify) | `_TAXONOMY` gains a ToT line |
| `tests/test_tot_helpers.py` (create) | Helper-module unit tests |
| `tests/test_phases_tot.py` (create) | Handler prompts/fallbacks/advancement/routing/method-level tests |
| `tests/test_tot_structured.py` (create) | Structured-output conversion tests (per-#23 convention) |
| `docs/devel/15-discussion-methods.md` (modify) | File list + method table |
| `docs/user_manual/05_discussion_methods.md` (modify) | Method section + "Choosing a Method" row |
| `HANDOVER.md` (modify) | Mark #26 done; record follow-ups |

**Method state keys** (contributed by handler `init_state`, no collisions):
- `thoughts: list[dict]` — `{"id": int (1-based), "entity_id": int, "entity_name": str, "text": str}` (ProposeThoughtsHandler)
- `thought_scores: dict[str, dict[str, dict[str, int]]]` — entity id (str) → thought label (`"T3"`) → `{"feasibility": int, "impact": int, "risk": int}` (ScoreThoughtsHandler)
- `beam_history: list[dict]` — `{"depth": int, "beam_ids": list[int], "ranking": [{"id", "composite", "scorer_count"}]}`; `tot_artifact: dict` (PruneThoughtsHandler)
- `expansions: list[dict]` — `{"depth": int, "entity_id": int, "entity_name": str, "thought_id": int, "refinement": str, "obstacles": list[str]}` (ExpandThoughtsHandler)

**Phase flow** (loop guard: 5 phases × 5 = 25 entries; worst case ≈ 9):

```
propose → score → prune ──(continue)──→ expand ──→ score (loop)
                    │
                    └──(converged | depth budget | degenerate)──→ synthesise → (end)
```

**Constants** (all in `_tot_helpers.py`): `SCORE_MIN = 1`, `SCORE_MAX = 5`, `DEFAULT_DIMENSION_SCORE = 3`, `DIMENSIONS = ("feasibility", "impact", "risk")`, `BEAM_WIDTH = 3`, `MAX_TOT_DEPTH = 3`, `MAX_PROPOSE_ROUNDS = 3`, `MIN_THOUGHT_LENGTH = 10`, `MIN_REFINEMENT_LENGTH = 10`, `SIMILARITY_THRESHOLD = 0.7`.

---

### Task 1: `_tot_helpers.py` — pure helper module

**Files:**
- Create: `consensus/methods/phases/_tot_helpers.py`
- Test: `tests/test_tot_helpers.py`

**Interfaces (produced, relied on by Tasks 2–6):**
- `thought_label(thought_id: int) -> str` — `"T3"`.
- `validate_thoughts_payload(payload: dict) -> str` — `''` or error; array of strings ≥ `MIN_THOUGHT_LENGTH`, required non-blank `reasoning`.
- `record_thoughts(state, entity, texts: list[str]) -> list[dict]` — dedup (word-overlap, `SIMILARITY_THRESHOLD`), min-length gate, `.strip().rstrip('.')`, 1-based ids; returns accepted dicts (the `record_options` pattern).
- `eligible_thoughts(state) -> list[dict]` — all thoughts before the first prune, else the thoughts in the latest `beam_history` entry's `beam_ids`.
- `validate_scores_payload(payload: dict, eligible: list[dict]) -> str` — labels ⊆ eligible labels; each entry an object with exactly the three `DIMENSIONS`, ints in `[SCORE_MIN, SCORE_MAX]` (bools rejected); required `reasoning`.
- `record_thought_scores(state, entity, scores: dict) -> int` — sanitise (unknown labels/dims dropped, bools rejected, int-coercion on the free-text path), **merge per thought label** into `state["thought_scores"][str(entity.id)]` so a re-score replaces only that entity's entry for that thought; returns cells kept.
- `composite_of(dims: dict[str, int]) -> int` — `feasibility + impact + (SCORE_MIN + SCORE_MAX - risk)`.
- `thought_composites(state) -> dict[int, dict]` — per eligible thought id: `{"composite": float (mean over scorers, 2 dp), "scorer_count": int}`; zero scorers → all-midpoint composite via `composite_of`.
- `compute_beam(state) -> tuple[list[int], list[dict]]` — ranking sorted by `(-composite, id)`; beam = first `BEAM_WIDTH` ids.
- `current_depth(state) -> int` — `len(state.get("beam_history", []))`.
- `validate_expansions_payload(payload: dict, beam_ids: set[int]) -> str` — each entry `{thought_id ∈ beam, refinement ≥ MIN_REFINEMENT_LENGTH, obstacles?: [str]}`; required `reasoning`.
- `record_expansions(state, entity, items: list[dict], depth: int) -> int` — skip unknown ids / short refinements, coerce obstacles to `list[str]`; returns accepted count.
- `build_tot_artifact(state, stop_reason: str) -> dict` — per the spec's artifact shape; caveats for zero scorers, defaulted (unscored) survivors, and non-converged stops.
- `extract_json_payload(content: str, key: str) -> dict | list | None` — fenced JSON block first, then inline balanced-brace scan starting at `{"<key>"` (the `extract_scores` pattern, generalised).
- Formatters: `format_thoughts(thoughts) -> str`, `format_ranking(state) -> str`, `format_beam(state) -> str`, `format_expansions(state, depth) -> str`, `format_beam_trajectory(state) -> str`.
- Constants above; `STOP_CONVERGED = "converged"`, `STOP_DEPTH = "depth_budget"`, `STOP_DEGENERATE = "degenerate"`.
- Schemas: `THOUGHTS_TOOL_PARAMETERS`, `SCORES_TOOL_PARAMETERS` (labels via `additionalProperties` → object with the three required dimension ints, `minimum`/`maximum` set), `EXPANSIONS_TOOL_PARAMETERS`.

- [ ] **Step 1: Write failing tests** — `tests/test_tot_helpers.py` with an `Entity` fixture (mirror `tests/test_ngt_helpers.py`). Representative cases (write all):

```python
class TestRecordThoughts:
    def test_dedups_word_overlap_and_strips(self, alice): ...
    def test_rejects_short_thoughts(self, alice): ...
    def test_ids_are_sequential_across_entities(self, alice, bob): ...

class TestScores:
    def test_validate_rejects_unknown_label_naming_valid_set(self): ...
    def test_validate_rejects_missing_dimension(self): ...
    def test_validate_rejects_bool_and_out_of_range(self): ...
    def test_record_merges_per_thought_not_wholesale(self, alice): ...
    def test_record_coerces_numeric_strings_drops_junk(self, alice): ...

class TestBeam:
    def test_composite_inverts_risk(self): ...          # f=5,i=5,r=1 → 14
    def test_ranking_mean_over_scorers_tiebreak_by_id(self, alice, bob): ...
    def test_unscored_thought_defaults_to_midpoint_composite(self): ...
    def test_beam_is_top_beam_width(self): ...
    def test_eligible_thoughts_all_then_latest_beam(self): ...

class TestExpansions:
    def test_validate_rejects_id_outside_beam(self): ...
    def test_record_skips_short_refinement_coerces_obstacles(self, alice): ...

class TestArtifact:
    def test_artifact_shape_and_recommendation_is_top_ranked(self): ...
    def test_caveats_zero_scorers_and_stop_reason(self): ...

class TestExtractors:
    def test_fenced_json_block(self): ...
    def test_inline_balanced_braces(self): ...
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_tot_helpers.py -q` → import error.
- [ ] **Step 3: Implement `_tot_helpers.py`** (constants, schemas, functions per Interfaces; module docstring citing issue #26).
- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_tot_helpers.py -q`.
- [ ] **Step 5: Commit** — `feat(methods): ToT helper module (#26)`.

---

### Task 2: `ProposeThoughtsHandler`

**Files:** Create `consensus/methods/phases/propose_thoughts.py`; tests in `tests/test_phases_tot.py` + `tests/test_tot_structured.py` (new files).

**Interfaces:** `ProposeThoughtsHandler` — `phase = Phase(name="propose", display_name="Parallel Approach Generation", rounds=1)`; `requires_structured_output = True`; `init_state` → `{"thoughts": []}`; anonymised context (`anonymise_content` from `._delphi_helpers`, the `generate_ideas.py` pattern); `get_output_tool` → `submit_thoughts`; free-text fallback `parse_numbered_list(content, min_length=MIN_THOUGHT_LENGTH)`; `should_advance` — thoughts recorded and `phase_round > 1`, or `phase_round > MAX_PROPOSE_ROUNDS` (warn); `next_phase` — `None` (abort) when give-up with zero thoughts, else `LINEAR_NEXT`; `get_method_complete_message` explains the abort.

- [ ] Step 1: failing tests (prompts name the tool; anonymisation; fallback parses numbered list; abort path; structured spec/validate/process incl. display = reasoning + numbered accepted thoughts).
- [ ] Step 2: verify fail. Step 3: implement (mirror `generate_ideas.py`, ToT wording: "distinct solution approaches — different strategies, not variations of one"). Step 4: verify pass. Step 5: commit `feat(methods): ToT propose phase (#26)`.

---

### Task 3: `ScoreThoughtsHandler`

**Files:** Create `consensus/methods/phases/score_thoughts.py`; extend both test files.

**Interfaces:** `phase = Phase(name="score", display_name="Candidate Scoring", rounds=1)`; `requires_structured_output = True`; `init_state` → `{"thought_scores": {}}`; prompts show `format_thoughts(eligible_thoughts(state))`, and on re-score passes (`current_depth(state) > 0`) also `format_expansions(state, current_depth(state))` with a "re-score in light of the deep-dives" instruction; `get_output_tool` → `submit_thought_scores` (description embeds eligible labels), **returns `None` when no thoughts exist** (defensive degenerate guard); `validate_output` via `validate_scores_payload`; fallback `extract_json_payload(content, "scores")` → `record_thought_scores`; summary prompt notes divergent scores (MCDA pattern); transition message differs for first pass vs re-score pass.

- [ ] Step 1: failing tests (eligible-set restriction after a beam exists; re-score prompt includes expansions; structured + fallback recording; `get_output_tool` None on empty). Step 2: fail. Step 3: implement. Step 4: pass. Step 5: commit `feat(methods): ToT score phase (#26)`.

---

### Task 4: `PruneThoughtsHandler` — beam + routing

**Files:** Create `consensus/methods/phases/prune_thoughts.py`; extend `tests/test_phases_tot.py`.

**Interfaces:** `phase = Phase(name="prune", display_name="Beam Pruning", rounds=1)`; **not** structured (presentational); `init_state` → `{"beam_history": [], "tot_artifact": {}}`; `get_turn_order` → `[discussion.moderator_id]`; system/turn prompts show `format_ranking(state)` and the computed beam (pure reads — no mutation in prompt hooks); `process_response` — display only; `next_phase(discussion)`:

```python
def next_phase(self, discussion: Discussion) -> str | None:
    state = discussion.method_state
    beam_ids, ranking = compute_beam(state)
    prev = state["beam_history"][-1]["beam_ids"] if state.get("beam_history") else None
    state.setdefault("beam_history", []).append(
        {"depth": current_depth(state) + 1, "beam_ids": beam_ids, "ranking": ranking})
    # Ordered equality: eligibility restricts scoring to the previous
    # beam, so the id SET is vacuously stable after the first prune —
    # only the order can move, and a stable order means convergence.
    converged = prev is not None and prev == beam_ids
    degenerate = len(beam_ids) < MIN_BEAM_SIZE          # MIN_BEAM_SIZE = 2
    depth_spent = current_depth(state) >= MAX_TOT_DEPTH
    if converged or degenerate or depth_spent:
        reason = (STOP_CONVERGED if converged
                  else STOP_DEGENERATE if degenerate else STOP_DEPTH)
        state["tot_artifact"] = build_tot_artifact(state, reason)
        logger.info(...)
        return "synthesise"
    return LINEAR_NEXT  # → expand
```

(Note `MIN_BEAM_SIZE = 2` joins the Task 1 constants.) Transition message announces scores are in and the moderator will present the cut.

- [ ] Step 1: failing tests — routing table: first prune with ≥2 candidates → `LINEAR_NEXT`; identical beam on second prune → `"synthesise"` + artifact `stop_reason == "converged"`; changed beam under depth budget → continue; third prune → `"synthesise"` (`depth_budget`); single thought → `"synthesise"` (`degenerate`); `beam_history` grows once per call; moderator-only turn order; prompts contain ranking labels. Step 2: fail. Step 3: implement. Step 4: pass. Step 5: commit `feat(methods): ToT prune phase with beam routing (#26)`.

---

### Task 5: `ExpandThoughtsHandler`

**Files:** Create `consensus/methods/phases/expand_thoughts.py`; extend both test files.

**Interfaces:** `phase = Phase(name="expand", display_name="Deep-Dive Expansion", rounds=1)`; `requires_structured_output = True`; `init_state` → `{"expansions": []}`; prompts show `format_beam(state)` and ask for refinement + obstacles per survivor; `get_output_tool` → `submit_expansions` (None when beam empty — defensive); `validate_output` via `validate_expansions_payload` with beam ids; structured/fallback paths record via `record_expansions(state, entity, items, current_depth(state))`; fallback parse `extract_json_payload(content, "expansions")`; `next_phase` → `"score"` always (the loop edge — reachable only when prune chose to continue).

- [ ] Step 1: failing tests (beam-restricted validation; depth tagging; `next_phase() == "score"`; display = reasoning + per-thought refinement/obstacle rendering). Step 2: fail. Step 3: implement. Step 4: pass. Step 5: commit `feat(methods): ToT expand phase (#26)`.

---

### Task 6: `SynthesiseThoughtsHandler`, `TreeOfThoughts` method, registration

**Files:** Create `consensus/methods/phases/synthesise_thoughts.py`, `consensus/methods/tree_of_thoughts.py`; modify `consensus/methods/__init__.py`, `consensus/methods/recommender.py`; extend `tests/test_phases_tot.py`.

**Interfaces:** `SynthesiseThoughtsHandler` — `phase = Phase(name="synthesise", display_name="Synthesis", rounds=1)`; moderator-only; free-text; prompts present `tot_artifact` (recommendation, trajectory via `format_beam_trajectory`, obstacles). `TreeOfThoughts` — `name = "tree_of_thoughts"`, `display_name = "Tree of Thoughts"`, handlers `(Propose, Score, Prune, Expand, Synthesise)`; `get_conclusion_prompt` builds stop-reason-specific synthesis instructions from the artifact (numbers quoted from state, `double_crux.py` pattern). Registry: `_METHODS["tree_of_thoughts"]`, import, `__all__`. Recommender `_TAXONOMY` += "Open-ended problem-solving by exploring, scoring, and iteratively refining parallel solution paths → Tree of Thoughts".

- [ ] Step 1: failing tests (method metadata/phase order; `requires_structured_output()` True; `get_method("tree_of_thoughts")`; `list_methods` contains it; conclusion prompt quotes artifact numbers and varies by stop reason; taxonomy line present). Step 2: fail. Step 3: implement. Step 4: pass — plus `uv run pytest tests/ -q` full suite. Step 5: commit `feat(methods): Tree of Thoughts method (#26)`.

---

### Task 7: Documentation + HANDOVER

**Files:** Modify `docs/devel/15-discussion-methods.md`, `docs/user_manual/05_discussion_methods.md`, `HANDOVER.md`, `CLAUDE.md` (method count line).

- [ ] Dev doc: file list + method table row. User manual: method section (what it is, when to use, phase walk-through) + choosing-table row. HANDOVER: mark #26 done with the session summary block (PR number placeholder until opened); refresh "Next steps". CLAUDE.md: "17 method classes" → 18.
- [ ] Commit `docs: document Tree of Thoughts; update handover (#26)`.

---

### Task 8: Full verification + self-review

- [ ] `uv run pytest tests/ -q` — full suite green.
- [ ] Code-review pass on the branch diff (correctness, conventions, file sizes < ~500 lines); fix findings, re-run suite.
- [ ] Push branch, open PR referencing #26.
