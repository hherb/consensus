# Recursive Decomposition Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Recursive Decomposition discussion method — an LLM-native method that decomposes complex questions into sub-questions, analyzes each through multi-participant discussion, integrates cross-cutting patterns, and recomposes a unified answer.

**Architecture:** Four-phase method (Decompose → Analyze → Integrate → Recompose) using the existing `PhaseHandler` composable pattern. Sub-questions are extracted via `parse_numbered_list` and deduplicated via `word_overlap_similar`. Per-sub-question analyses are parsed from structured headers and accumulated in `method_state`.

**Tech Stack:** Python 3.10+, pytest, existing `consensus.methods` framework

**Spec:** `docs/superpowers/specs/2026-03-12-recursive-decomposition-design.md`

---

## Chunk 1: Helper Module and Parser Tests

### Task 1: Helper module — `_decomposition_helpers.py`

**Files:**
- Create: `consensus/methods/phases/_decomposition_helpers.py`
- Test: `tests/test_decomposition_helpers.py`

- [ ] **Step 1: Write the failing tests for `extract_subquestion_analyses`**

Create `tests/test_decomposition_helpers.py`:

```python
"""Tests for Recursive Decomposition helper utilities."""

import pytest
from consensus.methods.phases._decomposition_helpers import (
    extract_subquestion_analyses,
)


class TestExtractSubquestionAnalyses:
    def test_extracts_bold_subquestion_headers(self):
        content = (
            "**Sub-question 1:** The economy is driven by consumer spending.\n\n"
            "**Sub-question 2:** Interest rates affect investment decisions.\n\n"
            "**Sub-question 3:** Trade policy shapes export markets."
        )
        result = extract_subquestion_analyses(content, 3)
        assert len(result) == 3
        assert 0 in result
        assert "consumer spending" in result[0]
        assert "interest rates" in result[1].lower()
        assert "trade policy" in result[2].lower()

    def test_extracts_short_q_headers(self):
        content = (
            "**Q1:** Analysis of first question.\n\n"
            "**Q2:** Analysis of second question."
        )
        result = extract_subquestion_analyses(content, 2)
        assert len(result) == 2
        assert "first question" in result[0].lower()

    def test_extracts_bold_numbered_headers(self):
        content = (
            "**1.** First analysis paragraph.\n\n"
            "**2.** Second analysis paragraph."
        )
        result = extract_subquestion_analyses(content, 2)
        assert len(result) == 2
        assert "First analysis" in result[0]

    def test_fallback_when_no_headers_detected(self):
        content = "This is a free-form response without any structure."
        result = extract_subquestion_analyses(content, 3)
        assert len(result) == 3
        # All indices get the full text
        assert result[0] == content
        assert result[1] == content
        assert result[2] == content

    def test_handles_extra_sections_beyond_num_subquestions(self):
        content = (
            "**Sub-question 1:** First.\n\n"
            "**Sub-question 2:** Second.\n\n"
            "**Sub-question 3:** Third.\n\n"
            "**Sub-question 4:** Extra one."
        )
        result = extract_subquestion_analyses(content, 3)
        # Only 3 sub-questions expected; extra is ignored
        assert len(result) == 3

    def test_handles_fewer_sections_than_expected(self):
        content = (
            "**Sub-question 1:** Only this one.\n\n"
        )
        result = extract_subquestion_analyses(content, 3)
        # Parsed 1, remaining get empty string
        assert len(result) == 3
        assert "Only this one" in result[0]
        assert result[1] == ""
        assert result[2] == ""

    def test_multiline_analysis_per_subquestion(self):
        content = (
            "**Sub-question 1:** First line of analysis.\n"
            "Continued analysis with more detail.\n"
            "Even more detail here.\n\n"
            "**Sub-question 2:** Second question analysis."
        )
        result = extract_subquestion_analyses(content, 2)
        assert "Continued analysis" in result[0]
        assert "Even more detail" in result[0]

    def test_zero_subquestions_returns_empty(self):
        result = extract_subquestion_analyses("Some content", 0)
        assert result == {}

    def test_empty_content_returns_fallback(self):
        result = extract_subquestion_analyses("", 2)
        assert len(result) == 2
        assert result[0] == ""
        assert result[1] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_decomposition_helpers.py -v`
