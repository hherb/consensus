# Composable Phase Library Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the discussion method system so phases are standalone, reusable objects — enabling method composition and eliminating cross-method code duplication.

**Architecture:** Introduce a `PhaseHandler` ABC whose instances own all behavior for a single phase. `DiscussionMethod` gains a `phase_handlers` tuple and delegates its existing hooks to the active handler. All 8 structured methods are refactored to assemble phase handlers. Engine call sites (`moderator.py`, `app_discussion_flow.py`) are unchanged.

**Tech Stack:** Python 3.11+, dataclasses, ABC. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-03-11-composable-phase-library-design.md`

---

## Chunk 1: Foundation (parsing utilities, PhaseHandler ABC, base class changes)

### Task 1: Create branch and shared parsing utilities

**Files:**
- Create: `consensus/methods/parsing.py`
- Create: `tests/test_methods_parsing.py`

- [ ] **Step 1: Create feature branch**

```bash
git checkout -b feature/composable-phases
```

- [ ] **Step 2: Write failing tests for parsing utilities**

Create `tests/test_methods_parsing.py` with tests for the three functions being extracted:

```python
"""Tests for shared method parsing utilities."""

import pytest
from consensus.methods.parsing import (
    extract_json_block,
    parse_numbered_list,
    word_overlap_similar,
)


class TestExtractJsonBlock:
    def test_extracts_fenced_json(self):
        content = 'Some text\n```json\n{"key": "value"}\n```\nMore text'
        result = extract_json_block(content)
        assert result == {"key": "value"}

    def test_extracts_unfenced_json(self):
        content = 'Some text\n```\n{"key": "value"}\n```\nMore text'
        result = extract_json_block(content)
        assert result == {"key": "value"}

    def test_returns_none_for_no_json(self):
        assert extract_json_block("no json here") is None

    def test_returns_none_for_invalid_json(self):
        content = '```json\n{invalid json}\n```'
        assert extract_json_block(content) is None

    def test_extracts_nested_json(self):
        content = '```json\n{"beliefs": {"h1": 0.6, "h2": 0.4}}\n```'
        result = extract_json_block(content)
        assert result["beliefs"]["h1"] == 0.6

    def test_extracts_json_array(self):
        content = '```json\n[{"a": 1}, {"b": 2}]\n```'
        result = extract_json_block(content)
        assert isinstance(result, list)
        assert len(result) == 2


class TestParseNumberedList:
    def test_parses_dot_numbered(self):
        content = "1. First assumption here\n2. Second assumption here"
        result = parse_numbered_list(content)
        assert len(result) == 2
        assert "First assumption here" in result[0]

    def test_parses_paren_numbered(self):
        content = "1) First assumption here\n2) Second assumption here"
        result = parse_numbered_list(content)
        assert len(result) == 2

    def test_parses_prefixed(self):
        content = "A1: First assumption here\nA2: Second assumption here"
        result = parse_numbered_list(content)
        assert len(result) == 2

    def test_parses_hypothesis_prefixed(self):
        content = "H1: First hypothesis here\nH2: Second hypothesis here"
        result = parse_numbered_list(content)
        assert len(result) == 2

    def test_parses_bullet_list(self):
        content = "- First assumption here\n- Second assumption here"
        result = parse_numbered_list(content)
        assert len(result) == 2

    def test_filters_short_items(self):
        content = "1. Short\n2. This is a sufficiently long item"
        result = parse_numbered_list(content, min_length=10)
        assert len(result) == 1

    def test_returns_empty_for_no_list(self):
        assert parse_numbered_list("Just a paragraph of text.") == []

    def test_strips_trailing_period(self):
        content = "1. An assumption with period."
        result = parse_numbered_list(content)
        assert not result[0].endswith(".")


class TestWordOverlapSimilar:
    def test_identical_strings(self):
        assert word_overlap_similar("hello world", "hello world")

    def test_similar_strings(self):
        assert word_overlap_similar(
            "the economy will grow steadily",
            "the economy will continue to grow steadily",
        )

    def test_dissimilar_strings(self):
        assert not word_overlap_similar(
            "the sun is hot",
            "fish swim in water",
        )

    def test_custom_threshold(self):
        assert word_overlap_similar("a b c d", "a b c e", threshold=0.5)
        assert not word_overlap_similar("a b c d", "a b c e", threshold=0.9)

    def test_empty_strings(self):
        assert not word_overlap_similar("", "hello")
        assert not word_overlap_similar("hello", "")
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_methods_parsing.py -v
```

Expected: ImportError — `consensus.methods.parsing` does not exist.

- [ ] **Step 4: Implement parsing utilities**

Create `consensus/methods/parsing.py`:

```python
"""Shared parsing utilities for discussion method phase handlers.

Pure functions for extracting structured data from AI-generated text.
Used across multiple methods to avoid duplication.
"""

from __future__ import annotations

import json
import re
from typing import Optional, Union


def extract_json_block(content: str) -> Optional[Union[dict, list]]:
    """Extract the first JSON object or array from a fenced code block.

    Handles both ```json and ``` fences.  Returns None if no valid
    JSON block is found.
    """
    match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```',
                      content, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


def parse_numbered_list(content: str, min_length: int = 10) -> list[str]:
    """Extract items from a numbered, prefixed, or bulleted list.

    Tries multiple patterns in priority order:
      1. Dot/paren numbered: ``1. item`` or ``1) item``
      2. Prefixed: ``A1: item`` or ``H2) item``
      3. Bulleted: ``- item`` or ``* item``

    Items shorter than *min_length* characters are filtered out.
    Trailing periods are stripped.
    """
    patterns = [
        r'^\s*\d+[\.\)]\s*(.+)',
        r'^\s*[A-Z]\d+[\.\):]\s*(.+)',
        r'^\s*[-*]\s+(.+)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, content, re.MULTILINE)
        if matches:
            return [m.strip().rstrip('.')
                    for m in matches
                    if len(m.strip()) >= min_length]
    return []


def word_overlap_similar(a: str, b: str, threshold: float = 0.7) -> bool:
    """Check if two strings are substantially similar by word overlap.

    Returns True if the Jaccard-like overlap ratio exceeds *threshold*.
    """
    w1 = set(a.lower().split())
    w2 = set(b.lower().split())
    if not w1 or not w2:
        return False
    overlap = len(w1 & w2) / max(len(w1), len(w2))
    return overlap > threshold
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_methods_parsing.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add consensus/methods/parsing.py tests/test_methods_parsing.py
git commit -m "feat(methods): add shared parsing utilities for phase handlers"
```

