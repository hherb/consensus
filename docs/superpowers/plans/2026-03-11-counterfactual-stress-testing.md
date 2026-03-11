# Counterfactual Stress Testing Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Counterfactual Stress Testing discussion method — systematically invert key claims and score their structural importance to a conclusion.

**Architecture:** Four composable PhaseHandler instances (deliberate, extract, stress_test, synthesize) assembled into a `CounterfactualStressTest` method class. Follows identical patterns to BeliefDiffusion: handler composition, condition-based advancement, method-level `on_round_complete` override, and `get_conclusion_prompt` override.

**Tech Stack:** Python 3.11+, pytest, consensus discussion methods framework (`consensus/methods/`)

**Spec:** `docs/superpowers/specs/2026-03-11-counterfactual-stress-testing-design.md`

---

## Chunk 1: Helpers Module

### Task 1: Helper functions — `_counterfactual_helpers.py`

**Files:**
- Create: `consensus/methods/phases/_counterfactual_helpers.py`
- Test: `tests/test_phases_counterfactual.py`

- [ ] **Step 1: Write failing tests for `extract_impact_score`**

```python
"""Tests for Counterfactual Stress Testing phase handlers and helpers."""

import pytest

from consensus.methods.phases._counterfactual_helpers import (
    extract_impact_score,
    classify_claim,
    format_results_table,
)


class TestExtractImpactScore:
    def test_standard_tag(self):
        content = "The conclusion falls apart entirely. [IMPACT: 5]"
        assert extract_impact_score(content) == 5

    def test_low_impact(self):
        content = "Not much changes. [IMPACT: 1]"
        assert extract_impact_score(content) == 1

    def test_mid_impact(self):
        content = "Some elements weaken. [IMPACT: 3]"
        assert extract_impact_score(content) == 3

    def test_no_tag(self):
        content = "I think the impact is moderate."
        assert extract_impact_score(content) is None

    def test_tag_in_middle(self):
        content = "Analysis here. [IMPACT: 4] More text after."
        assert extract_impact_score(content) == 4

    def test_out_of_range_high(self):
        content = "[IMPACT: 7]"
        assert extract_impact_score(content) is None

    def test_out_of_range_zero(self):
        content = "[IMPACT: 0]"
        assert extract_impact_score(content) is None

    def test_whitespace_variations(self):
        content = "[IMPACT:  3 ]"
        assert extract_impact_score(content) == 3

    def test_lowercase_ignored(self):
        content = "[impact: 3]"
        assert extract_impact_score(content) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_phases_counterfactual.py::TestExtractImpactScore -v`
Expected: FAIL — `ImportError: cannot import name 'extract_impact_score'`

- [ ] **Step 3: Write failing tests for `classify_claim`**

Append to the test file:

```python
class TestClassifyClaim:
    def test_load_bearing(self):
        assert classify_claim(4.5) == "LOAD-BEARING"

    def test_load_bearing_threshold(self):
        assert classify_claim(4.0) == "LOAD-BEARING"

    def test_supportive(self):
        assert classify_claim(3.0) == "SUPPORTIVE"

    def test_supportive_threshold(self):
        assert classify_claim(2.0) == "SUPPORTIVE"

    def test_decorative(self):
        assert classify_claim(1.5) == "DECORATIVE"

    def test_decorative_low(self):
        assert classify_claim(1.0) == "DECORATIVE"
```

- [ ] **Step 4: Write failing tests for `format_results_table`**

Append to the test file:

```python
class TestFormatResultsTable:
    def test_basic_table(self):
        results = [
            {
                "claim_id": 1,
                "claim_text": "Claim one text",
                "scores": {"Alice": 5, "Bob": 4},
                "avg_score": 4.5,
                "classification": "LOAD-BEARING",
            },
            {
                "claim_id": 2,
                "claim_text": "Claim two text",
                "scores": {"Alice": 1, "Bob": 2},
                "avg_score": 1.5,
                "classification": "DECORATIVE",
            },
        ]
        table = format_results_table(results)
        assert "Claim one text" in table
        assert "4.5" in table
        assert "LOAD-BEARING" in table
        assert "DECORATIVE" in table

    def test_empty_results(self):
        table = format_results_table([])
        assert "No claims" in table or table == ""

    def test_none_scores(self):
        results = [
            {
                "claim_id": 1,
                "claim_text": "Untested claim",
                "scores": {},
                "avg_score": None,
                "classification": None,
            },
        ]
        table = format_results_table(results)
        assert "Untested claim" in table
```

- [ ] **Step 5: Implement `_counterfactual_helpers.py`**

Create `consensus/methods/phases/_counterfactual_helpers.py`:

```python
"""Shared helpers for Counterfactual Stress Testing phase handlers.

Contains impact score extraction, claim classification, and results
table formatting — used by the stress test and synthesis phases.
"""

from __future__ import annotations

import re


def extract_impact_score(content: str) -> int | None:
    """Extract [IMPACT: N] tag from response content.

    Returns an integer 1-5, or None if no valid tag found.
    """
    match = re.search(r'\[IMPACT:\s*(\d)\s*\]', content)
    if not match:
        return None
    score = int(match.group(1))
    if score < 1 or score > 5:
        return None
    return score


def classify_claim(avg_score: float) -> str:
    """Classify a claim based on its average impact score.

    Returns LOAD-BEARING (>=4.0), SUPPORTIVE (>=2.0), or DECORATIVE (<2.0).
    """
    if avg_score >= 4.0:
        return "LOAD-BEARING"
    if avg_score >= 2.0:
        return "SUPPORTIVE"
    return "DECORATIVE"


def format_results_table(claim_results: list[dict]) -> str:
    """Format claim results as a markdown table.

    Each entry should have: claim_id, claim_text, avg_score, classification.
    """
    if not claim_results:
        return "No claims were tested."

    lines = [
        "| # | Claim | Avg Impact | Classification |",
        "|---|-------|-----------|----------------|",
    ]
    for r in claim_results:
        cid = r.get("claim_id", "?")
        text = r.get("claim_text", "")
        avg = r.get("avg_score")
        avg_str = f"{avg:.1f}" if avg is not None else "N/A"
        cls = r.get("classification") or "N/A"
        lines.append(f"| {cid} | {text} | {avg_str} | {cls} |")
    return "\n".join(lines)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_phases_counterfactual.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add consensus/methods/phases/_counterfactual_helpers.py tests/test_phases_counterfactual.py
git commit -m "feat(methods): add counterfactual stress testing helpers with tests"
```