Expected: FAIL with `ModuleNotFoundError` (module doesn't exist yet)

- [ ] **Step 3: Implement `_decomposition_helpers.py`**

Create `consensus/methods/phases/_decomposition_helpers.py`:

```python
"""Helper utilities for Recursive Decomposition method.

Parsing functions for extracting per-sub-question analyses from
participant responses.
"""

from __future__ import annotations

import re


def extract_subquestion_analyses(content: str, num_subquestions: int) -> dict[int, str]:
    """Extract per-sub-question analyses from a structured response.

    Splits on headers matching patterns like:
      - ``**Sub-question 1:**``
      - ``**Q1:**``
      - ``**1.**``

    Returns a mapping of sub-question index (0-based) to analysis text.
    If no structured headers are detected, falls back to associating
    the entire content with every sub-question index.

    Args:
        content: The participant's full response text.
        num_subquestions: Expected number of sub-questions.

    Returns:
        Dict mapping 0-based sub-question index to analysis text.
    """
    if num_subquestions <= 0:
        return {}

    # Try splitting on structured headers
    pattern = r'\*\*(?:Sub-question\s+|Q)\d+[.:]\*?\*?\s*'
    alt_pattern = r'\*\*\d+\.\*\*\s*'

    sections = _split_on_headers(content, pattern)
    if not sections:
        sections = _split_on_headers(content, alt_pattern)

    if sections:
        result: dict[int, str] = {}
        for idx in range(num_subquestions):
            if idx < len(sections):
                result[idx] = sections[idx].strip()
            else:
                result[idx] = ""
        return result

    # Fallback: associate entire content with all sub-questions
    stripped = content.strip()
    return {idx: stripped for idx in range(num_subquestions)}


def _split_on_headers(content: str, pattern: str) -> list[str]:
    """Split content on header patterns, returning the text after each."""
    parts = re.split(pattern, content, flags=re.IGNORECASE)
    # First element is text before the first header (preamble) — skip it
    sections = [p for p in parts[1:] if p.strip()] if len(parts) > 1 else []
    return sections
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_decomposition_helpers.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/_decomposition_helpers.py tests/test_decomposition_helpers.py
git commit -m "feat(methods): add decomposition helper with sub-question parser"
```

---

## Chunk 2: Phase Handlers

### Task 2: Decompose phase handler

**Files:**
- Create: `consensus/methods/phases/decompose.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_recursive_decomposition.py` (create file):

```python
"""Tests for Recursive Decomposition discussion method."""

import pytest
from consensus.methods.base import ProcessedResponse
from consensus.models import Discussion, Entity, EntityType


@pytest.fixture
def ai_entity():
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def ai_entity_2():
    return Entity(name="TestAI2", entity_type=EntityType.AI, id=2)


class TestDecomposeHandler:
    def test_init_state(self):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        state = handler.init_state(disc)
        assert state["sub_questions"] == []
        assert state["sub_question_analyses"] == {}

    def test_system_prompt_contains_topic(self, ai_entity):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="Why is the sky blue?",
                          discussion_method="recursive_decomposition")
        disc.method_state = {"current_phase": "decompose"}
        prompt = handler.get_system_prompt(ai_entity, disc)
        assert "Why is the sky blue?" in prompt
        assert "3-7" in prompt
        assert "sub-question" in prompt.lower()

    def test_turn_prompt_contains_name(self, ai_entity):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        prompt = handler.get_turn_prompt(ai_entity, disc)
        assert "TestAI" in prompt

    def test_extracts_sub_questions(self, ai_entity):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {"sub_questions": []}

        content = (
            "1. What are the physical mechanisms causing the sky to appear blue?\n"
            "2. How does atmospheric composition affect sky colour?\n"
            "3. Why does the sky change colour at sunset?"
        )
        result = handler.process_response(content, ai_entity, disc)
        assert len(disc.method_state["sub_questions"]) == 3
        assert "physical mechanisms" in disc.method_state["sub_questions"][0].lower()

    def test_deduplicates_sub_questions(self, ai_entity, ai_entity_2):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {"sub_questions": [
            "What are the physical mechanisms causing the sky to appear blue"
        ]}

        # Very similar sub-question should be deduplicated
        content = "1. What are the physical mechanisms that make the sky appear blue"
        handler.process_response(content, ai_entity_2, disc)
        assert len(disc.method_state["sub_questions"]) == 1

        # Genuinely different sub-question should be added
        content = "1. How does altitude affect the perceived colour of the sky"
        handler.process_response(content, ai_entity_2, disc)
        assert len(disc.method_state["sub_questions"]) == 2

    def test_should_advance_needs_subquestions_and_round(self):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")

        # No sub_questions → don't advance
        disc.method_state = {"sub_questions": [], "phase_round": 2}
        assert handler.should_advance(disc) is False

        # Sub-questions but round 1 → don't advance
        disc.method_state = {"sub_questions": ["Q1"], "phase_round": 1}
        assert handler.should_advance(disc) is False

        # Sub-questions and round > 1 → advance
        disc.method_state = {"sub_questions": ["Q1"], "phase_round": 2}
        assert handler.should_advance(disc) is True

    def test_summary_prompt(self):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        prompt = handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_recursive_decomposition.py::TestDecomposeHandler -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `decompose.py`**

Create `consensus/methods/phases/decompose.py`:

```python
"""Decompose phase handler for Recursive Decomposition.

Participants propose 3-7 sub-questions that collectively address the
main question.  Sub-questions are extracted via numbered-list parsing
and deduplicated by word overlap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..parsing import parse_numbered_list, word_overlap_similar
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

MIN_SUBQUESTION_LENGTH = 10
SIMILARITY_THRESHOLD = 0.7


class DecomposeHandler(PhaseHandler):
    """Phase 1: Collaborative question decomposition."""

    phase = Phase(
        name="decompose",
        display_name="Decomposition",
        description=(
            "Each participant proposes 3-7 independent sub-questions that, "
            "if answered thoroughly, would collectively address the main "
            "question."
        ),
        rounds=1,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "sub_questions": [],
            "sub_question_analyses": {},
        }

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a Recursive "
            f"Decomposition analysis.\n"
            f"Topic: {discussion.topic}\n\n"
            "DECOMPOSITION PHASE\n\n"
            "Break the main question into 3-7 independent sub-questions "
            "that, if each were answered thoroughly, would collectively "
            "provide a comprehensive answer to the main question.\n\n"
            "Guidelines:\n"
            "- Each sub-question should be self-contained and answerable "
            "independently\n"
            "- Cover different dimensions or aspects of the problem\n"
            "- Avoid sub-questions that simply restate the main question "
            "in different words\n"
            "- Prefer specific, concrete sub-questions over vague ones\n\n"
            "Format each sub-question on its own line:\n"
            "1. <sub-question>\n"
            "2. <sub-question>\n"
            "...\n\n"
            "For each, provide 1-2 sentences explaining why this "
            "sub-question matters for answering the main question."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}. Propose 3-7 sub-questions "
            "that, if each were answered thoroughly, would collectively "
            "address the main question."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has proposed their sub-questions. Briefly "
            "note the sub-questions proposed and how they complement or "
            f"overlap with previously proposed ones. Next: "
            f"{next_speaker_name}."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        new_sqs = parse_numbered_list(content, min_length=MIN_SUBQUESTION_LENGTH)

        if new_sqs:
            existing = state.get("sub_questions", [])
            for sq in new_sqs:
                if not any(word_overlap_similar(sq, e,
                           threshold=SIMILARITY_THRESHOLD)
                           for e in existing):
                    existing.append(sq)
            state["sub_questions"] = existing

        return ProcessedResponse(
            display_content=content,
            extracted_data={"new_sub_questions": new_sqs},
        )

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        return (bool(state.get("sub_questions"))
                and state.get("phase_round", 1) > 1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_recursive_decomposition.py::TestDecomposeHandler -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/decompose.py tests/test_recursive_decomposition.py
git commit -m "feat(methods): add DecomposeHandler for Recursive Decomposition"
```

### Task 3: Analyze phase handler

**Files:**
- Create: `consensus/methods/phases/analyze_subquestions.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recursive_decomposition.py`:

```python
class TestAnalyzeHandler:
    def test_system_prompt_lists_subquestions(self, ai_entity):
        from consensus.methods.phases.analyze_subquestions import AnalyzeSubquestionsHandler
        handler = AnalyzeSubquestionsHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {
            "current_phase": "analyze",
            "sub_questions": [
                "What causes X?",
                "How does Y affect Z?",
            ],
        }
        prompt = handler.get_system_prompt(ai_entity, disc)
        assert "What causes X?" in prompt
        assert "How does Y affect Z?" in prompt
        assert "Sub-question 1" in prompt or "1." in prompt

    def test_turn_prompt_contains_count(self, ai_entity):
        from consensus.methods.phases.analyze_subquestions import AnalyzeSubquestionsHandler
        handler = AnalyzeSubquestionsHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {"sub_questions": ["Q1", "Q2", "Q3"]}
        prompt = handler.get_turn_prompt(ai_entity, disc)
        assert "3" in prompt
        assert "TestAI" in prompt

    def test_process_response_accumulates_analyses(self, ai_entity, ai_entity_2):
        from consensus.methods.phases.analyze_subquestions import AnalyzeSubquestionsHandler
        handler = AnalyzeSubquestionsHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {
            "sub_questions": ["Q1?", "Q2?"],
            "sub_question_analyses": {},
        }

        content1 = "**Sub-question 1:** Analysis from AI1.\n\n**Sub-question 2:** More from AI1."
        handler.process_response(content1, ai_entity, disc)
        assert len(disc.method_state["sub_question_analyses"]["0"]) == 1
        assert disc.method_state["sub_question_analyses"]["0"][0]["entity"] == "TestAI"

        content2 = "**Sub-question 1:** Analysis from AI2.\n\n**Sub-question 2:** More from AI2."
        handler.process_response(content2, ai_entity_2, disc)
        assert len(disc.method_state["sub_question_analyses"]["0"]) == 2

    def test_standard_round_advancement(self):
        from consensus.methods.phases.analyze_subquestions import AnalyzeSubquestionsHandler
        handler = AnalyzeSubquestionsHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {"phase_round": 1}
        assert handler.should_advance(disc) is False
        disc.method_state["phase_round"] = 2
        assert handler.should_advance(disc) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_recursive_decomposition.py::TestAnalyzeHandler -v`
Expected: FAIL

- [ ] **Step 3: Implement `analyze_subquestions.py`**

Create `consensus/methods/phases/analyze_subquestions.py`:

```python
"""Analyze Sub-questions phase handler for Recursive Decomposition.

Each participant addresses every consolidated sub-question with
focused analysis.  Responses are parsed to extract per-sub-question
sections and accumulated in method_state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._decomposition_helpers import extract_subquestion_analyses

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class AnalyzeSubquestionsHandler(PhaseHandler):
    """Phase 2: Focused analysis of each sub-question."""

    phase = Phase(
        name="analyze",
        display_name="Sub-Question Analysis",
        description=(
            "Each participant addresses every sub-question with "
            "substantive analysis, using structured headers."
        ),
        rounds=1,
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        sub_questions = state.get("sub_questions", [])
        sq_list = "\n".join(
            f"{i + 1}. {sq}" for i, sq in enumerate(sub_questions)
        )
        return (
            f"You are {entity.name}, participating in a Recursive "
            f"Decomposition analysis.\n"
            f"Topic: {discussion.topic}\n\n"
            "SUB-QUESTION ANALYSIS PHASE\n\n"
            "The group has identified the following sub-questions:\n"
            f"{sq_list}\n\n"
            "Address EACH sub-question with substantive analysis. "
            "Use this format:\n\n"
            "**Sub-question 1:** <your analysis>\n\n"
            "**Sub-question 2:** <your analysis>\n\n"
            "...\n\n"
            "For each sub-question, provide your best reasoning, "
            "evidence, and any caveats or uncertainties."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        n = len(discussion.method_state.get("sub_questions", []))
        return (
            f"It is your turn, {entity.name}. Address each of the "
            f"{n} sub-questions with substantive analysis. Use the "
            "**Sub-question N:** format for each."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has provided their analysis of all "
            "sub-questions. Briefly note key points and any notable "
            f"differences from prior analyses. Next: "
            f"{next_speaker_name}."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        sub_questions = state.get("sub_questions", [])
        analyses = state.setdefault("sub_question_analyses", {})

        extractions = extract_subquestion_analyses(content, len(sub_questions))
        for idx, analysis_text in extractions.items():
            key = str(idx)
            analyses.setdefault(key, []).append({
                "entity": entity.name,
                "analysis": analysis_text,
            })

        return ProcessedResponse(
            display_content=content,
            extracted_data={"analyses_extracted": len(extractions)},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_recursive_decomposition.py::TestAnalyzeHandler -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/analyze_subquestions.py tests/test_recursive_decomposition.py
git commit -m "feat(methods): add AnalyzeSubquestionsHandler for Recursive Decomposition"
```

### Task 4: Integrate and Recompose phase handlers

**Files:**
- Create: `consensus/methods/phases/integrate_subquestions.py`
- Create: `consensus/methods/phases/recompose.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recursive_decomposition.py`:

```python
class TestIntegrateHandler:
    def test_system_prompt_mentions_reinforcements_and_conflicts(self, ai_entity):
        from consensus.methods.phases.integrate_subquestions import IntegrateSubquestionsHandler
        handler = IntegrateSubquestionsHandler()
        disc = Discussion(topic="Big question?",
                          discussion_method="recursive_decomposition")
        disc.method_state = {"current_phase": "integrate"}
        prompt = handler.get_system_prompt(ai_entity, disc)
        assert "Big question?" in prompt
        assert "Reinforcements" in prompt
        assert "Conflicts" in prompt
        assert "Gaps" in prompt

    def test_turn_prompt(self, ai_entity):
        from consensus.methods.phases.integrate_subquestions import IntegrateSubquestionsHandler
        handler = IntegrateSubquestionsHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        prompt = handler.get_turn_prompt(ai_entity, disc)
        assert "TestAI" in prompt
        assert "patterns" in prompt.lower() or "contradictions" in prompt.lower()

    def test_no_structured_extraction(self, ai_entity):
        from consensus.methods.phases.integrate_subquestions import IntegrateSubquestionsHandler
        handler = IntegrateSubquestionsHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {}
        result = handler.process_response("Prose integration.", ai_entity, disc)
        assert result.display_content == "Prose integration."
        assert result.extracted_data == {}


class TestRecomposeHandler:
    def test_system_prompt_mentions_synthesis(self, ai_entity):
        from consensus.methods.phases.recompose import RecomposeHandler
        handler = RecomposeHandler()
        disc = Discussion(topic="Original question?",
                          discussion_method="recursive_decomposition")
        disc.method_state = {"current_phase": "recompose"}
        prompt = handler.get_system_prompt(ai_entity, disc)
        assert "Original question?" in prompt
        assert "synthesize" in prompt.lower() or "synthesis" in prompt.lower()

    def test_turn_prompt(self, ai_entity):
        from consensus.methods.phases.recompose import RecomposeHandler
        handler = RecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        prompt = handler.get_turn_prompt(ai_entity, disc)
        assert "TestAI" in prompt
        assert "coherent" in prompt.lower() or "synthesize" in prompt.lower()

    def test_no_structured_extraction(self, ai_entity):
        from consensus.methods.phases.recompose import RecomposeHandler
        handler = RecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {}
        result = handler.process_response("My synthesis.", ai_entity, disc)
        assert result.display_content == "My synthesis."
        assert result.extracted_data == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_recursive_decomposition.py::TestIntegrateHandler tests/test_recursive_decomposition.py::TestRecomposeHandler -v`
Expected: FAIL

- [ ] **Step 3: Implement `integrate_subquestions.py`**

Create `consensus/methods/phases/integrate_subquestions.py`:

```python
"""Integrate phase handler for Recursive Decomposition.

Participants examine sub-question analyses as a whole, identifying
reinforcements, conflicts, gaps, and emergent insights.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class IntegrateSubquestionsHandler(PhaseHandler):
    """Phase 3: Cross-cutting integration of sub-question analyses."""

    phase = Phase(
        name="integrate",
        display_name="Integration",
        description=(
            "Examine all sub-question analyses as a whole to identify "
            "reinforcements, conflicts, gaps, and emergent insights."
        ),
        rounds=1,
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a Recursive "
            f"Decomposition analysis.\n"
            f"Topic: {discussion.topic}\n\n"
            "INTEGRATION PHASE\n\n"
            "The sub-questions and all participants' analyses are in "
            "the discussion history. Examine them as a whole and "
            "identify:\n\n"
            "1. **Reinforcements** — Where do different sub-question "
            "analyses support the same conclusion?\n"
            "2. **Conflicts** — Where do analyses of different "
            "sub-questions point in contradictory directions?\n"
            "3. **Gaps** — What important connections or dependencies "
            "between sub-questions were missed in the analysis phase?\n"
            "4. **Emergent insights** — What becomes visible only when "
            "looking across all sub-questions together?"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}. Examine all sub-question "
            "analyses as a whole. What patterns, contradictions, or "
            "gaps emerge?"
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has identified cross-cutting patterns. "
            "Briefly note the key reinforcements, conflicts, and gaps "
            f"found. Next: {next_speaker_name}."
        )
```

- [ ] **Step 4: Implement `recompose.py`**

Create `consensus/methods/phases/recompose.py`:

```python
"""Recompose phase handler for Recursive Decomposition.

Participants synthesize all sub-question analyses and integration
insights into a coherent, unified answer to the original question.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class RecomposeHandler(PhaseHandler):
    """Phase 4: Synthesis into a unified answer."""

    phase = Phase(
        name="recompose",
        display_name="Recomposition",
        description=(
            "Synthesize all sub-question analyses and integration "
            "insights into a coherent, unified answer to the original "
            "question."
        ),
        rounds=1,
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a Recursive "
            f"Decomposition analysis.\n"
            f"Topic: {discussion.topic}\n\n"
            "RECOMPOSITION PHASE\n\n"
            "All sub-questions have been analyzed and cross-cutting "
            "patterns identified. Now synthesize everything into a "
            "coherent, unified answer to the original question.\n\n"
            "Your synthesis should:\n"
            "- Draw on the sub-question analyses and integration "
            "insights\n"
            "- Account for conflicts and uncertainties identified\n"
            f"- Present a clear, well-structured answer to: "
            f"\"{discussion.topic}\"\n"
            "- Note any aspects that remain unresolved or would benefit "
            "from deeper decomposition"
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}. Synthesize everything "
            "into a coherent answer to the original question."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has proposed their synthesis. Briefly note "
            "how it compares to prior syntheses and what new "
            f"perspectives it brings. Next: {next_speaker_name}."
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_recursive_decomposition.py::TestIntegrateHandler tests/test_recursive_decomposition.py::TestRecomposeHandler -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add consensus/methods/phases/integrate_subquestions.py consensus/methods/phases/recompose.py tests/test_recursive_decomposition.py
git commit -m "feat(methods): add Integrate and Recompose handlers for Recursive Decomposition"
```

---

## Chunk 3: Method Class and Registration

### Task 5: Method class and registration

**Files:**
- Create: `consensus/methods/recursive_decomposition.py`
- Modify: `consensus/methods/__init__.py`
- Modify: `consensus/methods/phases/__init__.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recursive_decomposition.py`:

```python
class TestRecursiveDecompositionMethod:
    def test_registered_in_registry(self):
        from consensus.methods import get_method, list_methods
        method = get_method("recursive_decomposition")
        assert method.name == "recursive_decomposition"
        assert method.display_name == "Recursive Decomposition"

        names = [m["name"] for m in list_methods()]
        assert "recursive_decomposition" in names

    def test_has_four_phases(self):
        from consensus.methods import get_method
        method = get_method("recursive_decomposition")
        assert len(method.default_phases) == 4
        assert [p.name for p in method.default_phases] == [
            "decompose", "analyze", "integrate", "recompose"
        ]

    def test_init_state(self):
        from consensus.methods import get_method
        method = get_method("recursive_decomposition")
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        state = method.init_state(disc)
        assert state["current_phase"] == "decompose"
        assert state["sub_questions"] == []
        assert state["sub_question_analyses"] == {}

    def test_phase_transitions(self):
        from consensus.methods import get_method
        method = get_method("recursive_decomposition")
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = method.init_state(disc)

        # Decompose → Analyze
        disc.method_state["sub_questions"] = ["Q1", "Q2"]
        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True
        new_phase = method.advance_phase(disc)
        assert new_phase.name == "analyze"

        # Analyze → Integrate
        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True
        new_phase = method.advance_phase(disc)
        assert new_phase.name == "integrate"

        # Integrate → Recompose
        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True
        new_phase = method.advance_phase(disc)
        assert new_phase.name == "recompose"

        # Recompose → done
        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True
        result = method.advance_phase(disc)
        assert result is None

    def test_conclusion_prompt_includes_subquestions(self):
        from consensus.methods import get_method
        method = get_method("recursive_decomposition")
        disc = Discussion(topic="Why is the sky blue?",
                          discussion_method="recursive_decomposition")
        disc.method_state = {
            "sub_questions": [
                "What is Rayleigh scattering?",
                "How does wavelength affect scattering?",
            ],
        }
        prompt = method.get_conclusion_prompt(disc)
        assert "Why is the sky blue?" in prompt
        assert "Rayleigh scattering" in prompt
        assert "wavelength" in prompt
        assert "Sub-question findings" in prompt

    def test_system_prompts_delegate_to_handlers(self, ai_entity):
        from consensus.methods import get_method
        method = get_method("recursive_decomposition")
        disc = Discussion(topic="Test topic?",
                          discussion_method="recursive_decomposition")
        disc.method_state = method.init_state(disc)

        # Decompose phase
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "DECOMPOSITION" in prompt

        # Analyze phase
        disc.method_state["current_phase"] = "analyze"
        disc.method_state["sub_questions"] = ["Q1?"]
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "SUB-QUESTION ANALYSIS" in prompt

        # Integrate phase
        disc.method_state["current_phase"] = "integrate"
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "INTEGRATION" in prompt

        # Recompose phase
        disc.method_state["current_phase"] = "recompose"
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "RECOMPOSITION" in prompt

    def test_to_dict(self):
        from consensus.methods import get_method
        method = get_method("recursive_decomposition")
        d = method.to_dict()
        assert d["name"] == "recursive_decomposition"
        assert d["display_name"] == "Recursive Decomposition"
        assert len(d["phases"]) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_recursive_decomposition.py::TestRecursiveDecompositionMethod -v`
Expected: FAIL

- [ ] **Step 3: Implement `recursive_decomposition.py`**

Create `consensus/methods/recursive_decomposition.py`:

```python
"""Recursive Decomposition — LLM-native decompose-and-recompose method.

Participants collaboratively break a complex question into sub-questions,
each sub-question is analyzed by all participants, cross-cutting patterns
are identified, and results are recomposed into a coherent answer.

Phases:
  1. DECOMPOSE   — Participants propose sub-questions; moderator consolidates
  2. ANALYZE     — Each participant analyses every sub-question
  3. INTEGRATE   — Identify reinforcements, conflicts, gaps across analyses
  4. RECOMPOSE   — Synthesize a unified answer to the original question
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases.decompose import DecomposeHandler
from .phases.analyze_subquestions import AnalyzeSubquestionsHandler
from .phases.integrate_subquestions import IntegrateSubquestionsHandler
from .phases.recompose import RecomposeHandler

if TYPE_CHECKING:
    from ..models import Discussion


class RecursiveDecomposition(DiscussionMethod):
    """Recursive Decomposition — decompose, analyse, integrate, recompose."""

    name = "recursive_decomposition"
    display_name = "Recursive Decomposition"
    description = (
        "An LLM-native method: collaboratively decompose a complex "
        "question into sub-questions, analyse each through multi-"
        "participant discussion, identify cross-cutting patterns, and "
        "recompose a unified answer.  Exploits structured decomposition "
        "and synthesis across abstraction levels."
    )
    phase_handlers = (
        DecomposeHandler(),
        AnalyzeSubquestionsHandler(),
        IntegrateSubquestionsHandler(),
        RecomposeHandler(),
    )

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        sub_questions = state.get("sub_questions", [])
        sq_list = "\n".join(
            f"{i + 1}. {sq}" for i, sq in enumerate(sub_questions)
        )
        return (
            "The Recursive Decomposition analysis is complete.\n\n"
            f"Original question: \"{discussion.topic}\"\n\n"
            "The group decomposed this into the following sub-questions:\n"
            f"{sq_list}\n\n"
            "Provide a comprehensive final synthesis:\n"
            "1. **Sub-question findings** — Summarize the key findings "
            "for each sub-question, noting where participants agreed "
            "and diverged\n"
            "2. **Cross-cutting patterns** — What reinforcements, "
            "conflicts, and emergent insights were identified during "
            "integration?\n"
            "3. **Consolidated answer** — Provide a clear, unified "
            "answer to the original question that accounts for all "
            "sub-analyses\n"
            "4. **Confidence and caveats** — What aspects of the answer "
            "are well-supported vs. uncertain?\n"
            "5. **Decomposition assessment** — Were any sub-questions "
            "too complex for single-level analysis and would benefit "
            "from further decomposition?\n\n"
            "Ground your synthesis in the specific analyses and "
            "integrations provided by participants."
        )
```

- [ ] **Step 4: Update `consensus/methods/phases/__init__.py`**

Add imports and exports for the 4 new handlers. Add after the last existing import (currently `TallyHandler`):

```python
from .decompose import DecomposeHandler
from .analyze_subquestions import AnalyzeSubquestionsHandler
from .integrate_subquestions import IntegrateSubquestionsHandler
from .recompose import RecomposeHandler
```

Add to `__all__`:
```python
"DecomposeHandler",
"AnalyzeSubquestionsHandler",
"IntegrateSubquestionsHandler",
"RecomposeHandler",
```

- [ ] **Step 5: Update `consensus/methods/__init__.py`**

Add import:
```python
from .recursive_decomposition import RecursiveDecomposition
```

Add to `_METHODS` dict:
```python
"recursive_decomposition": RecursiveDecomposition,
```

Add to `__all__`:
```python
"RecursiveDecomposition",
```

Note: Adding this entry will invalidate the cached `_METHODS_METADATA`. The existing `list_methods()` function handles this correctly — it caches lazily and will include the new method on next call. However, if tests run after the cache is populated without the new method, clear `_METHODS_METADATA = None` or restart the test process. In practice, pytest runs in a fresh process so this is not an issue.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_recursive_decomposition.py -v`
Expected: All PASS

- [ ] **Step 7: Run full test suite to check for regressions**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/ -v`
Expected: All existing tests PASS, all new tests PASS

- [ ] **Step 8: Commit**

```bash
git add consensus/methods/recursive_decomposition.py consensus/methods/__init__.py consensus/methods/phases/__init__.py tests/test_recursive_decomposition.py
git commit -m "feat(methods): add RecursiveDecomposition method with registration"
```

---

## Chunk 4: Update existing registry test

### Task 6: Update existing test to include new method

**Files:**
- Modify: `tests/test_methods.py`

- [ ] **Step 1: Add `recursive_decomposition` to the registry test**

In `tests/test_methods.py`, in the `TestRegistry.test_list_methods_returns_all` method, add:

```python
assert "recursive_decomposition" in names
```

- [ ] **Step 2: Run existing test suite**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_methods.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_methods.py
git commit -m "test: add recursive_decomposition to registry test"
```