---

### Task 2: Create the PhaseHandler ABC

**Files:**
- Create: `consensus/methods/phase_handler.py`
- Create: `tests/test_phase_handler.py`

- [ ] **Step 1: Write failing tests for PhaseHandler**

Create `tests/test_phase_handler.py`:

```python
"""Tests for the PhaseHandler ABC."""

import pytest
from consensus.methods.phase_handler import PhaseHandler
from consensus.methods.base import Phase, ProcessedResponse
from consensus.models import Discussion, Entity, EntityType


class ConcreteHandler(PhaseHandler):
    """Minimal concrete handler for testing."""
    phase = Phase(name="test", display_name="Test Phase",
                  description="A test phase.", rounds=2)

    def get_system_prompt(self, entity, discussion):
        return f"System prompt for {entity.name}"

    def get_turn_prompt(self, entity, discussion):
        return f"Your turn, {entity.name}"


@pytest.fixture
def handler():
    return ConcreteHandler()


@pytest.fixture
def entity():
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def discussion():
    disc = Discussion(topic="test", discussion_method="test_method")
    disc.method_state = {"current_phase": "test", "phase_round": 1}
    return disc


class TestPhaseHandlerDefaults:
    def test_get_system_prompt(self, handler, entity, discussion):
        assert handler.get_system_prompt(entity, discussion) == "System prompt for TestAI"

    def test_get_turn_prompt(self, handler, entity, discussion):
        assert handler.get_turn_prompt(entity, discussion) == "Your turn, TestAI"

    def test_get_summary_prompt_default_empty(self, handler, discussion):
        assert handler.get_summary_prompt(discussion, "A", "B") == ""

    def test_filter_context_message_default_passthrough(self, handler, discussion):
        assert handler.filter_context_message("Bob", "hello", "user", discussion) == "hello"

    def test_process_response_default(self, handler, entity, discussion):
        result = handler.process_response("content", entity, discussion)
        assert isinstance(result, ProcessedResponse)
        assert result.display_content == "content"

    def test_init_state_default_empty(self, handler, discussion):
        assert handler.init_state(discussion) == {}

    def test_get_turn_order_default_passthrough(self, handler, discussion):
        assert handler.get_turn_order([1, 2, 3], discussion) == [1, 2, 3]


class TestPhaseHandlerAdvance:
    def test_should_advance_false_when_rounds_not_exceeded(self, handler, discussion):
        discussion.method_state["phase_round"] = 1
        assert not handler.should_advance(discussion)

    def test_should_advance_false_at_round_limit(self, handler, discussion):
        discussion.method_state["phase_round"] = 2
        assert not handler.should_advance(discussion)

    def test_should_advance_true_when_exceeded(self, handler, discussion):
        discussion.method_state["phase_round"] = 3
        assert handler.should_advance(discussion)

    def test_should_advance_false_for_unlimited(self, discussion):
        class UnlimitedHandler(ConcreteHandler):
            phase = Phase(name="open", display_name="Open", rounds=0)
        h = UnlimitedHandler()
        discussion.method_state["phase_round"] = 100
        assert not h.should_advance(discussion)


class TestPhaseHandlerTransition:
    def test_transition_message(self, handler, discussion):
        msg = handler.get_transition_message(discussion)
        assert "Test Phase" in msg
        assert "A test phase" in msg


class TestPhaseHandlerABC:
    def test_cannot_instantiate_without_prompts(self):
        with pytest.raises(TypeError):
            class Incomplete(PhaseHandler):
                phase = Phase(name="x", display_name="X")
            Incomplete()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_phase_handler.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement PhaseHandler ABC**

Create `consensus/methods/phase_handler.py`:

```python
"""PhaseHandler — the building block for composable discussion phases.

Each PhaseHandler encapsulates all behavior for one phase of a discussion
method: prompts, response processing, advancement logic, and state
initialization.  Methods assemble ordered sequences of handlers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .base import Phase, ProcessedResponse

if TYPE_CHECKING:
    from ..models import Discussion, Entity


class PhaseHandler(ABC):
    """A reusable, self-contained phase of a discussion method.

    Subclasses must set the ``phase`` class attribute and implement
    ``get_system_prompt`` and ``get_turn_prompt``.  All other methods
    have sensible defaults.
    """

    phase: Phase  # metadata — set as class attribute on each subclass

    # ------------------------------------------------------------------
    # Prompt hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        """Return the system prompt for a participant in this phase."""

    @abstractmethod
    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        """Return the turn instruction for a participant in this phase."""

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        """Return the moderator summary prompt for this phase.

        Default returns empty string (use standard DB template).
        """
        return ""

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion) -> str:
        """Transform a context message before sending to the AI.

        Default: no transformation.
        """
        return content

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Post-process a participant's response in this phase.

        Default: no transformation, no extracted data.
        """
        return ProcessedResponse(display_content=content)

    # ------------------------------------------------------------------
    # Phase lifecycle
    # ------------------------------------------------------------------

    def init_state(self, discussion: Discussion) -> dict:
        """Return phase-specific initial state keys.

        These are merged into the discussion's method_state at
        discussion start.  Default: no additional state.
        """
        return {}

    def should_advance(self, discussion: Discussion) -> bool:
        """Return True if this phase is complete.

        Default: advance when phase_round exceeds self.phase.rounds.
        Phases with rounds=0 never auto-advance.
        """
        if self.phase.rounds == 0:
            return False
        phase_round = discussion.method_state.get("phase_round", 1)
        return phase_round > self.phase.rounds

    def get_transition_message(self, discussion: Discussion) -> str:
        """Return a system message posted when transitioning TO this phase."""
        return (
            f"**Phase transition:** Moving to *{self.phase.display_name}*."
            f"\n\n{self.phase.description}"
        )

    # ------------------------------------------------------------------
    # Turn order
    # ------------------------------------------------------------------

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Return entity IDs in the desired order for this phase.

        Default: preserve existing order.
        """
        return entity_ids
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_phase_handler.py -v
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phase_handler.py tests/test_phase_handler.py
git commit -m "feat(methods): add PhaseHandler ABC for composable phases"
```

---

### Task 3: Modify DiscussionMethod base to support delegation

**Files:**
- Modify: `consensus/methods/base.py`
- Create: `tests/test_method_delegation.py`

This is the most critical task — the base class gains delegation while remaining backward-compatible with methods that don't use handlers (OpenDiscussion).

- [ ] **Step 1: Write failing tests for delegation**

Create `tests/test_method_delegation.py`:

```python
"""Tests for DiscussionMethod delegation to PhaseHandler instances."""

import pytest
from consensus.methods.base import DiscussionMethod, Phase, ProcessedResponse
from consensus.methods.phase_handler import PhaseHandler
from consensus.models import Discussion, Entity, EntityType


# -- Test handlers --

class AlphaHandler(PhaseHandler):
    phase = Phase(name="alpha", display_name="Alpha Phase",
                  description="First phase.", rounds=1)

    def get_system_prompt(self, entity, discussion):
        return f"Alpha system for {entity.name}"

    def get_turn_prompt(self, entity, discussion):
        return f"Alpha turn for {entity.name}"

    def get_summary_prompt(self, discussion, speaker, next_speaker):
        return f"Alpha summary: {speaker} done, {next_speaker} next"

    def init_state(self, discussion):
        return {"alpha_data": []}

    def process_response(self, content, entity, discussion):
        discussion.method_state.setdefault("alpha_data", []).append(content)
        return ProcessedResponse(display_content=content,
                                 extracted_data={"added": content})

    def get_transition_message(self, discussion):
        return "Entering Alpha phase."


class BetaHandler(PhaseHandler):
    phase = Phase(name="beta", display_name="Beta Phase",
                  description="Second phase.", rounds=2)

    def get_system_prompt(self, entity, discussion):
        alpha_data = discussion.method_state.get("alpha_data", [])
        return f"Beta system. Alpha produced: {alpha_data}"

    def get_turn_prompt(self, entity, discussion):
        return f"Beta turn for {entity.name}"

    def init_state(self, discussion):
        return {"beta_count": 0}

    def get_turn_order(self, entity_ids, discussion):
        return list(reversed(entity_ids))


class HandlerMethod(DiscussionMethod):
    """A method assembled from handlers."""
    name = "handler_test"
    display_name = "Handler Test"
    description = "Test method with handlers."
    phase_handlers = (AlphaHandler(), BetaHandler())


# -- Fixtures --

@pytest.fixture
def method():
    return HandlerMethod()

@pytest.fixture
def entity():
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)

@pytest.fixture
def discussion(method):
    disc = Discussion(topic="test", discussion_method="handler_test")
    disc.method_state = method.init_state(disc)
    return disc


class TestDelegation:
    def test_default_phases_derived_from_handlers(self, method):
        assert len(method.default_phases) == 2
        assert method.default_phases[0].name == "alpha"
        assert method.default_phases[1].name == "beta"

    def test_init_state_merges_handlers(self, method, discussion):
        state = method.init_state(discussion)
        assert state["current_phase"] == "alpha"
        assert state["phase_round"] == 1
        assert state["alpha_data"] == []
        assert state["beta_count"] == 0

    def test_system_prompt_delegates_to_active(self, method, entity, discussion):
        prompt = method.get_system_prompt(entity, discussion)
        assert prompt == "Alpha system for TestAI"

    def test_turn_prompt_delegates_to_active(self, method, entity, discussion):
        prompt = method.get_turn_prompt(entity, discussion)
        assert prompt == "Alpha turn for TestAI"

    def test_summary_prompt_delegates(self, method, discussion):
        prompt = method.get_summary_prompt(discussion, "Alice", "Bob")
        assert prompt == "Alpha summary: Alice done, Bob next"

    def test_process_response_delegates(self, method, entity, discussion):
        result = method.process_response("hello", entity, discussion)
        assert result.extracted_data == {"added": "hello"}
        assert discussion.method_state["alpha_data"] == ["hello"]

    def test_should_advance_delegates(self, method, discussion):
        discussion.method_state["phase_round"] = 1
        assert not method.should_advance_phase(discussion)
        discussion.method_state["phase_round"] = 2
        assert method.should_advance_phase(discussion)

    def test_advance_phase_works(self, method, discussion):
        discussion.method_state["phase_round"] = 2
        new_phase = method.advance_phase(discussion)
        assert new_phase.name == "beta"
        assert discussion.method_state["current_phase"] == "beta"
        assert discussion.method_state["phase_round"] == 1

    def test_delegates_to_beta_after_advance(self, method, entity, discussion):
        discussion.method_state["current_phase"] = "beta"
        prompt = method.get_system_prompt(entity, discussion)
        assert "Beta system" in prompt

    def test_turn_order_delegates(self, method, discussion):
        discussion.method_state["current_phase"] = "beta"
        order = method.get_turn_order([1, 2, 3], discussion)
        assert order == [3, 2, 1]

    def test_transition_message_uses_new_phase_handler(self, method, discussion):
        alpha_phase = method.default_phases[0]
        msg = method.get_phase_transition_message(alpha_phase, discussion)
        assert msg == "Entering Alpha phase."

    def test_filter_context_default_passthrough(self, method, discussion):
        result = method.filter_context_message("Bob", "hi", "user", discussion)
        assert result == "hi"


class TestBackwardCompatibility:
    """Methods without handlers still work via direct override."""

    def test_open_discussion_still_works(self):
        from consensus.methods.open_discussion import OpenDiscussion
        method = OpenDiscussion()
        disc = Discussion(topic="test", discussion_method="open_discussion")
        entity = Entity(name="AI", entity_type=EntityType.AI, id=1)
        assert method.get_system_prompt(entity, disc) == ""
        assert method.get_turn_prompt(entity, disc) == ""
        assert len(method.default_phases) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_method_delegation.py -v
```

Expected: Failures — `DiscussionMethod` does not have `phase_handlers` or delegation logic yet.

- [ ] **Step 3: Modify DiscussionMethod base class**

Edit `consensus/methods/base.py` to add delegation. Key changes:

1. Add `phase_handlers: tuple[PhaseHandler, ...] = ()` class attribute.
2. Add `_active_handler(discussion)` private method.
3. **Remove the `@abstractmethod` decorators and implementations** of `get_system_prompt` and `get_turn_prompt` (lines 117-123 of original). Replace them with the dispatcher versions below.
4. Override `init_state` to merge handler states.
5. Override dispatchers for `should_advance_phase`, `get_summary_prompt`, `filter_context_message`, `process_response`, `get_turn_order`, `get_phase_transition_message`.
6. Auto-derive `default_phases` from `phase_handlers` in `__init_subclass__`.

The key principle: if `phase_handlers` is non-empty, delegation kicks in. If empty (or method overrides a hook directly), existing behavior is preserved.

```python
# In base.py, add import at top:
from typing import TYPE_CHECKING, Any, Optional
# PhaseHandler imported conditionally to avoid circular import:
# phase_handler.py imports from base.py, so base.py uses string reference

class DiscussionMethod(ABC):
    name: str = ""
    display_name: str = ""
    description: str = ""
    default_phases: tuple[Phase, ...] = ()
    phase_handlers: tuple = ()  # tuple[PhaseHandler, ...] — see phase_handler.py

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Auto-derive default_phases from phase_handlers if handlers are set
        # and default_phases was not explicitly overridden
        if cls.phase_handlers and not cls.__dict__.get("default_phases"):
            cls.default_phases = tuple(h.phase for h in cls.phase_handlers)

    def _active_handler(self, discussion: Discussion):
        """Look up the PhaseHandler for the current phase."""
        if not self.phase_handlers:
            return None
        phase_name = discussion.method_state.get("current_phase", "")
        for h in self.phase_handlers:
            if h.phase.name == phase_name:
                return h
        return self.phase_handlers[0] if self.phase_handlers else None

    def _handler_for_phase(self, phase_name: str):
        """Look up a PhaseHandler by phase name."""
        for h in self.phase_handlers:
            if h.phase.name == phase_name:
                return h
        return None

    # --- Prompt hooks become dispatchers ---

    def get_system_prompt(self, entity, discussion) -> str:
        h = self._active_handler(discussion)
        return h.get_system_prompt(entity, discussion) if h else ""

    def get_turn_prompt(self, entity, discussion) -> str:
        h = self._active_handler(discussion)
        return h.get_turn_prompt(entity, discussion) if h else ""

    def get_summary_prompt(self, discussion, speaker_name, next_speaker_name) -> str:
        h = self._active_handler(discussion)
        if h:
            return h.get_summary_prompt(discussion, speaker_name, next_speaker_name)
        return ""

    def filter_context_message(self, entity_name, content, role, discussion) -> str:
        h = self._active_handler(discussion)
        if h:
            return h.filter_context_message(entity_name, content, role, discussion)
        return content

    # get_conclusion_prompt stays as-is (cross-phase, method-level)

    def get_phase_transition_message(self, new_phase, discussion) -> str:
        h = self._handler_for_phase(new_phase.name)
        if h:
            return h.get_transition_message(discussion)
        return (
            f"**Phase transition:** Moving to *{new_phase.display_name}*.\n\n"
            f"{new_phase.description}"
        )

    # --- Response processing ---

    def process_response(self, content, entity, discussion) -> ProcessedResponse:
        h = self._active_handler(discussion)
        if h:
            return h.process_response(content, entity, discussion)
        return ProcessedResponse(display_content=content)

    # --- Phase management ---

    def init_state(self, discussion) -> dict:
        if self.phase_handlers:
            state = {
                "current_phase": self.phase_handlers[0].phase.name,
                "phase_round": 1,
            }
            for h in self.phase_handlers:
                state.update(h.init_state(discussion))
            return state
        return {
            "current_phase": self.default_phases[0].name if self.default_phases else "",
            "phase_round": 1,
        }

    def should_advance_phase(self, discussion) -> bool:
        h = self._active_handler(discussion)
        if h:
            return h.should_advance(discussion)
        phase = self.current_phase(discussion)
        if not phase or phase.rounds == 0:
            return False
        return discussion.method_state.get("phase_round", 1) > phase.rounds

    # --- Turn order ---

    def get_turn_order(self, entity_ids, discussion) -> list[int]:
        h = self._active_handler(discussion)
        if h:
            return h.get_turn_order(entity_ids, discussion)
        return entity_ids
```

Note: `get_system_prompt` and `get_turn_prompt` are **no longer abstract**. `OpenDiscussion` already overrides them (returning ""), so it continues to work. The `ABC` base class no longer requires them — but `PhaseHandler` does.

- [ ] **Step 4: Run new delegation tests**

```bash
pytest tests/test_method_delegation.py -v
```

Expected: All pass.

- [ ] **Step 5: Run all existing method tests to verify backward compatibility**

```bash
pytest tests/test_methods.py tests/test_voting_method.py -v
```

Expected: All pass — existing methods still work via direct override.

- [ ] **Step 6: Commit**

```bash
git add consensus/methods/base.py tests/test_method_delegation.py
git commit -m "feat(methods): add PhaseHandler delegation to DiscussionMethod base"
```

---

## Chunk 2: Create phases/ package and refactor KeyAssumptionsCheck

KeyAssumptionsCheck is the simplest multi-phase method (3 phases, no complex state) — ideal for validating the pattern before refactoring more complex methods.

### Task 4: Create phases package and refactor KeyAssumptionsCheck

**Files:**
- Create: `consensus/methods/phases/__init__.py`
- Create: `consensus/methods/phases/surface_assumptions.py`
- Create: `consensus/methods/phases/challenge_assumptions.py`
- Create: `consensus/methods/phases/assess_assumptions.py`
- Modify: `consensus/methods/key_assumptions.py`
- Create: `tests/test_phases_key_assumptions.py`

- [ ] **Step 1: Create phases package**

```bash
mkdir -p consensus/methods/phases
touch consensus/methods/phases/__init__.py
```

- [ ] **Step 2: Write failing tests for KAC phase handlers**

Create `tests/test_phases_key_assumptions.py` testing each handler in isolation AND the refactored method producing identical output to the original:

```python
"""Tests for Key Assumptions Check phase handlers."""

import pytest
from consensus.methods.phases.surface_assumptions import SurfaceAssumptionsHandler
from consensus.methods.phases.challenge_assumptions import ChallengeAssumptionsHandler
from consensus.methods.phases.assess_assumptions import AssessAssumptionsHandler
from consensus.methods.base import ProcessedResponse
from consensus.models import Discussion, Entity, EntityType


@pytest.fixture
def entity():
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)

@pytest.fixture
def discussion():
    disc = Discussion(topic="Is the Earth flat?",
                      discussion_method="key_assumptions")
    disc.method_state = {"current_phase": "surface", "phase_round": 1,
                         "assumptions": []}
    return disc


class TestSurfaceAssumptionsHandler:
    def test_system_prompt_contains_surfacing(self, entity, discussion):
        h = SurfaceAssumptionsHandler()
        prompt = h.get_system_prompt(entity, discussion)
        assert "ASSUMPTION SURFACING" in prompt
        assert entity.name in prompt
        assert discussion.topic in prompt

    def test_turn_prompt(self, entity, discussion):
        h = SurfaceAssumptionsHandler()
        prompt = h.get_turn_prompt(entity, discussion)
        assert entity.name in prompt
        assert "3-5" in prompt

    def test_process_response_extracts_assumptions(self, entity, discussion):
        h = SurfaceAssumptionsHandler()
        content = "1. The Earth is a sphere\n2. Gravity pulls things down\n3. Water finds its level naturally"
        result = h.process_response(content, entity, discussion)
        assert len(discussion.method_state["assumptions"]) >= 2

    def test_process_response_deduplicates(self, entity, discussion):
        h = SurfaceAssumptionsHandler()
        discussion.method_state["assumptions"] = ["The Earth is a sphere"]
        content = "1. The Earth is a sphere\n2. Gravity pulls things down"
        h.process_response(content, entity, discussion)
        # Should not add duplicate
        assert discussion.method_state["assumptions"].count("The Earth is a sphere") <= 1

    def test_should_advance_needs_assumptions_and_round(self, discussion):
        h = SurfaceAssumptionsHandler()
        assert not h.should_advance(discussion)  # no assumptions, round 1
        discussion.method_state["assumptions"] = ["something here"]
        assert not h.should_advance(discussion)  # has assumptions, round 1
        discussion.method_state["phase_round"] = 2
        assert h.should_advance(discussion)  # has assumptions, round > 1

    def test_should_advance_false_without_assumptions_even_after_round(self, discussion):
        h = SurfaceAssumptionsHandler()
        discussion.method_state["phase_round"] = 2
        discussion.method_state["assumptions"] = []
        assert not h.should_advance(discussion)

    def test_init_state(self, discussion):
        h = SurfaceAssumptionsHandler()
        state = h.init_state(discussion)
        assert state == {"assumptions": []}

    def test_summary_prompt(self, discussion):
        h = SurfaceAssumptionsHandler()
        prompt = h.get_summary_prompt(discussion, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt


class TestChallengeAssumptionsHandler:
    def test_system_prompt_includes_assumptions(self, entity, discussion):
        h = ChallengeAssumptionsHandler()
        discussion.method_state["current_phase"] = "challenge"
        discussion.method_state["assumptions"] = ["Earth is round", "Gravity exists"]
        prompt = h.get_system_prompt(entity, discussion)
        assert "CHALLENGE" in prompt
        assert "Earth is round" in prompt
        assert "Gravity exists" in prompt

    def test_transition_message_lists_assumptions(self, discussion):
        h = ChallengeAssumptionsHandler()
        discussion.method_state["assumptions"] = ["A1", "A2"]
        msg = h.get_transition_message(discussion)
        assert "2 assumptions" in msg


class TestAssessAssumptionsHandler:
    def test_system_prompt_empty(self, entity, discussion):
        h = AssessAssumptionsHandler()
        discussion.method_state["current_phase"] = "assess"
        # Assess phase returns empty — moderator handles
        assert h.get_system_prompt(entity, discussion) == ""


class TestRefactoredMethodEquivalence:
    """Verify the refactored method produces identical behavior."""

    def test_init_state_matches(self):
        from consensus.methods.key_assumptions import KeyAssumptionsCheck
        method = KeyAssumptionsCheck()
        disc = Discussion(topic="test", discussion_method="key_assumptions")
        state = method.init_state(disc)
        assert state["current_phase"] == "surface"
        assert state["assumptions"] == []
        assert state["phase_round"] == 1

    def test_phases_match(self):
        from consensus.methods.key_assumptions import KeyAssumptionsCheck
        method = KeyAssumptionsCheck()
        assert len(method.default_phases) == 3
        assert method.default_phases[0].name == "surface"
        assert method.default_phases[1].name == "challenge"
        assert method.default_phases[2].name == "assess"

    def test_system_prompt_surface(self, entity):
        from consensus.methods.key_assumptions import KeyAssumptionsCheck
        method = KeyAssumptionsCheck()
        disc = Discussion(topic="test topic", discussion_method="key_assumptions")
        disc.method_state = method.init_state(disc)
        prompt = method.get_system_prompt(entity, disc)
        assert "ASSUMPTION SURFACING" in prompt
        assert entity.name in prompt
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_phases_key_assumptions.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement SurfaceAssumptionsHandler**

Create `consensus/methods/phases/surface_assumptions.py`:

```python
"""Surface Assumptions phase handler.