---

## Chunk 2: CounterfactualDeliberateHandler

### Task 2: Deliberate phase handler

**Files:**
- Create: `consensus/methods/phases/counterfactual_deliberate.py`
- Test: `tests/test_phases_counterfactual.py` (append)

- [ ] **Step 1: Write failing tests for `CounterfactualDeliberateHandler`**

Add fixtures and tests to the existing test file:

```python
from consensus.methods.base import Phase, ProcessedResponse
from consensus.methods.phases.counterfactual_deliberate import CounterfactualDeliberateHandler
from consensus.models import Discussion, Entity, EntityType


# -- Fixtures --

def _make_discussion(n_participants=3):
    """Create a counterfactual discussion with participants."""
    entities = []
    mod = Entity(name="Moderator", entity_type=EntityType.AI, id=100)
    entities.append(mod)
    for i in range(n_participants):
        e = Entity(name=f"Analyst_{i+1}", entity_type=EntityType.AI, id=i + 1)
        entities.append(e)

    disc = Discussion(
        id=1,
        topic="Should cities ban personal car ownership?",
        entities=entities,
        moderator_id=100,
        turn_order=[e.id for e in entities if e.id != 100],
        discussion_method="counterfactual",
    )
    return disc, mod


@pytest.fixture
def deliberate_handler():
    return CounterfactualDeliberateHandler()


@pytest.fixture
def cf_discussion():
    disc, _ = _make_discussion()
    # Manually set up base state for handler testing
    disc.method_state = {
        "current_phase": "cf_deliberate",
        "phase_round": 1,
        "preliminary_conclusion": None,
        "prior_conclusion": None,
        "claims": [],
        "claim_results": [],
        "current_claim_index": 0,
        "extraction_failed": False,
        "extraction_attempts": 0,
    }
    return disc


@pytest.fixture
def entity():
    return Entity(name="Analyst_1", entity_type=EntityType.AI, id=1)


class TestCounterfactualDeliberateHandler:
    def test_phase_metadata(self, deliberate_handler):
        assert deliberate_handler.phase.name == "cf_deliberate"
        assert deliberate_handler.phase.rounds == 2
        assert deliberate_handler.phase.allow_tools is True

    def test_init_state(self, deliberate_handler, cf_discussion):
        state = deliberate_handler.init_state(cf_discussion)
        assert state["preliminary_conclusion"] is None
        assert state["prior_conclusion"] is None

    def test_system_prompt_includes_topic(self, deliberate_handler, entity, cf_discussion):
        prompt = deliberate_handler.get_system_prompt(entity, cf_discussion)
        assert entity.name in prompt
        assert cf_discussion.topic in prompt
        assert "preliminary conclusion" in prompt.lower()

    def test_turn_prompt(self, deliberate_handler, entity, cf_discussion):
        prompt = deliberate_handler.get_turn_prompt(entity, cf_discussion)
        assert entity.name in prompt

    def test_should_advance_default(self, deliberate_handler, cf_discussion):
        cf_discussion.method_state["phase_round"] = 1
        assert deliberate_handler.should_advance(cf_discussion) is False
        cf_discussion.method_state["phase_round"] = 3
        assert deliberate_handler.should_advance(cf_discussion) is True

    def test_transition_message(self, deliberate_handler, cf_discussion):
        msg = deliberate_handler.get_transition_message(cf_discussion)
        assert "Deliberation" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_phases_counterfactual.py::TestCounterfactualDeliberateHandler -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement `counterfactual_deliberate.py`**

Create `consensus/methods/phases/counterfactual_deliberate.py`:

```python
"""Counterfactual Deliberate phase handler.

Open discussion to establish a preliminary conclusion before
stress testing. Skipped if a prior_conclusion is provided
(handled in CounterfactualStressTest.init_state).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class CounterfactualDeliberateHandler(PhaseHandler):
    """Phase 1: Open deliberation to establish a preliminary conclusion."""

    phase = Phase(
        name="cf_deliberate",
        display_name="Deliberation",
        description=(
            "Open discussion to establish a preliminary position on the "
            "topic before stress testing begins."
        ),
        rounds=2,
        allow_tools=True,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "preliminary_conclusion": None,
            "prior_conclusion": None,
        }

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a preliminary "
            f"discussion to establish a position.\n"
            f"Topic: {discussion.topic}\n\n"
            "Discuss openly and work toward a preliminary conclusion. "
            "Share your perspective, engage with others' arguments, and "
            "try to identify the strongest position supported by the "
            "available reasoning and evidence."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}. Share your perspective "
            "on this topic. Build on others' contributions where possible."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has shared their perspective. "
            "Briefly summarize their key points and note areas of "
            f"agreement or disagreement. Next: {next_speaker_name}."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_phases_counterfactual.py::TestCounterfactualDeliberateHandler -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/counterfactual_deliberate.py tests/test_phases_counterfactual.py
git commit -m "feat(methods): add CounterfactualDeliberateHandler"
```

---

## Chunk 3: ExtractClaimsHandler

### Task 3: Extract Claims phase handler

**Files:**
- Create: `consensus/methods/phases/counterfactual_extract.py`
- Test: `tests/test_phases_counterfactual.py` (append)

- [ ] **Step 1: Write failing tests for `ExtractClaimsHandler`**

Append to test file:

```python
from consensus.methods.phases.counterfactual_extract import ExtractClaimsHandler


class TestExtractClaimsHandler:
    @pytest.fixture
    def handler(self):
        return ExtractClaimsHandler()

    def test_phase_metadata(self, handler):
        assert handler.phase.name == "extract"
        assert handler.phase.rounds == 0
        assert handler.phase.allow_tools is False

    def test_init_state(self, handler, cf_discussion):
        state = handler.init_state(cf_discussion)
        assert state["claims"] == []
        assert state["claim_results"] == []
        assert state["current_claim_index"] == 0
        assert state["extraction_failed"] is False
        assert state["extraction_attempts"] == 0

    def test_turn_order_moderator_only(self, handler, cf_discussion):
        entity_ids = [1, 2, 3]
        result = handler.get_turn_order(entity_ids, cf_discussion)
        assert result == [cf_discussion.moderator_id]

    def test_turn_prompt_includes_conclusion(self, handler, entity, cf_discussion):
        cf_discussion.method_state["current_phase"] = "extract"
        cf_discussion.method_state["preliminary_conclusion"] = "Cars should be banned."
        prompt = handler.get_turn_prompt(entity, cf_discussion)
        assert "Cars should be banned" in prompt
        assert "3-7" in prompt
        assert "numbered" in prompt.lower()

    def test_turn_prompt_uses_prior_conclusion(self, handler, entity, cf_discussion):
        cf_discussion.method_state["current_phase"] = "extract"
        cf_discussion.method_state["prior_conclusion"] = "AI will surpass humans."
        prompt = handler.get_turn_prompt(entity, cf_discussion)
        assert "AI will surpass humans" in prompt

    def test_turn_prompt_retry(self, handler, entity, cf_discussion):
        cf_discussion.method_state["current_phase"] = "extract"
        cf_discussion.method_state["preliminary_conclusion"] = "Some conclusion."
        cf_discussion.method_state["extraction_failed"] = True
        cf_discussion.method_state["extraction_attempts"] = 1
        prompt = handler.get_turn_prompt(entity, cf_discussion)
        assert "failed" in prompt.lower() or "try again" in prompt.lower()
        assert "numbered" in prompt.lower()

    def test_process_response_extracts_claims(self, handler, entity, cf_discussion):
        cf_discussion.method_state["current_phase"] = "extract"
        content = (
            "Key claims:\n"
            "1. Personal cars contribute significantly to urban pollution\n"
            "2. Public transit can fully replace personal car usage\n"
            "3. Car bans would reduce traffic fatalities substantially\n"
        )
        result = handler.process_response(content, entity, cf_discussion)
        claims = cf_discussion.method_state["claims"]
        assert len(claims) == 3
        assert claims[0]["id"] == 1
        assert "urban pollution" in claims[0]["text"]
        assert len(cf_discussion.method_state["claim_results"]) == 3
        assert cf_discussion.method_state["extraction_failed"] is False

    def test_process_response_no_claims_sets_failed(self, handler, entity, cf_discussion):
        cf_discussion.method_state["current_phase"] = "extract"
        content = "I think there are many factors to consider."
        result = handler.process_response(content, entity, cf_discussion)
        assert cf_discussion.method_state["extraction_failed"] is True
        assert cf_discussion.method_state["extraction_attempts"] == 1
        assert cf_discussion.method_state["claims"] == []

    def test_should_advance_with_claims(self, handler, cf_discussion):
        cf_discussion.method_state["claims"] = [{"id": 1, "text": "A claim"}]
        assert handler.should_advance(cf_discussion) is True

    def test_should_advance_no_claims_no_advance(self, handler, cf_discussion):
        cf_discussion.method_state["claims"] = []
        cf_discussion.method_state["extraction_attempts"] = 1
        assert handler.should_advance(cf_discussion) is False

    def test_should_advance_gives_up_after_3(self, handler, cf_discussion):
        cf_discussion.method_state["claims"] = []
        cf_discussion.method_state["extraction_attempts"] = 3
        assert handler.should_advance(cf_discussion) is True

    def test_process_response_retry_then_success_clears_failed(self, handler, entity, cf_discussion):
        cf_discussion.method_state["current_phase"] = "extract"
        cf_discussion.method_state["extraction_failed"] = True
        cf_discussion.method_state["extraction_attempts"] = 1
        content = "1. Cars cause significant urban pollution\n2. Public transit is viable\n"
        handler.process_response(content, entity, cf_discussion)
        assert cf_discussion.method_state["extraction_failed"] is False
        assert len(cf_discussion.method_state["claims"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_phases_counterfactual.py::TestExtractClaimsHandler -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement `counterfactual_extract.py`**

Create `consensus/methods/phases/counterfactual_extract.py`:

```python
"""Extract Claims phase handler for Counterfactual Stress Testing.

