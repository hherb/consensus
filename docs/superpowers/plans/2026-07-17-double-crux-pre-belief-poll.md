# Double Crux pre-belief poll — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `poll_belief` phase to Double Crux so every disagreeing party states their probability on the moderator's synthesized shared claim, making that the authoritative source of the belief-shift metric's "before" value.

**Architecture:** A new composable `PhaseHandler` (`PollBeliefHandler`) inserted between `identify_crux` and `test_crux`. It runs only on the factual path (identify's existing routing already jumps `values`/`none` straight to `resolve`). Poll results replace the current hunt-phase `initial_beliefs` snapshot. All numeric aggregation stays in pure helper functions in `_crux_helpers.py`; `build_crux_map` / `format_belief_shifts` are unchanged because they already read `shared_crux["initial_beliefs"]`.

**Tech Stack:** Python 3, `pytest` (`uv run pytest`), the project's composable-phase framework (`consensus/methods/phase_handler.py`, `base.py`), structured-output tool-call convention (issue #23).

Spec: `docs/superpowers/specs/2026-07-17-double-crux-pre-belief-poll-design.md`

## Global Constraints

- **Package manager:** `uv` only — never `pip`. Run tests with `uv run pytest`.
- **No magic numbers:** every cap is a named constant in `_crux_helpers.py` (e.g. `MAX_POLL_ROUNDS = 3`, matching `MAX_RESOLVE_ROUNDS`).
- **Docstrings + type hints mandatory** on every new function and method.
- **Files stay under ~500 lines**; the new handler is its own module.
- **TDD:** write the failing test first, watch it fail, implement minimally, watch it pass, commit.
- **All tests must pass before every commit** (`uv run pytest`). Each task below ends green.
- **Structured phases keep a free-text `process_response` fallback** (humans type free text); `reasoning` is a required field rendered before the data.
- **Commit trailer:** end every commit message with
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## File Structure

- Create `consensus/methods/phases/poll_belief.py` — `PollBeliefHandler` (one phase, one responsibility).
- Modify `consensus/methods/phases/_crux_helpers.py` — add poll constants/schema/helpers (Task 1); remove the hunt-snapshot from `record_crux_selection` (Task 4).
- Modify `consensus/methods/double_crux.py` — insert the handler into `phase_handlers` (Task 3).
- Modify `consensus/methods/phases/identify_crux.py` — reword the belief-carry-over sentence (Task 3).
- Modify tests: `tests/test_crux_helpers.py`, `tests/test_phases_double_crux.py`, `tests/test_double_crux_structured.py`, `tests/test_crux_artifact.py`, `tests/test_method_flow_e2e.py`.

---

### Task 1: Poll helpers, schema, and constant in `_crux_helpers.py`

Purely additive pure functions — no existing behavior changes, so the suite stays green.

**Files:**
- Modify: `consensus/methods/phases/_crux_helpers.py`
- Test: `tests/test_crux_helpers.py`

**Interfaces:**
- Consumes: existing `_belief_error`, `extract_json_block` (already imported in the module).
- Produces:
  - `MAX_POLL_ROUNDS: int`
  - `POLL_BELIEF_TOOL_PARAMETERS: dict`
  - `validate_poll_belief_payload(payload: dict) -> str`
  - `record_poll_belief(state: dict, entity: Entity, payload: dict) -> None`
  - `entities_with_poll(state: dict) -> set[int]`
  - `extract_poll_belief(content: str) -> dict | None`
  - `apply_poll_beliefs(state: dict) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crux_helpers.py`. First add the new names to the existing import block from `consensus.methods.phases._crux_helpers` (add `MAX_POLL_ROUNDS`, `POLL_BELIEF_TOOL_PARAMETERS`, `apply_poll_beliefs`, `entities_with_poll`, `extract_poll_belief`, `record_poll_belief`, `validate_poll_belief_payload`). Then append:

```python
class TestValidatePollBelief:
    def test_accepts_valid(self):
        assert validate_poll_belief_payload(
            {"belief": 0.6, "reasoning": "prior evidence leans this way"}) == ""

    def test_rejects_out_of_range_belief(self):
        assert validate_poll_belief_payload(
            {"belief": 1.5, "reasoning": "r"}) != ""

    def test_rejects_non_numeric_belief(self):
        assert validate_poll_belief_payload(
            {"belief": "high", "reasoning": "r"}) != ""

    def test_rejects_boolean_belief(self):
        assert validate_poll_belief_payload(
            {"belief": True, "reasoning": "r"}) != ""

    def test_requires_reasoning(self):
        assert validate_poll_belief_payload({"belief": 0.5}) != ""
        assert validate_poll_belief_payload(
            {"belief": 0.5, "reasoning": "  "}) != ""


class TestRecordPollBelief:
    def test_appends_entry(self):
        state: dict = {}
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 0.7, "reasoning": "prior data"})
        assert state["poll_beliefs"] == [{
            "entity_id": 1, "entity_name": "Alice",
            "belief": 0.7, "reasoning": "prior data"}]

    def test_replaces_own_entry(self):
        state: dict = {}
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 0.7, "reasoning": "first"})
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 0.3, "reasoning": "revised"})
        assert len(state["poll_beliefs"]) == 1
        assert state["poll_beliefs"][0]["belief"] == 0.3

    def test_coerces_belief_to_float(self):
        state: dict = {}
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 1, "reasoning": "certain"})
        assert state["poll_beliefs"][0]["belief"] == 1.0


class TestEntitiesWithPoll:
    def test_returns_polled_ids(self):
        state: dict = {}
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 0.7, "reasoning": "r"})
        record_poll_belief(state, _entity(2, "Bob"),
                           {"belief": 0.2, "reasoning": "r"})
        assert entities_with_poll(state) == {1, 2}

    def test_empty_when_none(self):
        assert entities_with_poll({}) == set()


class TestExtractPollBelief:
    def test_reads_json_block(self):
        content = '```json\n{"belief": 0.4, "reasoning": "r"}\n```'
        assert extract_poll_belief(content) == {"belief": 0.4,
                                                "reasoning": "r"}

    def test_none_without_belief_key(self):
        assert extract_poll_belief("I am about 40% sure.") is None
        assert extract_poll_belief('```json\n{"reasoning": "r"}\n```') is None


class TestApplyPollBeliefs:
    def test_folds_into_initial_beliefs(self):
        state: dict = {"shared_crux": {"claim": "c", "initial_beliefs": {}}}
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 0.7, "reasoning": "r"})
        record_poll_belief(state, _entity(2, "Bob"),
                           {"belief": 0.2, "reasoning": "r"})
        apply_poll_beliefs(state)
        assert state["shared_crux"]["initial_beliefs"] == {
            "Alice": 0.7, "Bob": 0.2}

    def test_replaces_prior_initial_beliefs(self):
        # Any snapshot value present is overwritten by the poll (replace,
        # not merge) — the poll is authoritative.
        state: dict = {"shared_crux": {
            "claim": "c", "initial_beliefs": {"Alice": 0.99}}}
        record_poll_belief(state, _entity(1, "Alice"),
                           {"belief": 0.7, "reasoning": "r"})
        apply_poll_beliefs(state)
        assert state["shared_crux"]["initial_beliefs"] == {"Alice": 0.7}

    def test_empty_poll_yields_empty_beliefs(self):
        state: dict = {"shared_crux": {"claim": "c", "initial_beliefs": {}}}
        apply_poll_beliefs(state)
        assert state["shared_crux"]["initial_beliefs"] == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_crux_helpers.py -k "Poll or ApplyPoll or EntitiesWithPoll" -q`
Expected: FAIL at import — `ImportError: cannot import name 'MAX_POLL_ROUNDS'`.

- [ ] **Step 3: Add the constant, schema, and helpers**

In `consensus/methods/phases/_crux_helpers.py`, add the constant next to the other caps (after `MAX_RESOLVE_ROUNDS`):

```python
#: Give up and advance after this many belief-poll rounds.
MAX_POLL_ROUNDS = 3
```

Add the schema after `RESOLUTION_TOOL_PARAMETERS`:

```python
#: JSON Schema for the submit_crux_belief output tool (belief poll).
POLL_BELIEF_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "belief": {
            "type": "number", "minimum": 0, "maximum": 1,
            "description": ("Your current probability (0-1) that the "
                            "shared crux claim is true, before evidence "
                            "is presented."),
        },
        "reasoning": {
            "type": "string",
            "description": ("Why you hold that probability right now."),
        },
    },
    "required": ["belief", "reasoning"],
}
```

Add the helpers (near `record_resolution` / `entities_with_resolutions`):

```python
def validate_poll_belief_payload(payload: dict) -> str:
    """Return '' if a submit_crux_belief payload is usable, else an error."""
    error = _belief_error(payload.get("belief"))
    if error:
        return error
    if not str(payload.get("reasoning") or "").strip():
        return "'reasoning' must explain your current probability."
    return ""


def record_poll_belief(state: dict, entity: Entity, payload: dict) -> None:
    """Record an entity's crux-belief poll; resubmission replaces their own.

    Shared by the free-text and structured paths.  ``belief`` is
    float-coerced; ``None`` (defensive — the validated paths never pass
    it) is preserved so ``apply_poll_beliefs`` can skip it.
    """
    belief = payload.get("belief")
    entry = {
        "entity_id": entity.id,
        "entity_name": entity.name,
        "belief": None if belief is None else float(belief),
        "reasoning": str(payload.get("reasoning") or "").strip(),
    }
    polls = state.setdefault("poll_beliefs", [])
    for i, existing in enumerate(polls):
        if existing["entity_id"] == entity.id:
            polls[i] = entry
            return
    polls.append(entry)


def entities_with_poll(state: dict) -> set[int]:
    """Entity ids that have a recorded crux-belief poll."""
    return {e["entity_id"] for e in state.get("poll_beliefs", [])}


def extract_poll_belief(content: str) -> dict | None:
    """Parse a crux-belief poll from free text (fallback path).

    Only a fenced JSON block with a ``belief`` key is accepted.
    """
    data = extract_json_block(content)
    if isinstance(data, dict) and "belief" in data:
        return data
    return None


def apply_poll_beliefs(state: dict) -> None:
    """Replace ``shared_crux['initial_beliefs']`` with the polled values.

    The poll is the authoritative source of the belief-shift metric's
    "before" end (design 2026-07-17): a name->belief map built purely
    from ``poll_beliefs``, dropping any ``None`` belief.  Called once
    when the poll phase completes.
    """
    beliefs = {e["entity_name"]: e["belief"]
               for e in state.get("poll_beliefs", [])
               if e.get("belief") is not None}
    state.setdefault("shared_crux", {})["initial_beliefs"] = beliefs
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_crux_helpers.py -q`
Expected: PASS (existing + new tests).

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/_crux_helpers.py tests/test_crux_helpers.py
git commit -m "$(cat <<'EOF'
feat(double-crux): add belief-poll helpers to _crux_helpers

Pure functions + schema + MAX_POLL_ROUNDS cap for the new poll_belief
phase: validate/record/extract a per-party crux-belief poll, and
apply_poll_beliefs to fold the polls into shared_crux.initial_beliefs.
Additive only — no existing behavior changes.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `PollBeliefHandler` phase handler

Create the handler and test it directly (not yet wired into the method), so the suite stays green.

**Files:**
- Create: `consensus/methods/phases/poll_belief.py`
- Test: `tests/test_phases_double_crux.py`, `tests/test_double_crux_structured.py`

**Interfaces:**
- Consumes (Task 1): `MAX_POLL_ROUNDS`, `POLL_BELIEF_TOOL_PARAMETERS`, `apply_poll_beliefs`, `entities_with_poll`, `extract_poll_belief`, `record_poll_belief`, `validate_poll_belief_payload`, `format_shared_crux`; framework `LINEAR_NEXT`, `OutputToolSpec`, `Phase`, `ProcessedResponse`, `PhaseHandler`.
- Produces: `PollBeliefHandler` with phase name `"poll_belief"`, `requires_structured_output = True`, output tool `submit_crux_belief`, and `init_state -> {"poll_beliefs": []}`.

- [ ] **Step 1: Add `poll_beliefs` to the shared test fixture and write the failing handler tests**

In `tests/test_phases_double_crux.py`, add `"poll_beliefs": []` to the `_crux_discussion` state dict (so `_factual_discussion`, built on it, carries the key), and add `PollBeliefHandler` plus the `MAX_POLL_ROUNDS` import:

```python
from consensus.methods.phases._crux_helpers import (
    MAX_CRUX_SEARCH_ROUNDS,
    MAX_HUNT_ROUNDS,
    MAX_IDENTIFY_ATTEMPTS,
    MAX_POLL_ROUNDS,
    VERDICT_FACTUAL,
    VERDICT_NONE,
    VERDICT_VALUES,
    record_cruxes,
)
from consensus.methods.phases.poll_belief import PollBeliefHandler
```

The `_crux_discussion` state dict becomes:

```python
    disc.method_state = {
        "current_phase": phase, "phase_round": 1,
        "positions": {}, "cruxes": [],
        "crux_verdict": "", "shared_crux": {},
        "identify_attempts": 0, "crux_search_rounds": 1,
        "poll_beliefs": [],
        "resolutions": [], "crux_map": {},
    }
```

Append the handler tests:

```python
class TestPollBeliefPrompts:
    def test_system_prompt_shows_the_claim_and_names_the_tool(self):
        disc = _factual_discussion(phase="poll_belief")
        system = PollBeliefHandler().get_system_prompt(_entity(), disc)
        assert CLAIM_A in system
        assert "submit_crux_belief" in system
        assert "probability" in system.lower()

    def test_turn_prompt_names_the_tool(self):
        disc = _factual_discussion(phase="poll_belief")
        assert "submit_crux_belief" in PollBeliefHandler().get_turn_prompt(
            _entity(), disc)

    def test_default_turn_order_is_not_overridden(self):
        # The poll runs for the full non-moderator roster, like resolve.
        disc = _factual_discussion(phase="poll_belief")
        assert PollBeliefHandler().get_turn_order([1, 2], disc) == [1, 2]


class TestPollBeliefProcessing:
    def test_structured_records_and_renders_reasoning_first(self):
        handler = PollBeliefHandler()
        disc = _factual_discussion(phase="poll_belief")
        result = handler.process_structured_response(
            {"belief": 0.6, "reasoning": "Prior studies lean this way."},
            _entity(1, "Alice"), disc)
        assert disc.method_state["poll_beliefs"][0]["belief"] == 0.6
        assert result.display_content.startswith("Prior studies lean")
        assert "0.6" in result.display_content

    def test_free_text_json_records_belief(self):
        handler = PollBeliefHandler()
        disc = _factual_discussion(phase="poll_belief")
        content = '```json\n{"belief": 0.3, "reasoning": "sceptical"}\n```'
        handler.process_response(content, _entity(1, "Alice"), disc)
        assert disc.method_state["poll_beliefs"][0]["belief"] == 0.3

    def test_free_text_unparseable_records_nothing(self):
        handler = PollBeliefHandler()
        disc = _factual_discussion(phase="poll_belief")
        handler.process_response("Roughly even odds.", _entity(), disc)
        assert disc.method_state["poll_beliefs"] == []

    def test_validate_output_delegates(self):
        handler = PollBeliefHandler()
        disc = _factual_discussion(phase="poll_belief")
        assert handler.validate_output(
            {"belief": 0.5, "reasoning": "r"}, _entity(), disc) == ""
        assert handler.validate_output(
            {"belief": 2}, _entity(), disc) != ""


class TestPollBeliefAdvancement:
    def test_waits_for_stragglers_when_roster_known(self):
        handler = PollBeliefHandler()
        disc = _factual_discussion(phase="poll_belief", phase_round=1)
        disc.turn_order = [1, 2]
        handler.process_structured_response(
            {"belief": 0.7, "reasoning": "r"}, _entity(1, "Alice"), disc)
        assert handler.should_advance(disc) is False

    def test_advances_once_all_polled(self):
        handler = PollBeliefHandler()
        disc = _factual_discussion(phase="poll_belief", phase_round=1)
        disc.turn_order = [1, 2]
        for eid, name in ((1, "Alice"), (2, "Bob")):
            handler.process_structured_response(
                {"belief": 0.5, "reasoning": "r"}, _entity(eid, name), disc)
        assert handler.should_advance(disc) is True

    def test_gives_up_after_cap(self):
        handler = PollBeliefHandler()
        disc = _factual_discussion(phase="poll_belief",
                                   phase_round=MAX_POLL_ROUNDS + 1)
        disc.turn_order = [1, 2]
        assert handler.should_advance(disc) is True

    def test_next_phase_folds_beliefs_and_continues(self):
        from consensus.methods.base import LINEAR_NEXT
        handler = PollBeliefHandler()
        disc = _factual_discussion(phase="poll_belief")
        handler.process_structured_response(
            {"belief": 0.8, "reasoning": "r"}, _entity(1, "Alice"), disc)
        handler.process_structured_response(
            {"belief": 0.2, "reasoning": "r"}, _entity(2, "Bob"), disc)
        assert handler.next_phase(disc) == LINEAR_NEXT
        assert disc.method_state["shared_crux"]["initial_beliefs"] == {
            "Alice": 0.8, "Bob": 0.2}

    def test_init_state_seeds_poll_beliefs(self):
        assert PollBeliefHandler().init_state(_factual_discussion()) == {
            "poll_beliefs": []}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_phases_double_crux.py -k "Poll" -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'consensus.methods.phases.poll_belief'`.

- [ ] **Step 3: Create the handler**

Create `consensus/methods/phases/poll_belief.py`:

```python
"""Belief-poll phase handler for Double Crux (pre-belief poll, 2026-07-17).

Runs on the factual path only, immediately after the moderator
identifies the shared crux and before crux testing.  Each disagreeing
party states their current probability that the *moderator's synthesized
shared claim* is true, via the forced ``submit_crux_belief`` output tool
(issue #23 pattern); free-text JSON-block parsing remains the
human/fallback path.  These polls become the authoritative
``initial_beliefs`` — the "before" end of the belief-shift metric —
fixing the coverage gap (all parties are polled, not just crux authors)
and the proposition mismatch (initial and final are both measured on the
moderator's claim).  When the phase completes, the polls are folded into
``shared_crux['initial_beliefs']`` deterministically.

Spec: docs/superpowers/specs/2026-07-17-double-crux-pre-belief-poll-design.md
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import LINEAR_NEXT, OutputToolSpec, Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._crux_helpers import (
    MAX_POLL_ROUNDS,
    POLL_BELIEF_TOOL_PARAMETERS,
    apply_poll_beliefs,
    entities_with_poll,
    extract_poll_belief,
    format_shared_crux,
    record_poll_belief,
    validate_poll_belief_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class PollBeliefHandler(PhaseHandler):
    """Phase 3.5: Each party polls their belief on the shared crux."""

    phase = Phase(
        name="poll_belief",
        display_name="Belief Poll",
        description=(
            "Each participant records their current probability that the "
            "shared crux claim is true, before evidence is presented — "
            "the baseline for measuring belief change."
        ),
        rounds=1,
    )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        return {"poll_beliefs": []}

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        return (
            f"You are {entity.name}, participating in a Double Crux "
            "session.\n"
            f"Topic: {discussion.topic}\n\n"
            "BELIEF POLL PHASE\n\n"
            "The disagreement has been reduced to this crux:\n"
            f"{format_shared_crux(state)}\n\n"
            "Before any evidence is presented, state your current "
            "probability (0-1) that THIS exact claim is true, with a "
            "brief reason.  Answer for the claim as worded above — not a "
            "reframed version — because this number is the baseline "
            "against which any belief change from crux testing is "
            "measured.  Submit by calling the submit_crux_belief tool."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  State your current "
            "probability (0-1) that the shared crux claim is true by "
            "calling the submit_crux_belief tool."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has stated their current belief on the crux.  "
            "Note it neutrally — do not argue the claim yet.  Next: "
            f"{next_speaker_name}."
        )

    # ------------------------------------------------------------------
    # Response processing (free-text / human fallback path)
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        payload = extract_poll_belief(content)
        error = ("no belief found" if payload is None else
                 validate_poll_belief_payload(payload))
        if payload is not None and not error:
            record_poll_belief(state, entity, payload)
        else:
            logger.warning(
                "Could not extract a belief poll from %s's response (%s)",
                entity.name, error)
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec:
        return OutputToolSpec(
            name="submit_crux_belief",
            description=("Submit your current probability (0-1) that the "
                         "shared crux claim is true, plus your reasoning."),
            parameters=POLL_BELIEF_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        return validate_poll_belief_payload(payload)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        record_poll_belief(state, entity, payload)
        reasoning = str(payload.get("reasoning") or "").strip()
        display = f"{reasoning}\n\nBelief on the crux: {payload['belief']}"
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Phase advancement
    # ------------------------------------------------------------------

    def should_advance(self, discussion: Discussion) -> bool:
        """Advance when every party has polled, or the cap is reached.

        Both ends of the belief-shift metric need every participant, so
        stragglers whose polls could not be parsed get further rounds
        (up to ``MAX_POLL_ROUNDS``).  When the roster is unknown (empty
        ``turn_order``) fall back to advancing once any poll has been
        recorded and a full round has run — mirrors ResolveCruxHandler.
        """
        state = discussion.method_state
        participant_ids = set(discussion.turn_order)
        if participant_ids and participant_ids.issubset(
                entities_with_poll(state)):
            return True
        phase_round = state.get("phase_round", 1)
        if phase_round > MAX_POLL_ROUNDS:
            logger.warning(
                "Belief poll reached round %d; advancing with %d "
                "belief(s) recorded.",
                phase_round, len(state.get("poll_beliefs", [])),
            )
            return True
        if participant_ids:
            return False  # roster known: keep waiting for stragglers
        return bool(state.get("poll_beliefs")) and phase_round > 1

    def next_phase(self, discussion: Discussion) -> str | None:
        """Fold the polls into initial_beliefs, then continue to testing.

        Done once, deterministically, so build_crux_map and the
        conclusion read poll-sourced initial beliefs (mirrors how
        ResolveCruxHandler builds the crux_map).
        """
        apply_poll_beliefs(discussion.method_state)
        return LINEAR_NEXT

    # ------------------------------------------------------------------
    # Transition message (when transitioning TO this phase)
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "A shared factual crux has been identified.  Before testing "
            "it, each participant records their current probability that "
            "the crux claim is true — the baseline for measuring belief "
            "change."
        )
```

- [ ] **Step 4: Run the handler tests to verify they pass**

Run: `uv run pytest tests/test_phases_double_crux.py -k "Poll" -q`
Expected: PASS.

- [ ] **Step 5: Write the structured-output tests**

In `tests/test_double_crux_structured.py`, add to the imports from `_crux_helpers` (`POLL_BELIEF_TOOL_PARAMETERS`) and import the handler:

```python
from consensus.methods.phases.poll_belief import PollBeliefHandler
```

Add to `TestStructuredFlags`:

```python
    def test_poll_requires_structured(self):
        assert PollBeliefHandler().requires_structured_output is True
```

Add to `TestOutputToolSpecs`:

```python
    def test_poll_spec(self):
        spec = PollBeliefHandler().get_output_tool(
            _entity(), _discussion("poll_belief",
                                   shared_crux={"claim": CLAIM,
                                                "initial_beliefs": {}}))
        assert spec.name == "submit_crux_belief"
        assert spec.parameters is POLL_BELIEF_TOOL_PARAMETERS
```

Add to `TestPromptsNameTheTool`:

```python
    def test_poll_prompts(self):
        handler = PollBeliefHandler()
        disc = _discussion("poll_belief",
                           shared_crux={"claim": CLAIM, "initial_beliefs": {}})
        assert "submit_crux_belief" in handler.get_system_prompt(
            _entity(), disc)
        assert "submit_crux_belief" in handler.get_turn_prompt(
            _entity(), disc)
```

Add to `TestStructuredMatchesFreeTextPaths`:

```python
    def test_poll_structured_and_free_text_produce_same_state(self):
        handler = PollBeliefHandler()
        disc_a = _discussion("poll_belief",
                             shared_crux={"claim": CLAIM,
                                          "initial_beliefs": {}})
        handler.process_structured_response(
            {"belief": 0.4, "reasoning": "sceptical"}, _entity(), disc_a)

        disc_b = _discussion("poll_belief",
                             shared_crux={"claim": CLAIM,
                                          "initial_beliefs": {}})
        content = '```json\n{"belief": 0.4, "reasoning": "sceptical"}\n```'
        handler.process_response(content, _entity(), disc_b)

        assert (disc_a.method_state["poll_beliefs"]
                == disc_b.method_state["poll_beliefs"])
```

- [ ] **Step 6: Run the structured tests to verify they pass**

Run: `uv run pytest tests/test_double_crux_structured.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add consensus/methods/phases/poll_belief.py tests/test_phases_double_crux.py tests/test_double_crux_structured.py
git commit -m "$(cat <<'EOF'
feat(double-crux): add PollBeliefHandler phase (not yet wired)

New composable phase: each party polls their probability on the
moderator's shared crux via submit_crux_belief (structured) or a JSON
block (free-text). Straggler-completion should_advance mirrors resolve;
next_phase folds the polls into shared_crux.initial_beliefs. Tested
directly; not yet inserted into the DoubleCrux phase sequence.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Wire the poll into DoubleCrux; reword identify prompt; update flow tests

Insert the handler into the method and update every test that asserts the phase sequence or the end-to-end flow. `record_crux_selection` still snapshots here — `apply_poll_beliefs` overwrites it, so the suite stays green — the snapshot is removed in Task 4.

**Files:**
- Modify: `consensus/methods/double_crux.py`, `consensus/methods/phases/identify_crux.py`
- Test: `tests/test_phases_double_crux.py`, `tests/test_method_flow_e2e.py`

**Interfaces:**
- Consumes: `PollBeliefHandler` (Task 2).
- Produces: `DoubleCrux.default_phases` now `positions, hunt_cruxes, identify_crux, poll_belief, test_crux, resolve`.

- [ ] **Step 1: Update the phase-list, init-state, worst-case, and prompt tests (they will fail)**

In `tests/test_phases_double_crux.py`:

`test_registered_with_expected_phases` — insert `poll_belief`:

```python
    def test_registered_with_expected_phases(self):
        method = self._method()
        assert method.name == "double_crux"
        assert [p.name for p in method.default_phases] == [
            "positions", "hunt_cruxes", "identify_crux", "poll_belief",
            "test_crux", "resolve"]
```

`test_init_state_has_all_keys` — add `poll_beliefs`:

```python
    def test_init_state_has_all_keys(self):
        state = self._discussion().method_state
        for key in ("positions", "cruxes", "crux_verdict", "shared_crux",
                    "identify_attempts", "crux_search_rounds",
                    "poll_beliefs", "resolutions", "crux_map"):
            assert key in state, key
```

`test_worst_case_looping_never_trips_loop_guard` — the factual tail is now `identify -> poll -> test -> resolve` (3 transitions):

```python
    def test_worst_case_looping_never_trips_loop_guard(self):
        from consensus.methods.base import MAX_PHASE_VISITS_PER_PHASE
        # positions→hunt→(identify→hunt)×(MAX-1)→identify→poll→test→resolve
        transitions = 2 + 2 * (MAX_CRUX_SEARCH_ROUNDS - 1) + 3
        method = self._method()
        cap = len(method.default_phases) * MAX_PHASE_VISITS_PER_PHASE
        assert transitions < cap
```

`test_prompts_show_cruxes_and_name_the_tool` — the belief-carry-over comment is outdated; update it (the `"polarity"` assertion is unchanged):

```python
        # The shared claim should keep the cited cruxes' polarity — each
        # party is re-polled on this exact claim, so the prompt must say so.
        assert "polarity" in system.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_phases_double_crux.py -k "expected_phases or all_keys or worst_case" -q`
Expected: FAIL — `poll_belief` not in `default_phases`; `poll_beliefs` not in init_state.

- [ ] **Step 3: Wire the handler into the method**

In `consensus/methods/double_crux.py`, add the import and insert the handler:

```python
from .phases.poll_belief import PollBeliefHandler
```

```python
    phase_handlers = (
        StatePositionsHandler(context_label="a Double Crux session"),
        HuntCruxesHandler(),
        IdentifyCruxHandler(),
        PollBeliefHandler(),
        TestCruxHandler(),
        ResolveCruxHandler(),
    )
```

- [ ] **Step 4: Reword the identify prompt**

In `consensus/methods/phases/identify_crux.py`, in `get_system_prompt`, replace the belief-carry-over sentence:

```python
            "it — disagreeing about a shared pivotal claim is exactly "
            "what a crux is).  State it as ONE neutral claim and cite "
            "the crux ids it comes from.  Keep the claim in the same "
            "polarity as the cited cruxes wherever possible — each "
            "participant will next be polled for their probability on "
            "this exact claim, so a reversed or reframed claim makes "
            "those numbers hard to read.\n"
```

- [ ] **Step 5: Run the phase tests to verify they pass**

Run: `uv run pytest tests/test_phases_double_crux.py -q`
Expected: PASS.

- [ ] **Step 6: Update the Double Crux E2E flow test (it will fail first)**

In `tests/test_method_flow_e2e.py`, update the `DC_HUNT_ROUND_2` comment and add poll content. Replace the `DC_HUNT_ROUND_2` docstring comment with:

```python
#: Round-2 cruxes share one pivotal claim.  Their per-crux beliefs
#: (0.75 / 0.25) are now provenance only — the poll_belief phase, not
#: this snapshot, sets initial_beliefs.  They are NOT word-overlap
#: similar to each author's own round-1 crux (near-duplicates are dropped).
```

Add poll content after `DC_IDENTIFY_FACTUAL` (deliberately distinct from the hunt beliefs, to prove the poll — not the old snapshot — is the source):

```python
#: Belief poll on the moderator's synthesized claim.  Numbers differ
#: from the round-2 crux beliefs (0.75 / 0.25) to prove initial_beliefs
#: is poll-sourced.
DC_POLL = {
    "P1": ('```json\n{"belief": 0.8, "reasoning": "Delivery metrics I '
           'have seen favour parity"}\n```'),
    "P2": ('```json\n{"belief": 0.3, "reasoning": "My experience says '
           'colocated teams still ship faster"}\n```'),
}
```

Add a `poll_belief` branch to `dc_content` (before the `test_crux` branch):

```python
    if phase == "poll_belief":
        return DC_POLL[speaker.name]
```

Update the expected trace in `test_full_run_with_loop_back` to include the poll:

```python
        assert [phase for phase, _ in trace] == (
            ["positions"] * 2
            + ["hunt_cruxes"] * 2 + ["identify_crux"]
            + ["hunt_cruxes"] * 2 + ["identify_crux"]
            + ["poll_belief"] * 2
            + ["test_crux"] * 4 + ["resolve"] * 2
        )
```

Update the `initial_beliefs` assertion (now poll-sourced and distinct):

```python
        assert state["shared_crux"]["initial_beliefs"] == {
            "P1": 0.8, "P2": 0.3}
```

Update the belief-shift assertions (initial from the poll; finals 0.7 / 0.6 from the resolutions):

```python
        crux_map = state["crux_map"]
        assert crux_map["verdict"] == "factual"
        assert crux_map["belief_shifts"]["P1"] == {
            "initial": 0.8, "final": 0.7, "shift": -0.1}
        assert crux_map["belief_shifts"]["P2"] == {
            "initial": 0.3, "final": 0.6, "shift": 0.3}
        assert crux_map["caveats"] == []
```

Update the conclusion-prompt assertion (P2's initial is now 0.3):

```python
        assert "0.3 → 0.6" in prompt
```

- [ ] **Step 7: Run the E2E test to verify it passes**

Run: `uv run pytest tests/test_method_flow_e2e.py::TestDoubleCruxFlow -q`
Expected: PASS. (If the shift shows a floating-point tail like `-0.1` rendering as `-0.09999…`, note `build_crux_map` rounds to `BELIEF_PRECISION=2`, so `-0.1` and `0.3` are exact — no change needed.)

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (all tests). `record_crux_selection` still snapshots, but `apply_poll_beliefs` overwrites it, so `test_crux_helpers` / `test_crux_artifact` are untouched and green.

- [ ] **Step 9: Commit**

```bash
git add consensus/methods/double_crux.py consensus/methods/phases/identify_crux.py tests/test_phases_double_crux.py tests/test_method_flow_e2e.py
git commit -m "$(cat <<'EOF'
feat(double-crux): insert poll_belief phase into the method flow

Wire PollBeliefHandler between identify_crux and test_crux (factual path
only). Reword the identify prompt: each party is re-polled on the shared
claim, so initial_beliefs no longer depends on carry-over. Update the
phase-list, init-state, worst-case, and E2E flow tests — the E2E run now
passes through poll_belief and asserts poll-sourced initial_beliefs
distinct from the hunt snapshot.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Remove the redundant hunt-snapshot from `record_crux_selection`

The poll is now the sole source of `initial_beliefs`. Remove the dead snapshot code and update the two direct unit tests plus the artifact/formatter fixtures that relied on it.

**Files:**
- Modify: `consensus/methods/phases/_crux_helpers.py`
- Test: `tests/test_crux_helpers.py`, `tests/test_crux_artifact.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `record_crux_selection` factual branch sets `initial_beliefs: {}` (poll fills it later).

- [ ] **Step 1: Update the record_crux_selection unit tests (they will fail)**

In `tests/test_crux_helpers.py`, replace `test_factual_snapshots_initial_beliefs` and delete `test_factual_skips_none_beliefs` (the None-skip logic is gone). The replacement asserts the factual branch preserves the claim/ids but leaves beliefs empty:

```python
    def test_factual_sets_claim_and_empty_initial_beliefs(self):
        # initial_beliefs is now owned by the poll_belief phase; the
        # selection records the shared claim/ids but no beliefs.
        state = self._hunted_state()
        record_crux_selection(state, {
            "verdict": "factual", "crux_ids": [1, 3], "claim": CLAIM_A,
            "reasoning": "Both named it."})
        assert state["crux_verdict"] == VERDICT_FACTUAL
        shared = state["shared_crux"]
        assert shared["claim"] == CLAIM_A
        assert shared["source_crux_ids"] == [1, 3]
        assert shared["initial_beliefs"] == {}
```

Also update the module docstring: change the line
`free-text extraction fallbacks.  The crux_map artifact ...` region that
mentions `crux-selection recording (verdict + initial-belief snapshot)`
to `crux-selection recording (verdict + shared-claim capture)`.

- [ ] **Step 2: Run the unit tests to verify the intent flips**

Run: `uv run pytest tests/test_crux_helpers.py -k "RecordCruxSelection" -q`
Expected: FAIL — `record_crux_selection` still snapshots `{"Alice": 0.9, "Bob": 0.2}`, so `initial_beliefs == {}` fails.

- [ ] **Step 3: Remove the snapshot from `record_crux_selection`**

In `consensus/methods/phases/_crux_helpers.py`, change the `VERDICT_FACTUAL` branch of `record_crux_selection` to stop building beliefs. Replace:

```python
    if verdict == VERDICT_FACTUAL:
        crux_ids = [int(cid) for cid in payload.get("crux_ids", [])]
        by_id = {c["id"]: c for c in state.get("cruxes", [])}
        initial_beliefs: dict[str, float] = {}
        for cid in crux_ids:
            crux = by_id.get(cid)
            if crux is not None and crux["belief"] is not None:
                initial_beliefs[crux["entity_name"]] = crux["belief"]
        state["shared_crux"] = {
            "claim": str(payload.get("claim") or "").strip().rstrip('.'),
            "description": "",
            "source_crux_ids": crux_ids,
            "initial_beliefs": initial_beliefs,
        }
```

with:

```python
    if verdict == VERDICT_FACTUAL:
        crux_ids = [int(cid) for cid in payload.get("crux_ids", [])]
        # initial_beliefs is owned by the poll_belief phase (design
        # 2026-07-17): it is polled on this shared claim for every party,
        # not snapshotted from the (differently-phrased) hunt cruxes.
        state["shared_crux"] = {
            "claim": str(payload.get("claim") or "").strip().rstrip('.'),
            "description": "",
            "source_crux_ids": crux_ids,
            "initial_beliefs": {},
        }
```

Also update `record_crux_selection`'s docstring: replace the sentence
"For a factual verdict, snapshots each referenced participant's stated
belief ... into ``initial_beliefs`` (the "before" end of the
belief-shift metric); ``None`` beliefs are skipped." with "For a factual
verdict, records the shared claim and source crux ids; ``initial_beliefs``
is left empty for the poll_belief phase to fill."

- [ ] **Step 4: Update the artifact/formatter fixtures**

In `tests/test_crux_artifact.py`, the `_full_state` and `_state` fixtures relied on `record_crux_selection` populating `initial_beliefs`; seed it directly now (simulating the poll).

In `TestBuildCruxMap._full_state`, after the `record_crux_selection(...)` call and before the resolutions, add:

```python
        state["shared_crux"]["initial_beliefs"] = {"Alice": 0.9, "Bob": 0.2}
```

In `TestFormatters._state`, after the `record_crux_selection(...)` call, add:

```python
        state["shared_crux"]["initial_beliefs"] = {"Alice": 0.9}
```

- [ ] **Step 5: Run the affected test files to verify they pass**

Run: `uv run pytest tests/test_crux_helpers.py tests/test_crux_artifact.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (all tests). The E2E run still asserts poll-sourced
`initial_beliefs == {"P1": 0.8, "P2": 0.3}`; with the snapshot gone the
poll is the only writer, producing the same result.

- [ ] **Step 7: Commit**

```bash
git add consensus/methods/phases/_crux_helpers.py tests/test_crux_helpers.py tests/test_crux_artifact.py
git commit -m "$(cat <<'EOF'
refactor(double-crux): make the belief poll the sole source of initial_beliefs

Remove the hunt-phase snapshot from record_crux_selection — the
poll_belief phase now polls every party on the shared claim, so the
factual branch just records the claim/ids and leaves initial_beliefs
empty. Update the direct record_crux_selection tests and the
artifact/formatter fixtures that seeded beliefs via the snapshot.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- New `PollBeliefHandler` (spec §1) → Task 2.
- `_crux_helpers.py` additions (spec §2) → Task 1.
- `record_crux_selection` change (spec §3) → Task 4.
- `DoubleCrux.phase_handlers` insertion (spec §4) → Task 3 (Step 3).
- Identify wording fix (spec §5) → Task 3 (Step 4).
- Testing plan (spec §Testing): unit → Task 1; structured → Task 2; phase-behavior → Task 2; E2E → Task 3; regression sweep (snapshot tests, phase-list/count) → Tasks 3 & 4.
- Data flow / error handling / non-goals → satisfied by the handler + helpers (always-on, factual-only, honest `?` on total failure, no new network calls, no config flag/UI).

**2. Placeholder scan** — no TBD/TODO/"handle edge cases"; every code step shows complete code and every run step gives an exact command + expected result.

**3. Type/name consistency** — state key `poll_beliefs` (list) and phase name `poll_belief` are used consistently; tool `submit_crux_belief`; helpers `validate_poll_belief_payload` / `record_poll_belief` / `entities_with_poll` / `extract_poll_belief` / `apply_poll_beliefs` match between the handler (Task 2), the helper definitions (Task 1), and the tests. `apply_poll_beliefs` replaces (not merges) `initial_beliefs`, which is why Task 3 stays green while the snapshot still runs and Task 4 removes it without changing E2E results.