Participants identify key assumptions underlying the discussion topic.
Assumptions are extracted from responses and deduplicated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ..parsing import parse_numbered_list, word_overlap_similar

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class SurfaceAssumptionsHandler(PhaseHandler):
    """Phase: participants identify key assumptions."""

    phase = Phase(
        name="surface",
        display_name="Surface Assumptions",
        description=(
            "Identify the key assumptions underlying the question, "
            "the prevailing view, or any proposed answer.  These may "
            "be factual, causal, logical, or value-based assumptions."
        ),
        rounds=1,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {"assumptions": []}

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a Key Assumptions Check.\n"
            f"Topic: {discussion.topic}\n\n"
            "ASSUMPTION SURFACING PHASE\n\n"
            "Identify the key assumptions that underlie this topic, "
            "question, or any proposed answer.  Consider:\n\n"
            "- **Factual assumptions** — What facts are taken for granted?\n"
            "- **Causal assumptions** — What cause-effect relationships "
            "are assumed?\n"
            "- **Logical assumptions** — What logical connections are "
            "assumed to hold?\n"
            "- **Value assumptions** — What values or priorities are "
            "implicitly assumed?\n"
            "- **Scope assumptions** — What boundaries or constraints "
            "are assumed?\n\n"
            "Format each assumption as a numbered item:\n"
            "1. <assumption>\n"
            "2. <assumption>\n"
            "...\n\n"
            "Aim for 3-5 assumptions.  Include assumptions that seem "
            "obvious — those are often the most dangerous when wrong."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Identify 3-5 key "
            "assumptions underlying this topic.  Include both obvious "
            "and hidden assumptions."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has identified their key assumptions.  "
            "Briefly note which assumptions are new vs. overlapping "
            f"with previously surfaced ones.  Next: {next_speaker_name}."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        new_assumptions = parse_numbered_list(content)
        existing = discussion.method_state.get("assumptions", [])
        for a in new_assumptions:
            if not any(word_overlap_similar(a, e) for e in existing):
                existing.append(a)
        discussion.method_state["assumptions"] = existing
        return ProcessedResponse(
            display_content=content,
            extracted_data={"new_assumptions": new_assumptions},
        )

    def should_advance(self, discussion: Discussion) -> bool:
        state = discussion.method_state
        return (bool(state.get("assumptions"))
                and state.get("phase_round", 1) > 1)
```

- [ ] **Step 5: Implement ChallengeAssumptionsHandler**

Create `consensus/methods/phases/challenge_assumptions.py`:

```python
"""Challenge Assumptions phase handler.

Participants systematically challenge each surfaced assumption with
evidence, falsification conditions, consequences, and confidence ratings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class ChallengeAssumptionsHandler(PhaseHandler):
    """Phase: participants challenge surfaced assumptions."""

    phase = Phase(
        name="challenge",
        display_name="Challenge Assumptions",
        description=(
            "Systematically challenge each surfaced assumption.  "
            "For each, ask: What evidence supports it?  Under what "
            "conditions would it be false?  What are the consequences "
            "if it is wrong?"
        ),
        rounds=1,
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        assumptions = discussion.method_state.get("assumptions", [])
        assumption_list = "\n".join(
            f"  A{i+1}: {a}" for i, a in enumerate(assumptions)
        )
        return (
            f"You are {entity.name}, participating in a Key Assumptions Check.\n"
            f"Topic: {discussion.topic}\n\n"
            "ASSUMPTION CHALLENGE PHASE\n\n"
            f"The following assumptions have been surfaced:\n"
            f"{assumption_list}\n\n"
            "For EACH assumption, provide:\n"
            "1. **Supporting evidence** — What evidence supports this "
            "assumption being true?\n"
            "2. **Falsification conditions** — Under what circumstances "
            "would this assumption be FALSE?\n"
            "3. **Consequences if wrong** — If this assumption is wrong, "
            "how would it change the analysis or conclusion?\n"
            "4. **Confidence rating** — Rate your confidence that this "
            "assumption holds: HIGH / MEDIUM / LOW\n\n"
            "Be rigorous — even assumptions you believe are correct "
            "deserve honest scrutiny."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"It is your turn, {entity.name}.  Systematically challenge "
            "each surfaced assumption with evidence, falsification "
            "conditions, consequences, and a confidence rating."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has challenged the assumptions.  Note "
            "any assumptions rated LOW confidence and any surprising "
            f"findings.  Next: {next_speaker_name}."
        )

    def get_transition_message(self, discussion: Discussion) -> str:
        assumptions = discussion.method_state.get("assumptions", [])
        assumption_list = "\n".join(
            f"  **A{i+1}:** {a}" for i, a in enumerate(assumptions)
        )
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            f"{len(assumptions)} assumptions have been surfaced:\n"
            f"{assumption_list}\n\n"
            "Each participant will now systematically challenge these "
            "assumptions."
        )
```

- [ ] **Step 6: Implement AssessAssumptionsHandler**

Create `consensus/methods/phases/assess_assumptions.py`:

```python
"""Assess Assumptions phase handler.

Moderator assesses each assumption's status after challenges.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class AssessAssumptionsHandler(PhaseHandler):
    """Phase: moderator assesses assumption status."""

    phase = Phase(
        name="assess",
        display_name="Assessment",
        description=(
            "The moderator assesses each assumption's status: "
            "confirmed, unsupported, or contested.  Identifies which "
            "vulnerable assumptions most affect the overall analysis."
        ),
        rounds=1,
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return ""  # moderator handles via get_conclusion_prompt

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return ""

    def get_transition_message(self, discussion: Discussion) -> str:
        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "All challenges are in.  The moderator will now assess "
            "each assumption's status and identify which vulnerable "
            "assumptions most affect the analysis."
        )
```

- [ ] **Step 7: Refactor KeyAssumptionsCheck to use handlers**

Replace the body of `consensus/methods/key_assumptions.py` with handler assembly. Keep `get_conclusion_prompt` on the method (cross-phase logic):

```python
"""Key Assumptions Check — surface and challenge hidden assumptions.

Refactored to use composable PhaseHandler instances.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import DiscussionMethod
from .phases.surface_assumptions import SurfaceAssumptionsHandler
from .phases.challenge_assumptions import ChallengeAssumptionsHandler
from .phases.assess_assumptions import AssessAssumptionsHandler

if TYPE_CHECKING:
    from ..models import Discussion


class KeyAssumptionsCheck(DiscussionMethod):
    """Key Assumptions Check — expose and test hidden assumptions."""

    name = "key_assumptions"
    display_name = "Key Assumptions Check"
    description = (
        "Explicitly surface the assumptions underlying the question or "
        "prevailing view, then systematically challenge each one.  "
        "Prevents analysis from being built on unexamined foundations.  "
        "Effective as a standalone method or as a first phase before "
        "deeper analysis."
    )
    phase_handlers = (
        SurfaceAssumptionsHandler(),
        ChallengeAssumptionsHandler(),
        AssessAssumptionsHandler(),
    )

    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        state = discussion.method_state
        assumptions = state.get("assumptions", [])
        assumption_list = "\n".join(
            f"  A{i+1}: {a}" for i, a in enumerate(assumptions)
        )
        return (
            "The Key Assumptions Check is complete.\n\n"
            f"Assumptions examined:\n{assumption_list}\n\n"
            "Provide a comprehensive assessment:\n"
            "1. **Assumption status** — Classify each assumption as:\n"
            "   - CONFIRMED (strong evidence, high confidence)\n"
            "   - CONTESTED (mixed evidence, disagreement among participants)\n"
            "   - UNSUPPORTED (weak evidence, low confidence)\n"
            "   - REFUTED (strong counter-evidence)\n"
            "2. **Load-bearing assumptions** — Which assumptions, if wrong, "
            "would most change the overall analysis or conclusion?\n"
            "3. **Blind spots** — Were any important assumptions NOT surfaced "
            "that should have been?\n"
            "4. **Recommendations** — Given the assumption landscape, what "
            "should be investigated further before proceeding?  What "
            "conclusions should be held tentatively?\n\n"
            "Be specific and cite the challenges raised by participants."
        )
```

- [ ] **Step 8: Update phases/__init__.py exports**

```python
"""Reusable phase handlers for discussion methods."""

from .surface_assumptions import SurfaceAssumptionsHandler
from .challenge_assumptions import ChallengeAssumptionsHandler
from .assess_assumptions import AssessAssumptionsHandler

__all__ = [
    "SurfaceAssumptionsHandler",
    "ChallengeAssumptionsHandler",
    "AssessAssumptionsHandler",
]
```

- [ ] **Step 9: Run all tests**

```bash
pytest tests/test_phases_key_assumptions.py tests/test_method_delegation.py tests/test_methods.py -v
```

Expected: All pass — new handler tests, delegation tests, and existing method tests.

- [ ] **Step 10: Commit**

```bash
git add consensus/methods/phases/ consensus/methods/key_assumptions.py tests/test_phases_key_assumptions.py
git commit -m "feat(methods): refactor KeyAssumptionsCheck to composable phase handlers"
```

---

## Chunk 3: Refactor remaining methods (7 methods)

Each method follows the same pattern established in Task 4. The methods are listed in order of increasing complexity.

### Task 5: Refactor PremortemAnalysis

**Files:**
- Create: `consensus/methods/phases/frame_premortem.py`
- Create: `consensus/methods/phases/premortem_imagine.py`
- Create: `consensus/methods/phases/consolidate_premortem.py`
- Modify: `consensus/methods/premortem.py`
- Add tests to: `tests/test_phases_premortem.py`

Follow the same TDD pattern as Task 4:
- [ ] **Step 1:** Write tests for each handler + equivalence tests
- [ ] **Step 2:** Run tests, verify failure
- [ ] **Step 3:** Implement handlers (extract from current `if phase.name ==` blocks)
- [ ] **Step 4:** Refactor method to handler assembly, keep `get_conclusion_prompt` on method
- [ ] **Step 5:** Run all tests (new + existing)
- [ ] **Step 6:** Commit

### Task 6: Refactor AdversarialCollaboration

**Files:**
- Create: `consensus/methods/phases/state_positions.py`
- Create: `consensus/methods/phases/define_criteria.py`
- Create: `consensus/methods/phases/present_evidence.py`
- Create: `consensus/methods/phases/adjudicate.py`
- Modify: `consensus/methods/adversarial_collab.py`
- Add tests to: `tests/test_phases_adversarial_collab.py`

Same TDD pattern. Note: AdversarialCollab has `process_response` logic in `positions` and `criteria` phases that updates `method_state` — this moves into the handlers.

- [ ] Steps 1-6: same as Task 5

### Task 7: Refactor RedTeamBlueTeam

**Files:**
- Create: `consensus/methods/phases/construct.py`
- Create: `consensus/methods/phases/attack.py`
- Create: `consensus/methods/phases/revise.py`
- Create: `consensus/methods/phases/assess_red_team.py`
- Modify: `consensus/methods/red_team.py`
- Add tests to: `tests/test_phases_red_team.py`

Same TDD pattern. Note: Red Team uses `get_turn_order` override in construct/attack phases to exclude/include the red team member. This moves to the handler's `get_turn_order`.

- [ ] Steps 1-6: same as Task 5

### Task 8: Refactor ACH

**Files:**
- Create: `consensus/methods/phases/hypothesize.py`
- Create: `consensus/methods/phases/gather_evidence.py`
- Create: `consensus/methods/phases/evaluate_matrix.py`
- Create: `consensus/methods/phases/analyse_ach.py`
- Modify: `consensus/methods/ach.py`
- Add tests to: `tests/test_phases_ach.py`

Same TDD pattern. ACH has complex `process_response` in hypothesize (hypothesis extraction + dedup) and evidence (evidence parsing + ID assignment) phases. The `should_advance` for hypothesize checks both `hypotheses` and `phase_round`. Update `parsing.py` if ACH needs additional shared parsers beyond what exists.

- [ ] Steps 1-6: same as Task 5

### Task 9: Refactor DelphiMethod

**Files:**
- Create: `consensus/methods/phases/estimate.py`
- Create: `consensus/methods/phases/revise_delphi.py`
- Create: `consensus/methods/phases/synthesise_delphi.py`
- Modify: `consensus/methods/delphi.py`
- Add tests to: `tests/test_phases_delphi.py`

Same TDD pattern. Delphi uses `filter_context_message` for anonymization — this moves to the handler. Convergence checking in `should_advance` for the revise phase is the most complex advancement logic of all methods.

- [ ] Steps 1-6: same as Task 5

### Task 10: Refactor BeliefDiffusion

**Files:**
- Create: `consensus/methods/phases/frame_hypotheses.py`
- Create: `consensus/methods/phases/prior_beliefs.py`
- Create: `consensus/methods/phases/diffuse_beliefs.py`
- Create: `consensus/methods/phases/diagnose_beliefs.py`
- Modify: `consensus/methods/belief_diffusion.py`
- Add tests to: `tests/test_phases_belief_diffusion.py`

Same TDD pattern. Most complex method. Belief extraction from JSON blocks, bar chart rendering, convergence detection, and custom `on_round_complete` logic. The `on_round_complete` stays on the method (or moves to `diffuse` handler if it's phase-specific — check whether it only runs during diffuse). Also fix the `extract_hypotheses_from_framing` wiring gap noted during exploration.

- [ ] Steps 1-6: same as Task 5

### Task 11: Refactor VotingMethod

**Files:**
- Create: `consensus/methods/phases/deliberate.py`
- Create: `consensus/methods/phases/vote.py`
- Create: `consensus/methods/phases/tally.py`
- Modify: `consensus/methods/voting.py`
- Add tests to: `tests/test_phases_voting.py`

Same TDD pattern. Voting has the most complex `process_response` (motion extraction, vote parsing, duplicate-vote prevention). The vote phase's `should_advance` checks whether all participants have voted on all motions — non-trivial state logic.

- [ ] Steps 1-6: same as Task 5

---

## Chunk 4: Cleanup and integration verification

### Task 12: Update phases/__init__.py with all handlers

**Files:**
- Modify: `consensus/methods/phases/__init__.py`

- [ ] **Step 1:** Add all handler exports
- [ ] **Step 2:** Verify import works: `python -c "from consensus.methods.phases import *"`
- [ ] **Step 3:** Commit

### Task 13: Run full test suite

- [ ] **Step 1:** Run all tests

```bash
pytest tests/ -v --tb=short
```

Expected: All pass, including pre-existing tests in `test_methods.py` and `test_voting_method.py`.

- [ ] **Step 2:** Run a quick smoke test of the application

```bash
python -c "from consensus.methods import list_methods; print([m['name'] for m in list_methods()])"
```

Expected: All 9 method names listed.

- [ ] **Step 3:** Commit any final fixes if needed

### Task 14: Remove dead code

**Files:**
- Modify: `consensus/methods/base.py`

- [ ] **Step 1:** Remove `ProcessedResponse.phase_complete` field (dead code — never read by engine)

Note: only do this if no tests or methods reference it. Search first:

```bash
grep -r "phase_complete" consensus/ tests/
```

If only defined but never read, remove it. If referenced, leave it.

- [ ] **Step 2:** Run full test suite
- [ ] **Step 3:** Commit

```bash
git commit -m "refactor(methods): remove unused ProcessedResponse.phase_complete field"
```