Moderator extracts 3-7 key falsifiable claims from the deliberation
or prior conclusion. Includes retry logic for failed extractions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..parsing import parse_numbered_list
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)

MAX_EXTRACTION_ATTEMPTS = 3


class ExtractClaimsHandler(PhaseHandler):
    """Phase 2: Moderator extracts key falsifiable claims."""

    phase = Phase(
        name="extract",
        display_name="Claim Extraction",
        description=(
            "The moderator extracts 3-7 key falsifiable claims that "
            "the conclusion depends on."
        ),
        rounds=0,  # condition-based
        allow_tools=False,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "claims": [],
            "claim_results": [],
            "current_claim_index": 0,
            "extraction_failed": False,
            "extraction_attempts": 0,
        }

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        return [discussion.moderator_id]

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            "You are the moderator extracting testable claims from "
            "the discussion. Identify the specific, falsifiable assertions "
            "that the conclusion depends on."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        conclusion = (state.get("preliminary_conclusion")
                      or state.get("prior_conclusion")
                      or "(no conclusion available)")

        # Retry prompt if previous extraction failed
        if state.get("extraction_failed") and state.get("extraction_attempts", 0) > 0:
            return (
                "The previous extraction failed to produce a numbered list "
                "of claims. Please try again.\n\n"
                f"Conclusion to analyze:\n{conclusion}\n\n"
                "Extract 3-7 key claims as a NUMBERED LIST. Each claim "
                "must be a specific, falsifiable assertion — not a value "
                "judgment or vague statement. Use this format:\n"
                "1. <claim>\n"
                "2. <claim>\n"
                "..."
            )

        return (
            "Review the discussion above and the conclusion reached.\n\n"
            f"Conclusion:\n{conclusion}\n\n"
            "Extract 3-7 key claims that this conclusion depends on. "
            "Each claim should be a specific, falsifiable assertion — "
            "not a value judgment or vague statement. List them as a "
            "numbered list:\n"
            "1. <claim>\n"
            "2. <claim>\n"
            "..."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        parsed = parse_numbered_list(content)

        if not parsed:
            state["extraction_failed"] = True
            state["extraction_attempts"] = state.get("extraction_attempts", 0) + 1
            logger.warning(
                "Claim extraction attempt %d failed — no numbered items found",
                state["extraction_attempts"],
            )
            return ProcessedResponse(display_content=content)

        # Build claims list
        claims = [{"id": i + 1, "text": text} for i, text in enumerate(parsed)]
        state["claims"] = claims
        state["extraction_failed"] = False

        # Initialize claim_results
        state["claim_results"] = [
            {
                "claim_id": c["id"],
                "claim_text": c["text"],
                "scores": {},
                "avg_score": None,
                "classification": None,
            }
            for c in claims
        ]

        logger.info("Extracted %d claims for stress testing", len(claims))
        return ProcessedResponse(
            display_content=content,
            extracted_data={"claims": claims},
        )

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        if state.get("claims"):
            return True
        if state.get("extraction_attempts", 0) >= MAX_EXTRACTION_ATTEMPTS:
            logger.warning("Giving up on claim extraction after %d attempts",
                           MAX_EXTRACTION_ATTEMPTS)
            return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_phases_counterfactual.py::TestExtractClaimsHandler -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/counterfactual_extract.py tests/test_phases_counterfactual.py
git commit -m "feat(methods): add ExtractClaimsHandler with retry logic"
```

---

## Chunk 4: StressTestHandler

### Task 4: Stress Test phase handler

**Files:**
- Create: `consensus/methods/phases/counterfactual_stress.py`
- Test: `tests/test_phases_counterfactual.py` (append)

- [ ] **Step 1: Write failing tests for `StressTestHandler`**

Append to test file:

```python
from consensus.methods.phases.counterfactual_stress import StressTestHandler


class TestStressTestHandler:
    @pytest.fixture
    def handler(self):
        return StressTestHandler()

    @pytest.fixture
    def stress_discussion(self, cf_discussion):
        cf_discussion.method_state["current_phase"] = "stress_test"
        cf_discussion.method_state["claims"] = [
            {"id": 1, "text": "Cars cause significant urban pollution"},
            {"id": 2, "text": "Public transit can replace personal cars"},
            {"id": 3, "text": "Car bans reduce traffic fatalities"},
        ]
        cf_discussion.method_state["claim_results"] = [
            {"claim_id": 1, "claim_text": "Cars cause significant urban pollution",
             "scores": {}, "avg_score": None, "classification": None},
            {"claim_id": 2, "claim_text": "Public transit can replace personal cars",
             "scores": {}, "avg_score": None, "classification": None},
            {"claim_id": 3, "claim_text": "Car bans reduce traffic fatalities",
             "scores": {}, "avg_score": None, "classification": None},
        ]
        cf_discussion.method_state["current_claim_index"] = 0
        cf_discussion.method_state["preliminary_conclusion"] = "Cars should be banned."
        return cf_discussion

    def test_phase_metadata(self, handler):
        assert handler.phase.name == "stress_test"
        assert handler.phase.rounds == 0
        assert handler.phase.allow_tools is True

    def test_system_prompt_includes_claim(self, handler, entity, stress_discussion):
        prompt = handler.get_system_prompt(entity, stress_discussion)
        assert "Cars cause significant urban pollution" in prompt
        assert "FALSE" in prompt
        assert "MUST argue" in prompt or "must argue" in prompt.lower()

    def test_system_prompt_changes_with_index(self, handler, entity, stress_discussion):
        stress_discussion.method_state["current_claim_index"] = 1
        prompt = handler.get_system_prompt(entity, stress_discussion)
        assert "Public transit can replace personal cars" in prompt

    def test_turn_prompt_includes_claim_and_impact_tag(self, handler, entity, stress_discussion):
        prompt = handler.get_turn_prompt(entity, stress_discussion)
        assert "Cars cause significant urban pollution" in prompt
        assert "[IMPACT:" in prompt
        assert "1 of 3" in prompt

    def test_turn_prompt_second_claim(self, handler, entity, stress_discussion):
        stress_discussion.method_state["current_claim_index"] = 1
        prompt = handler.get_turn_prompt(entity, stress_discussion)
        assert "Public transit" in prompt
        assert "2 of 3" in prompt

    def test_process_response_extracts_score(self, handler, entity, stress_discussion):
        content = "If this claim is false, the conclusion weakens significantly. [IMPACT: 4]"
        result = handler.process_response(content, entity, stress_discussion)
        scores = stress_discussion.method_state["claim_results"][0]["scores"]
        assert scores["Analyst_1"] == 4

    def test_process_response_no_score(self, handler, entity, stress_discussion):
        content = "The conclusion still mostly holds without this."
        result = handler.process_response(content, entity, stress_discussion)
        scores = stress_discussion.method_state["claim_results"][0]["scores"]
        assert "Analyst_1" not in scores

    def test_process_response_skips_moderator(self, handler, stress_discussion):
        mod = Entity(name="Moderator", entity_type=EntityType.AI, id=100)
        content = "Summary of the discussion. [IMPACT: 3]"
        handler.process_response(content, mod, stress_discussion)
        scores = stress_discussion.method_state["claim_results"][0]["scores"]
        assert "Moderator" not in scores

    def test_should_advance_not_done(self, handler, stress_discussion):
        stress_discussion.method_state["current_claim_index"] = 1
        assert handler.should_advance(stress_discussion) is False

    def test_should_advance_all_done(self, handler, stress_discussion):
        stress_discussion.method_state["current_claim_index"] = 3
        assert handler.should_advance(stress_discussion) is True

    def test_transition_message(self, handler, stress_discussion):
        msg = handler.get_transition_message(stress_discussion)
        assert "Counterfactual" in msg
        assert "Cars cause significant urban pollution" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_phases_counterfactual.py::TestStressTestHandler -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement `counterfactual_stress.py`**

Create `consensus/methods/phases/counterfactual_stress.py`:

```python
"""Stress Test phase handler for Counterfactual Stress Testing.

For each claim, all participants argue from the premise that it is
false and score the impact on the overall conclusion.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._counterfactual_helpers import extract_impact_score

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class StressTestHandler(PhaseHandler):
    """Phase 3: Systematically invert each claim and assess impact."""

    phase = Phase(
        name="stress_test",
        display_name="Counterfactual Stress Test",
        description=(
            "Each key claim is systematically inverted. Participants "
            "must argue from the counterfactual premise and rate "
            "the impact on the conclusion."
        ),
        rounds=0,  # condition-based
        allow_tools=True,
    )

    def _current_claim(self, discussion: Discussion) -> dict | None:
        """Return the claim currently under test, or None."""
        state = discussion.method_state
        idx = state.get("current_claim_index", 0)
        claims = state.get("claims", [])
        if 0 <= idx < len(claims):
            return claims[idx]
        return None

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        claim = self._current_claim(discussion)
        claim_text = claim["text"] if claim else "(no claim)"
        conclusion = (discussion.method_state.get("preliminary_conclusion")
                      or "(no conclusion)")

        return (
            f"You are {entity.name}, participating in a counterfactual "
            f"stress test.\n"
            f"Topic: {discussion.topic}\n"
            f"Preliminary conclusion: {conclusion}\n\n"
            "COUNTERFACTUAL STRESS TEST\n\n"
            f"The claim under test is: \"{claim_text}\"\n\n"
            "You MUST argue from the premise that this claim is FALSE — "
            "even if you believe it is true. Your job is to honestly "
            "assess how much damage losing this claim does to the "
            "overall conclusion."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        claim = self._current_claim(discussion)
        claim_text = claim["text"] if claim else "(no claim)"
        idx = state.get("current_claim_index", 0)
        total = len(state.get("claims", []))

        return (
            f"--- Counterfactual Test #{idx + 1} of {total} ---\n"
            f"Assume the following claim is FALSE: \"{claim_text}\"\n\n"
            f"It is your turn, {entity.name}. Given this counterfactual, "
            "how does the overall conclusion change? What breaks? What "
            "still holds?\n\n"
            "Rate the impact on a scale of 1-5 at the end of your "
            "response using this exact format:\n"
            "[IMPACT: N]\n"
            "where 1 = conclusion completely unaffected, "
            "5 = conclusion collapses entirely."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        claim = self._current_claim(discussion)
        claim_text = claim["text"] if claim else "(no claim)"
        return (
            f"{speaker_name} has assessed the impact of losing the "
            f"claim \"{claim_text}\". Briefly note their damage "
            f"assessment. Next: {next_speaker_name}."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state

        # Skip moderator entities — only participants score
        if entity.id == discussion.moderator_id:
            return ProcessedResponse(display_content=content)

        score = extract_impact_score(content)
        idx = state.get("current_claim_index", 0)
        claim_results = state.get("claim_results", [])
        if score is not None and idx < len(claim_results):
            claim_results[idx]["scores"][entity.name] = score

        return ProcessedResponse(
            display_content=content,
            extracted_data={"impact_score": score},
        )

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        idx = state.get("current_claim_index", 0)
        total = len(state.get("claims", []))
        return idx >= total

    def get_transition_message(self, discussion: Discussion) -> str:
        claim = self._current_claim(discussion)
        claim_text = claim["text"] if claim else "(no claim)"
        total = len(discussion.method_state.get("claims", []))
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"We will now test {total} key claims by systematically "
            "assuming each one is false and assessing the damage to "
            "the conclusion.\n\n"
            f"**First claim under test:** \"{claim_text}\""
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_phases_counterfactual.py::TestStressTestHandler -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/counterfactual_stress.py tests/test_phases_counterfactual.py
git commit -m "feat(methods): add StressTestHandler for counterfactual testing"
```

---

## Chunk 5: SynthesizeHandler and Method Class

### Task 5: Synthesize phase handler

**Files:**
- Create: `consensus/methods/phases/counterfactual_synthesize.py`
- Test: `tests/test_phases_counterfactual.py` (append)

- [ ] **Step 1: Write failing tests for `SynthesizeHandler`**

Append to test file:

```python
from consensus.methods.phases.counterfactual_synthesize import SynthesizeHandler


class TestSynthesizeHandler:
    @pytest.fixture
    def handler(self):
        return SynthesizeHandler()

    def test_phase_metadata(self, handler):
        assert handler.phase.name == "synthesize"
        assert handler.phase.rounds == 1
        assert handler.phase.allow_tools is False

    def test_turn_order_moderator_only(self, handler, cf_discussion):
        entity_ids = [1, 2, 3]
        result = handler.get_turn_order(entity_ids, cf_discussion)
        assert result == [cf_discussion.moderator_id]

    def test_system_prompt_empty(self, handler, entity, cf_discussion):
        assert handler.get_system_prompt(entity, cf_discussion) == ""

    def test_turn_prompt_empty(self, handler, entity, cf_discussion):
        assert handler.get_turn_prompt(entity, cf_discussion) == ""

    def test_transition_message(self, handler, cf_discussion):
        msg = handler.get_transition_message(cf_discussion)
        assert "Synthesis" in msg
```

- [ ] **Step 2: Implement `counterfactual_synthesize.py`**

Create `consensus/methods/phases/counterfactual_synthesize.py`:

```python
"""Synthesize phase handler for Counterfactual Stress Testing.

Moderator-only phase that triggers the final conclusion prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class SynthesizeHandler(PhaseHandler):
    """Phase 4: Moderator synthesizes stress test results."""

    phase = Phase(
        name="synthesize",
        display_name="Synthesis",
        description=(
            "The moderator classifies each claim by structural importance "
            "and assesses the overall robustness of the conclusion."
        ),
        rounds=1,
        allow_tools=False,
    )

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        return [discussion.moderator_id]

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `python -m pytest tests/test_phases_counterfactual.py::TestSynthesizeHandler -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add consensus/methods/phases/counterfactual_synthesize.py tests/test_phases_counterfactual.py
git commit -m "feat(methods): add SynthesizeHandler for counterfactual testing"
```

### Task 6: Method class — `CounterfactualStressTest`

**Files:**
- Create: `consensus/methods/counterfactual.py`
- Modify: `consensus/methods/__init__.py`
- Test: `tests/test_phases_counterfactual.py` (append)

- [ ] **Step 1: Write failing tests for method class**

Append to test file:

```python
from consensus.methods.counterfactual import CounterfactualStressTest
from consensus.methods.phases._counterfactual_helpers import (
    classify_claim,
    format_results_table,
)


class TestCounterfactualStressTestIntegration:
    @pytest.fixture
    def method(self):
        return CounterfactualStressTest()

    @pytest.fixture
    def discussion(self, method):
        disc, _ = _make_discussion()
        disc.method_state = method.init_state(disc)
        return disc

    # -- Phase auto-derivation --

    def test_phases_auto_derived(self, method):
        assert len(method.default_phases) == 4
        assert method.default_phases[0].name == "cf_deliberate"
        assert method.default_phases[1].name == "extract"
        assert method.default_phases[2].name == "stress_test"
        assert method.default_phases[3].name == "synthesize"

    # -- init_state --

    def test_init_state_default(self, method, discussion):
        state = discussion.method_state
        assert state["current_phase"] == "cf_deliberate"
        assert state["phase_round"] == 1
        assert state["preliminary_conclusion"] is None
        assert state["prior_conclusion"] is None
        assert state["claims"] == []
        assert state["claim_results"] == []
        assert state["current_claim_index"] == 0
        assert state["extraction_failed"] is False
        assert state["extraction_attempts"] == 0

    def test_init_state_with_prior_conclusion(self, method):
        """Test that prior_conclusion skips deliberation and populates state."""
        disc, _ = _make_discussion()
        disc.method_state = {"prior_conclusion": "AI will dominate."}
        state = method.init_state(disc)
        assert state["current_phase"] == "extract"
        assert state["preliminary_conclusion"] == "AI will dominate."
        assert state["prior_conclusion"] == "AI will dominate."

    # -- on_round_complete --

    def test_on_round_complete_non_stress(self, method, discussion):
        discussion.method_state["phase_round"] = 1
        method.on_round_complete(discussion)
        assert discussion.method_state["phase_round"] == 2
        assert discussion.method_state["current_claim_index"] == 0

    def test_on_round_complete_stress_test(self, method, discussion):
        discussion.method_state["current_phase"] = "stress_test"
        discussion.method_state["phase_round"] = 1
        discussion.method_state["claims"] = [
            {"id": 1, "text": "Claim A"},
            {"id": 2, "text": "Claim B"},
        ]
        discussion.method_state["claim_results"] = [
            {"claim_id": 1, "claim_text": "Claim A",
             "scores": {"Analyst_1": 4, "Analyst_2": 5},
             "avg_score": None, "classification": None},
            {"claim_id": 2, "claim_text": "Claim B",
             "scores": {}, "avg_score": None, "classification": None},
        ]
        discussion.method_state["current_claim_index"] = 0

        method.on_round_complete(discussion)

        assert discussion.method_state["phase_round"] == 2
        assert discussion.method_state["current_claim_index"] == 1
        assert discussion.method_state["claim_results"][0]["avg_score"] == 4.5
        assert discussion.method_state["claim_results"][0]["classification"] == "LOAD-BEARING"

    def test_on_round_complete_stress_empty_scores(self, method, discussion):
        discussion.method_state["current_phase"] = "stress_test"
        discussion.method_state["claims"] = [{"id": 1, "text": "C"}]
        discussion.method_state["claim_results"] = [
            {"claim_id": 1, "claim_text": "C",
             "scores": {}, "avg_score": None, "classification": None},
        ]
        discussion.method_state["current_claim_index"] = 0

        method.on_round_complete(discussion)

        # Still increments index even with no scores
        assert discussion.method_state["current_claim_index"] == 1
        assert discussion.method_state["claim_results"][0]["avg_score"] is None

    # -- get_conclusion_prompt --

    def test_get_conclusion_prompt(self, method, discussion):
        discussion.method_state["preliminary_conclusion"] = "Cars should be banned."
        discussion.method_state["claim_results"] = [
            {"claim_id": 1, "claim_text": "Pollution claim",
             "scores": {"A": 5}, "avg_score": 5.0, "classification": "LOAD-BEARING"},
            {"claim_id": 2, "claim_text": "Transit claim",
             "scores": {"A": 1}, "avg_score": 1.0, "classification": "DECORATIVE"},
        ]
        prompt = method.get_conclusion_prompt(discussion)
        assert "Cars should be banned" in prompt
        assert "Pollution claim" in prompt
        assert "LOAD-BEARING" in prompt
        assert "DECORATIVE" in prompt
        assert "robustness" in prompt.lower() or "robust" in prompt.lower()

    def test_get_conclusion_prompt_no_claims(self, method, discussion):
        discussion.method_state["claims"] = []
        prompt = method.get_conclusion_prompt(discussion)
        assert "no claims" in prompt.lower() or "could not" in prompt.lower()

    # -- Method delegation --

    def test_system_prompt_deliberate(self, method, discussion):
        entity = Entity(name="Analyst_1", entity_type=EntityType.AI, id=1)
        prompt = method.get_system_prompt(entity, discussion)
        assert "preliminary" in prompt.lower()
        assert discussion.topic in prompt

    def test_system_prompt_stress(self, method, discussion):
        entity = Entity(name="Analyst_1", entity_type=EntityType.AI, id=1)
        discussion.method_state["current_phase"] = "stress_test"
        discussion.method_state["claims"] = [{"id": 1, "text": "Test claim"}]
        discussion.method_state["current_claim_index"] = 0
        prompt = method.get_system_prompt(entity, discussion)
        assert "Test claim" in prompt
        assert "FALSE" in prompt

    # -- Phase advancement --

    def test_advance_deliberate_to_extract(self, method, discussion):
        discussion.method_state["phase_round"] = 3
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "extract"

    def test_advance_extract_to_stress(self, method, discussion):
        discussion.method_state["current_phase"] = "extract"
        discussion.method_state["claims"] = [{"id": 1, "text": "C"}]
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "stress_test"

    def test_advance_stress_to_synthesize(self, method, discussion):
        discussion.method_state["current_phase"] = "stress_test"
        discussion.method_state["claims"] = [{"id": 1, "text": "C"}]
        discussion.method_state["current_claim_index"] = 1
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "synthesize"

    def test_advance_chain_with_no_claims(self, method, discussion):
        """3 failed extractions → stress_test immediately advances → synthesize."""
        discussion.method_state["current_phase"] = "extract"
        discussion.method_state["claims"] = []
        discussion.method_state["extraction_attempts"] = 3
        # Extract advances (gives up)
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "stress_test"
        # Stress test immediately advances (0 >= 0)
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "synthesize"

    def test_advance_synthesize_to_none(self, method, discussion):
        discussion.method_state["current_phase"] = "synthesize"
        discussion.method_state["phase_round"] = 2
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_phases_counterfactual.py::TestCounterfactualStressTestIntegration -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement `counterfactual.py`**

Create `consensus/methods/counterfactual.py`:

```python
"""Counterfactual Stress Testing — systematically test claim importance.

For each key claim in a developing consensus, invert it and check
if the conclusion survives. Produces a ranked classification of
claims as load-bearing, supportive, or decorative.

Phases:
  1. CF_DELIBERATE — Open discussion to establish preliminary conclusion
                     (skipped if prior_conclusion is provided)
  2. EXTRACT       — Moderator extracts 3-7 key falsifiable claims
  3. STRESS_TEST   — Invert each claim; participants assess impact
  4. SYNTHESIZE    — Moderator classifies claims and assesses robustness
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases.counterfactual_deliberate import CounterfactualDeliberateHandler
from .phases.counterfactual_extract import ExtractClaimsHandler
from .phases.counterfactual_stress import StressTestHandler
from .phases.counterfactual_synthesize import SynthesizeHandler
from .phases._counterfactual_helpers import classify_claim, format_results_table

if TYPE_CHECKING:
    from ..models import Discussion

logger = logging.getLogger(__name__)


class CounterfactualStressTest(DiscussionMethod):
    """Counterfactual Stress Testing — test which beliefs are load-bearing."""

    name = "counterfactual"
    display_name = "Counterfactual Stress Testing"
    description = (
        "Systematically tests which beliefs are load-bearing vs. decorative "
        "in a developing consensus. For each key claim, participants argue "
        "from the premise that it is false and score the impact. Produces "
        "a ranked classification of claims by structural importance."
    )
    phase_handlers = (
        CounterfactualDeliberateHandler(),
        ExtractClaimsHandler(),
        StressTestHandler(),
        SynthesizeHandler(),
    )

    # ------------------------------------------------------------------
    # State initialization
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        """Initialize state, skipping deliberation if prior_conclusion set."""
        # Read prior_conclusion from the discussion's existing method_state
        # BEFORE super().init_state() runs, because super() merges handler
        # init_state dicts which reset prior_conclusion to None.
        prior = (discussion.method_state or {}).get("prior_conclusion")

        state = super().init_state(discussion)

        if prior:
            state["current_phase"] = "extract"
            state["prior_conclusion"] = prior
            state["preliminary_conclusion"] = prior
            logger.info("Prior conclusion provided — skipping deliberation")

        return state

    # ------------------------------------------------------------------
    # Round lifecycle
    # ------------------------------------------------------------------

    def on_round_complete(self, discussion: Discussion) -> None:
        """Increment phase_round; finalize claim scores during stress_test."""
        super().on_round_complete(discussion)

        phase = self.current_phase(discussion)
        if phase and phase.name == "stress_test":
            state = discussion.method_state
            idx = state.get("current_claim_index", 0)
            claim_results = state.get("claim_results", [])

            if idx < len(claim_results):
                scores = claim_results[idx].get("scores", {})
                if scores:
                    avg = sum(scores.values()) / len(scores)
                    claim_results[idx]["avg_score"] = avg
                    claim_results[idx]["classification"] = classify_claim(avg)

            state["current_claim_index"] = idx + 1

    # ------------------------------------------------------------------
    # Conclusion
    # ------------------------------------------------------------------

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        """Build the final synthesis prompt with results table."""
        state = discussion.method_state
        claims = state.get("claims", [])
        claim_results = state.get("claim_results", [])
        conclusion = (state.get("preliminary_conclusion")
                      or state.get("prior_conclusion")
                      or "(no conclusion)")

        if not claims:
            return (
                "The counterfactual stress test could not extract any "
                "claims from the discussion. Please provide a qualitative "
                "summary of the discussion and note that claim extraction "
                "was unsuccessful."
            )

        table = format_results_table(claim_results)

        return (
            "The counterfactual stress test is complete.\n\n"
            f"**Preliminary conclusion:** {conclusion}\n\n"
            f"**Stress test results:**\n{table}\n\n"
            "Provide a comprehensive synthesis:\n"
            "1. **Claim ranking** — Rank claims from most to least "
            "structurally important based on their impact scores.\n"
            "2. **Load-bearing analysis** — For each LOAD-BEARING claim "
            "(avg >= 4.0), explain why the conclusion depends on it.\n"
            "3. **Decorative analysis** — For each DECORATIVE claim "
            "(avg < 2.0), explain why it is not structurally important.\n"
            "4. **Robustness assessment** — Overall, how robust is the "
            "conclusion? How many of its supporting claims are truly "
            "load-bearing vs. decorative?\n"
            "5. **Revised conclusion** — Given what the stress test "
            "revealed, restate the conclusion with appropriate "
            "confidence and caveats.\n\n"
            "Be specific and cite the impact scores."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_phases_counterfactual.py::TestCounterfactualStressTestIntegration -v`
Expected: All PASS

- [ ] **Step 5: Register the method in `__init__.py`**

Modify `consensus/methods/__init__.py`:

Add import after the `VotingMethod` import:
```python
from .counterfactual import CounterfactualStressTest
```

Add to `_METHODS` dict:
```python
"counterfactual": CounterfactualStressTest,
```

Add to `__all__`:
```python
"CounterfactualStressTest",
```

- [ ] **Step 6: Write test for method registration**

Append to test file:

```python
import consensus.methods as _methods_module
from consensus.methods import get_method, list_methods


class TestMethodRegistration:
    def setup_method(self):
        """Clear cached singletons to avoid stale test state."""
        _methods_module._METHODS_METADATA = None
        _methods_module._INSTANCES.pop("counterfactual", None)

    def test_get_method(self):
        method = get_method("counterfactual")
        assert isinstance(method, CounterfactualStressTest)
        assert method.name == "counterfactual"

    def test_list_methods_includes_counterfactual(self):
        methods = list_methods()
        names = [m["name"] for m in methods]
        assert "counterfactual" in names

    def test_method_to_dict(self):
        method = CounterfactualStressTest()
        d = method.to_dict()
        assert d["name"] == "counterfactual"
        assert len(d["phases"]) == 4
        assert d["phases"][0]["name"] == "cf_deliberate"
```

- [ ] **Step 7: Run full test suite**

Run: `python -m pytest tests/test_phases_counterfactual.py -v`
Expected: All PASS

- [ ] **Step 8: Run existing tests to verify no regressions**

Run: `python -m pytest tests/ -v --timeout=30`
Expected: All PASS (no regressions in other method tests)

- [ ] **Step 9: Commit**

```bash
git add consensus/methods/counterfactual.py consensus/methods/__init__.py tests/test_phases_counterfactual.py
git commit -m "feat(methods): add CounterfactualStressTest method with registration"
```

---

## Chunk 6: ROADMAP Update

### Task 7: Update ROADMAP.md

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Update ROADMAP.md**

In `ROADMAP.md`, change the Counterfactual Stress Testing line from:

```
| ⬜ Planned | Counterfactual Stress Testing | For each key claim in a developing consensus, systematically invert it and check if the conclusion survives. Produces a dependency graph of load-bearing vs. decorative beliefs. **Difficulty: Medium** — needs claim extraction and dependency tracking |
```

to:

```
| ✅ Done | Counterfactual Stress Testing | For each key claim, systematically invert it and assess impact on the conclusion. Classifies claims as LOAD-BEARING, SUPPORTIVE, or DECORATIVE with 1-5 impact scores. `consensus/methods/counterfactual.py` — cf_deliberate (optional) → extract → stress_test → synthesize. Supports both standalone and post-hoc (prior_conclusion) modes |
| ⬜ Planned | Counterfactual dependency graph | Extend Counterfactual Stress Testing with inter-claim dependency mapping — show which claims support other claims, not just the final conclusion |
```

- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: mark Counterfactual Stress Testing as done, add dependency graph extension"
```
