# Method Triage & Recommendation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-tier method recommendation system — a lightweight LLM-based recommender at setup time, plus a "Guided Triage" meta-method for collaborative method selection during discussions.

**Architecture:** `MethodRecommender` (stateless utility) handles LLM-based classification shared by both tiers. `TriageMethod` is a three-phase discussion method (Intake → Recommend → Confirm) using the existing PhaseHandler pattern. A `switch_discussion_method()` function in `app_discussion_flow.py` handles method transition after triage completes. Frontend gets a new answer-type selector and recommendation display in the setup UI.

**Tech Stack:** Python 3.10+, pytest, existing `consensus.methods` framework, vanilla JS frontend

**Spec:** `docs/superpowers/specs/2026-03-12-method-triage-design.md`

---

## Chunk 1: MethodRecommender — Shared Classification Engine

### Task 1: MethodRecommendation dataclass and MethodRecommender skeleton

**Files:**
- Create: `consensus/methods/recommender.py`
- Test: `tests/test_recommender.py`

- [ ] **Step 1: Write failing tests for MethodRecommendation dataclass**

Create `tests/test_recommender.py`:

```python
"""Tests for MethodRecommender — LLM-based method classification."""

import json
import pytest
from consensus.methods.recommender import MethodRecommendation


class TestMethodRecommendation:
    def test_creates_recommendation(self):
        rec = MethodRecommendation(
            method_name="ach",
            display_name="Analysis of Competing Hypotheses",
            confidence=0.85,
            reasoning="Topic involves testing claims against evidence.",
            fit_factors=["hypothesis testing", "evidence evaluation"],
        )
        assert rec.method_name == "ach"
        assert rec.confidence == 0.85
        assert len(rec.fit_factors) == 2

    def test_to_dict(self):
        rec = MethodRecommendation(
            method_name="delphi",
            display_name="Delphi Method",
            confidence=0.7,
            reasoning="Forecasting question.",
            fit_factors=["quantitative"],
        )
        d = rec.to_dict()
        assert d["method_name"] == "delphi"
        assert d["display_name"] == "Delphi Method"
        assert d["confidence"] == 0.7
        assert d["reasoning"] == "Forecasting question."
        assert d["fit_factors"] == ["quantitative"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_recommender.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Create recommender.py with dataclass and constants**

Create `consensus/methods/recommender.py`:

```python
"""MethodRecommender — LLM-based discussion method classification.

Stateless utility shared by the quick setup recommendation and the
Guided Triage meta-method. Sends the topic, answer type, and method
catalog to an LLM and parses ranked recommendations.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .parsing import extract_json_block

if TYPE_CHECKING:
    from ..ai_client import AIClient

logger = logging.getLogger(__name__)

# Methods excluded from recommendation candidates
_EXCLUDED_METHODS = {"triage", "open_discussion"}

# Answer type options presented to the user
ANSWER_TYPES = [
    "Explore a topic from multiple perspectives",
    "Make a decision between options",
    "Forecast or estimate something",
    "Identify risks or failure modes",
    "Test a hypothesis or claim",
    "Resolve a disagreement",
    "Something else / not sure",
]

_FALLBACK = None  # lazily initialized


@dataclass
class MethodRecommendation:
    """A single method recommendation with confidence and reasoning."""

    method_name: str
    display_name: str
    confidence: float
    reasoning: str
    fit_factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "method_name": self.method_name,
            "display_name": self.display_name,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "fit_factors": self.fit_factors,
        }


def _fallback_recommendation() -> list[MethodRecommendation]:
    """Return the default fallback recommendation."""
    return [MethodRecommendation(
        method_name="open_discussion",
        display_name="Open Discussion",
        confidence=0.5,
        reasoning="Could not reach AI for recommendation. Open Discussion is a safe default.",
        fit_factors=["fallback"],
    )]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_recommender.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/recommender.py tests/test_recommender.py
git commit -m "feat(triage): add MethodRecommendation dataclass and recommender skeleton"
```

### Task 2: Classification prompt builder

**Files:**
- Modify: `consensus/methods/recommender.py`
- Test: `tests/test_recommender.py`

- [ ] **Step 1: Write failing tests for prompt building**

Append to `tests/test_recommender.py`:

```python
from consensus.methods.recommender import (
    MethodRecommender, _EXCLUDED_METHODS, ANSWER_TYPES,
)


class TestMethodRecommender:
    def test_build_catalog_excludes_triage_and_open(self):
        catalog = [
            {"name": "ach", "display_name": "ACH", "description": "...", "phases": []},
            {"name": "triage", "display_name": "Guided Triage", "description": "...", "phases": []},
            {"name": "open_discussion", "display_name": "Open Discussion", "description": "...", "phases": []},
            {"name": "delphi", "display_name": "Delphi", "description": "...", "phases": []},
        ]
        recommender = MethodRecommender()
        filtered = recommender._filter_catalog(catalog)
        names = [m["name"] for m in filtered]
        assert "ach" in names
        assert "delphi" in names
        assert "triage" not in names
        assert "open_discussion" not in names

    def test_build_system_prompt_contains_methods(self):
        catalog = [
            {"name": "ach", "display_name": "ACH", "description": "Hypothesis testing", "phases": []},
        ]
        recommender = MethodRecommender()
        prompt = recommender._build_system_prompt(catalog)
        assert "ACH" in prompt
        assert "Hypothesis testing" in prompt
        assert "methodology expert" in prompt.lower()

    def test_build_user_prompt_contains_topic_and_type(self):
        recommender = MethodRecommender()
        prompt = recommender._build_user_prompt(
            topic="Will AI replace doctors?",
            answer_type="Forecast or estimate something",
            additional_context="",
        )
        assert "Will AI replace doctors?" in prompt
        assert "Forecast" in prompt

    def test_build_user_prompt_includes_additional_context(self):
        recommender = MethodRecommender()
        prompt = recommender._build_user_prompt(
            topic="test",
            answer_type="test",
            additional_context="The uncertainty is quantifiable.",
        )
        assert "quantifiable" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_recommender.py::TestMethodRecommender -v`
Expected: FAIL with `ImportError` (MethodRecommender not defined yet)

- [ ] **Step 3: Implement prompt builder methods**

Add to `consensus/methods/recommender.py`:

```python
_TAXONOMY = """\
Problem-type taxonomy — method strengths:
- Probabilistic / forecasting questions → Delphi Method, Belief State Diffusion
- Risk assessment / failure mode identification → Premortem Analysis
- Hypothesis testing / intelligence analysis → Analysis of Competing Hypotheses (ACH)
- Testing claim robustness / structural importance → Counterfactual Stress Testing
- Assumption examination / foundation checking → Key Assumptions Check
- Resolving disagreements / principled comparison → Adversarial Collaboration
- Stress-testing positions / adversarial analysis → Red Team / Blue Team
- Complex multi-faceted questions / decomposition → Recursive Decomposition
- Decision-making with formal group consensus → Participant Voting
- General exploration from multiple perspectives → Open Discussion (fallback only)
"""


class MethodRecommender:
    """Stateless LLM-based method classification engine."""

    def _filter_catalog(self, catalog: list[dict]) -> list[dict]:
        """Remove methods that should not be recommended."""
        return [m for m in catalog if m["name"] not in _EXCLUDED_METHODS]

    def _build_system_prompt(self, filtered_catalog: list[dict]) -> str:
        """Build the system prompt including method catalog."""
        methods_text = "\n".join(
            f"- **{m['display_name']}** (`{m['name']}`): {m['description']}"
            for m in filtered_catalog
        )
        return (
            "You are a discussion methodology expert. Given a topic and "
            "problem characteristics, recommend the most suitable discussion "
            "methods from the available catalog.\n\n"
            f"## Available Methods\n\n{methods_text}\n\n"
            f"## {_TAXONOMY}\n"
            "Respond with a JSON object (no markdown fences) matching this "
            "schema:\n"
            '{"recommendations": [\n'
            '  {"method_name": "<registry key>", '
            '"display_name": "<human name>", '
            '"confidence": <0.0-1.0>, '
            '"reasoning": "<1-2 sentences>", '
            '"fit_factors": ["<factor>", ...]}\n'
            "]}\n\n"
            "Return exactly the number of recommendations requested. "
            "Rank by confidence (highest first)."
        )

    def _build_user_prompt(
        self, topic: str, answer_type: str, additional_context: str = "",
    ) -> str:
        """Build the user prompt from topic and answer type."""
        parts = [
            f"**Topic:** {topic}",
            f"**Answer type:** {answer_type}",
        ]
        if additional_context:
            parts.append(
                f"**Additional context:**\n{additional_context}"
            )
        parts.append(
            "\nRecommend the top 3 most suitable discussion methods "
            "for this topic."
        )
        return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_recommender.py::TestMethodRecommender -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/recommender.py tests/test_recommender.py
git commit -m "feat(triage): add classification prompt builder for MethodRecommender"
```

### Task 3: Response parsing and the recommend() method

**Files:**
- Modify: `consensus/methods/recommender.py`
- Test: `tests/test_recommender.py`

- [ ] **Step 1: Write failing tests for response parsing**

Append to `tests/test_recommender.py`:

```python
class TestParseRecommendations:
    def test_parses_valid_json(self):
        recommender = MethodRecommender()
        raw = json.dumps({"recommendations": [
            {"method_name": "ach", "display_name": "ACH",
             "confidence": 0.9, "reasoning": "Good fit.",
             "fit_factors": ["hypothesis"]},
            {"method_name": "delphi", "display_name": "Delphi",
             "confidence": 0.6, "reasoning": "Possible.",
             "fit_factors": ["forecasting"]},
        ]})
        results = recommender._parse_response(raw, num_recommendations=3)
        assert len(results) == 2
        assert results[0].method_name == "ach"
        assert results[0].confidence == 0.9

    def test_parses_json_in_code_fence(self):
        recommender = MethodRecommender()
        raw = '```json\n{"recommendations": [{"method_name": "delphi", "display_name": "Delphi", "confidence": 0.8, "reasoning": "r", "fit_factors": []}]}\n```'
        results = recommender._parse_response(raw, num_recommendations=3)
        assert len(results) == 1
        assert results[0].method_name == "delphi"

    def test_returns_fallback_on_invalid_json(self):
        recommender = MethodRecommender()
        results = recommender._parse_response("not json at all", num_recommendations=3)
        assert len(results) == 1
        assert results[0].method_name == "open_discussion"
        assert results[0].fit_factors == ["fallback"]

    def test_returns_fallback_on_missing_recommendations_key(self):
        recommender = MethodRecommender()
        results = recommender._parse_response('{"other": []}', num_recommendations=3)
        assert len(results) == 1
        assert results[0].method_name == "open_discussion"

    def test_clamps_confidence(self):
        recommender = MethodRecommender()
        raw = json.dumps({"recommendations": [
            {"method_name": "ach", "display_name": "ACH",
             "confidence": 1.5, "reasoning": "r", "fit_factors": []},
        ]})
        results = recommender._parse_response(raw, num_recommendations=3)
        assert results[0].confidence == 1.0
```

Also add an async integration test with mocked AIClient:

```python
from unittest.mock import AsyncMock, MagicMock
from consensus.methods.recommender import _fallback_recommendation


class TestRecommendAsync:
    @pytest.mark.asyncio
    async def test_recommend_returns_parsed_results(self):
        recommender = MethodRecommender()
        mock_response = MagicMock()
        mock_response.content = json.dumps({"recommendations": [
            {"method_name": "ach", "display_name": "ACH",
             "confidence": 0.9, "reasoning": "Good.", "fit_factors": ["test"]},
        ]})
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=mock_response)
        mock_client.close = AsyncMock()

        catalog = [{"name": "ach", "display_name": "ACH", "description": "d", "phases": []}]
        provider = {"model": "test-model"}

        results = await recommender.recommend(
            "test topic", "test type", catalog, mock_client, provider,
        )
        assert len(results) == 1
        assert results[0].method_name == "ach"
        mock_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_recommend_returns_fallback_on_error(self):
        recommender = MethodRecommender()
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(side_effect=Exception("API down"))
        mock_client.close = AsyncMock()

        catalog = [{"name": "ach", "display_name": "ACH", "description": "d", "phases": []}]
        provider = {"model": "test-model"}

        results = await recommender.recommend(
            "test topic", "test type", catalog, mock_client, provider,
        )
        assert len(results) == 1
        assert results[0].method_name == "open_discussion"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_recommender.py::TestParseRecommendations tests/test_recommender.py::TestRecommendAsync -v`
Expected: FAIL

- [ ] **Step 3: Implement _parse_response and recommend()**

Add to `MethodRecommender` in `consensus/methods/recommender.py`:

```python
    def _parse_response(
        self, content: str, num_recommendations: int,
    ) -> list[MethodRecommendation]:
        """Parse LLM response into MethodRecommendation objects."""
        # Try direct JSON parse first, then code-fence extraction
        data = None
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            data = extract_json_block(content)

        if not isinstance(data, dict) or "recommendations" not in data:
            logger.warning("Failed to parse recommendation response")
            return _fallback_recommendation()

        recs = []
        for item in data["recommendations"][:num_recommendations]:
            try:
                recs.append(MethodRecommendation(
                    method_name=item["method_name"],
                    display_name=item.get("display_name", item["method_name"]),
                    confidence=max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
                    reasoning=item.get("reasoning", ""),
                    fit_factors=item.get("fit_factors", []),
                ))
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("Skipping malformed recommendation: %s", e)

        return recs if recs else _fallback_recommendation()

    async def recommend(
        self,
        topic: str,
        answer_type: str,
        method_catalog: list[dict],
        ai_client: AIClient,
        provider: dict,
        num_recommendations: int = 3,
        additional_context: str = "",
    ) -> list[MethodRecommendation]:
        """Classify topic and return ranked method recommendations.

        The ``ai_client`` must already be constructed with the correct
        base_url and api_key (``AIClient(base_url=..., api_key=...)``).
        The ``provider`` dict only needs ``"model"`` for the completion call.

        Args:
            topic: Discussion topic text.
            answer_type: One of ANSWER_TYPES.
            method_catalog: Output of list_methods().
            ai_client: Pre-configured AIClient instance.
            provider: Dict with at least "model" key.
            num_recommendations: How many to return (default 3).
            additional_context: Extra context from guided triage intake.

        Returns:
            List of MethodRecommendation sorted by confidence (desc).
        """
        filtered = self._filter_catalog(method_catalog)
        if not filtered:
            return _fallback_recommendation()

        system_prompt = self._build_system_prompt(filtered)
        user_prompt = self._build_user_prompt(
            topic, answer_type, additional_context,
        )

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            resp = await ai_client.complete(
                messages=messages,
                model=provider["model"],
                temperature=0.3,
            )
            return self._parse_response(resp.content, num_recommendations)
        except Exception:
            logger.exception("MethodRecommender.recommend() failed")
            return _fallback_recommendation()
```

- [ ] **Step 4: Run all recommender tests**

Run: `python -m pytest tests/test_recommender.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/recommender.py tests/test_recommender.py
git commit -m "feat(triage): add response parsing and recommend() to MethodRecommender"
```

---

## Chunk 2: Triage Method — Phase Handlers

### Task 4: TriageIntakeHandler

**Files:**
- Create: `consensus/methods/phases/triage_intake.py`
- Test: `tests/test_triage_handlers.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_triage_handlers.py`:

```python
"""Tests for Guided Triage phase handlers."""

import pytest
from consensus.methods.base import ProcessedResponse
from consensus.models import Discussion, Entity, EntityType


@pytest.fixture
def human_entity():
    return Entity(name="Alice", entity_type=EntityType.HUMAN, id=1)


@pytest.fixture
def ai_entity():
    return Entity(name="Bot", entity_type=EntityType.AI, id=2)


@pytest.fixture
def moderator_entity():
    return Entity(name="Moderator", entity_type=EntityType.AI, id=3)


class TestTriageIntakeHandler:
    def test_system_prompt_contains_topic(self, human_entity):
        from consensus.methods.phases.triage_intake import TriageIntakeHandler
        handler = TriageIntakeHandler()
        disc = Discussion(topic="Should we expand into Asia?",
                          discussion_method="triage")
        disc.method_state = {"current_phase": "intake"}
        prompt = handler.get_system_prompt(human_entity, disc)
        assert "Should we expand into Asia?" in prompt

    def test_turn_prompt_asks_structured_questions(self, human_entity):
        from consensus.methods.phases.triage_intake import TriageIntakeHandler
        handler = TriageIntakeHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        prompt = handler.get_turn_prompt(human_entity, disc)
        assert "type of question" in prompt.lower() or "kind of question" in prompt.lower()
        assert "decision context" in prompt.lower() or "context" in prompt.lower()
        assert "uncertainty" in prompt.lower()

    def test_turn_order_excludes_ai_entities(self, human_entity, ai_entity, moderator_entity):
        from consensus.methods.phases.triage_intake import TriageIntakeHandler
        handler = TriageIntakeHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.entities = [human_entity, ai_entity, moderator_entity]
        disc.moderator_id = 3
        order = handler.get_turn_order([1, 2], disc)
        assert 1 in order
        assert 2 not in order

    def test_turn_order_empty_when_no_humans(self, ai_entity, moderator_entity):
        from consensus.methods.phases.triage_intake import TriageIntakeHandler
        handler = TriageIntakeHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.entities = [ai_entity, moderator_entity]
        disc.moderator_id = 3
        order = handler.get_turn_order([2], disc)
        assert order == []

    def test_should_advance_skips_when_no_humans(self, ai_entity, moderator_entity):
        from consensus.methods.phases.triage_intake import TriageIntakeHandler
        handler = TriageIntakeHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.entities = [ai_entity, moderator_entity]
        disc.moderator_id = 3
        disc.method_state = {"current_phase": "intake", "phase_round": 1}
        assert handler.should_advance(disc) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_triage_handlers.py::TestTriageIntakeHandler -v`
Expected: FAIL

- [ ] **Step 3: Implement TriageIntakeHandler**

Create `consensus/methods/phases/triage_intake.py`:

```python
"""Intake phase handler for Guided Triage.

Moderator asks human participants structured questions about the
problem type, decision context, and uncertainty structure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity, EntityType


class TriageIntakeHandler(PhaseHandler):
    """Phase 1: Moderator interviews human participants."""

    phase = Phase(
        name="intake",
        display_name="Problem Intake",
        description=(
            "The moderator asks structured questions to understand "
            "the nature of the problem before recommending a method."
        ),
        rounds=1,
        allow_tools=False,
    )

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Only human participants respond during intake."""
        from ...models import EntityType
        return [
            eid for eid in entity_ids
            if any(
                e.id == eid and e.entity_type == EntityType.HUMAN
                for e in discussion.entities
            )
        ]

    def should_advance(self, discussion: Discussion) -> bool:
        """Advance after 1 round, or immediately if no humans."""
        from ...models import EntityType
        has_humans = any(
            e.entity_type == EntityType.HUMAN
            and e.id != discussion.moderator_id
            for e in discussion.entities
        )
        if not has_humans:
            return True
        return super().should_advance(discussion)

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            f"You are {entity.name}, participating in a structured "
            f"methodology selection process.\n"
            f"Topic: {discussion.topic}\n\n"
            "The moderator will ask you questions to understand the "
            "nature of this problem so the best discussion method "
            "can be selected."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            f"Please answer the following questions about the topic "
            f"\"{discussion.topic}\" to help select the best discussion "
            f"method:\n\n"
            "1. **Type of question:** What kind of question is this? "
            "(e.g., exploring perspectives, making a decision, "
            "forecasting, identifying risks, testing a hypothesis, "
            "resolving a disagreement)\n\n"
            "2. **Decision context:** What is the context? "
            "(e.g., academic exploration, real-world decision with "
            "stakes, risk assessment, policy evaluation)\n\n"
            "3. **Uncertainty structure:** What does the uncertainty "
            "look like? (e.g., known unknowns, expert disagreement, "
            "quantifiable uncertainty, poorly defined problem space)\n\n"
            "4. **Preliminary conclusion:** Is there an existing "
            "conclusion or position to examine? (optional — say "
            "'none' if not applicable)"
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"{speaker_name} has provided their problem characterization. "
            "Briefly note the key points about the problem type, context, "
            f"and uncertainty structure. Next: {next_speaker_name}."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_triage_handlers.py::TestTriageIntakeHandler -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/triage_intake.py tests/test_triage_handlers.py
git commit -m "feat(triage): add TriageIntakeHandler for problem intake phase"
```

### Task 5: TriageRecommendHandler

**Files:**
- Create: `consensus/methods/phases/triage_recommend.py`
- Test: `tests/test_triage_handlers.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_triage_handlers.py`:

```python
class TestTriageRecommendHandler:
    def test_turn_order_is_moderator_only(self, moderator_entity):
        from consensus.methods.phases.triage_recommend import TriageRecommendHandler
        handler = TriageRecommendHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.moderator_id = 3
        order = handler.get_turn_order([1, 2, 3], disc)
        assert order == [3]

    def test_system_prompt_references_methodology(self, moderator_entity):
        from consensus.methods.phases.triage_recommend import TriageRecommendHandler
        handler = TriageRecommendHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.method_state = {"current_phase": "recommend"}
        prompt = handler.get_system_prompt(moderator_entity, disc)
        assert "method" in prompt.lower()

    def test_turn_prompt_instructs_synthesis(self, moderator_entity):
        from consensus.methods.phases.triage_recommend import TriageRecommendHandler
        handler = TriageRecommendHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        prompt = handler.get_turn_prompt(moderator_entity, disc)
        assert "synthesize" in prompt.lower() or "characteriz" in prompt.lower()

    def test_init_state_keys(self):
        from consensus.methods.phases.triage_recommend import TriageRecommendHandler
        handler = TriageRecommendHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        state = handler.init_state(disc)
        assert "recommendations" in state
        assert "recommended_method" in state
        assert "chosen_method" in state
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_triage_handlers.py::TestTriageRecommendHandler -v`
Expected: FAIL

- [ ] **Step 3: Implement TriageRecommendHandler**

Create `consensus/methods/phases/triage_recommend.py`:

```python
"""Recommend phase handler for Guided Triage.

Moderator-only phase: synthesizes intake responses, calls
MethodRecommender programmatically, and presents recommendations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class TriageRecommendHandler(PhaseHandler):
    """Phase 2: Moderator synthesizes and recommends methods."""

    phase = Phase(
        name="recommend",
        display_name="Method Recommendation",
        description=(
            "The moderator synthesizes the intake responses and "
            "recommends discussion methods for the group to consider."
        ),
        rounds=1,
        allow_tools=False,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "recommendations": [],
            "recommended_method": None,
            "chosen_method": None,
        }

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Moderator only."""
        return [discussion.moderator_id]

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        return (
            "You are the moderator conducting a methodology selection "
            "process. Based on the participants' answers about the "
            "problem type, decision context, and uncertainty structure, "
            "synthesize their input into a clear problem characterization.\n\n"
            "Focus on: what kind of problem this is, what the key "
            "uncertainties are, and what kind of analytical approach "
            "would be most productive."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        return (
            "Review the intake responses from participants above.\n\n"
            "Synthesize their answers into a clear characterization of:\n"
            "1. The type of problem or question\n"
            "2. The decision context and stakes\n"
            "3. The structure of uncertainty\n"
            "4. Whether there is a preliminary conclusion to test\n\n"
            "Based on this characterization, explain what kind of "
            "analytical method would be most productive and why."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Store the moderator's synthesis for the recommender call.

        Note: The actual MethodRecommender.recommend() call happens in
        app_discussion_flow.py after this response is processed, because
        process_response is synchronous and recommend() is async.
        The moderator's characterization is stored in method_state for
        the async call to use as additional_context.
        """
        state = discussion.method_state
        state["moderator_characterization"] = content
        return ProcessedResponse(display_content=content)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_triage_handlers.py::TestTriageRecommendHandler -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/triage_recommend.py tests/test_triage_handlers.py
git commit -m "feat(triage): add TriageRecommendHandler for method recommendation phase"
```

### Task 6: TriageConfirmHandler

**Files:**
- Create: `consensus/methods/phases/triage_confirm.py`
- Test: `tests/test_triage_handlers.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_triage_handlers.py`:

```python
class TestTriageConfirmHandler:
    def test_turn_prompt_shows_recommendations(self, ai_entity):
        from consensus.methods.phases.triage_confirm import TriageConfirmHandler
        handler = TriageConfirmHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.method_state = {
            "current_phase": "confirm",
            "recommendations": [
                {"method_name": "ach", "display_name": "ACH",
                 "confidence": 0.9, "reasoning": "Good fit.",
                 "fit_factors": ["hypothesis"]},
            ],
            "recommended_method": "ach",
        }
        prompt = handler.get_turn_prompt(ai_entity, disc)
        assert "ACH" in prompt
        assert "ach" in prompt.lower()

    def test_process_response_extracts_chosen_method(self, moderator_entity):
        from consensus.methods.phases.triage_confirm import TriageConfirmHandler
        handler = TriageConfirmHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.moderator_id = 3
        disc.method_state = {
            "current_phase": "confirm",
            "recommendations": [
                {"method_name": "ach", "display_name": "ACH",
                 "confidence": 0.9, "reasoning": "r", "fit_factors": []},
            ],
            "recommended_method": "ach",
            "chosen_method": None,
        }
        content = "Based on the group's agreement, I recommend we proceed with `ach` — Analysis of Competing Hypotheses."
        handler.process_response(content, moderator_entity, disc)
        assert disc.method_state["chosen_method"] == "ach"

    def test_process_response_falls_back_to_recommended(self, moderator_entity):
        from consensus.methods.phases.triage_confirm import TriageConfirmHandler
        handler = TriageConfirmHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.moderator_id = 3
        disc.method_state = {
            "current_phase": "confirm",
            "recommendations": [],
            "recommended_method": "delphi",
            "chosen_method": None,
        }
        content = "Let's proceed with the recommended method."
        handler.process_response(content, moderator_entity, disc)
        assert disc.method_state["chosen_method"] == "delphi"

    def test_process_response_ignores_non_moderator(self, ai_entity):
        from consensus.methods.phases.triage_confirm import TriageConfirmHandler
        handler = TriageConfirmHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.moderator_id = 3
        disc.method_state = {
            "current_phase": "confirm",
            "recommendations": [
                {"method_name": "ach", "display_name": "ACH",
                 "confidence": 0.9, "reasoning": "r", "fit_factors": []},
            ],
            "recommended_method": "ach",
            "chosen_method": None,
        }
        handler.process_response("I agree with ach", ai_entity, disc)
        assert disc.method_state["chosen_method"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_triage_handlers.py::TestTriageConfirmHandler -v`
Expected: FAIL

- [ ] **Step 3: Implement TriageConfirmHandler**

Create `consensus/methods/phases/triage_confirm.py`:

```python
"""Confirm phase handler for Guided Triage.

All participants review the recommended methods and confirm or
suggest alternatives. The moderator makes the final selection.
"""

from __future__ import annotations

import re
import logging
from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class TriageConfirmHandler(PhaseHandler):
    """Phase 3: Group confirms method selection."""

    phase = Phase(
        name="confirm",
        display_name="Method Confirmation",
        description=(
            "All participants review the recommended methods and "
            "confirm or suggest alternatives."
        ),
        rounds=1,
        allow_tools=False,
    )

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        recs = state.get("recommendations", [])
        rec_text = "\n".join(
            f"- **{r['display_name']}** (`{r['method_name']}`) — "
            f"confidence {r['confidence']:.0%}: {r['reasoning']}"
            for r in recs
        ) if recs else "(no recommendations available)"

        recommended = state.get("recommended_method", "unknown")

        return (
            f"You are {entity.name} participating in a methodology "
            f"selection process.\n"
            f"Topic: {discussion.topic}\n\n"
            f"The moderator recommends: **{recommended}**\n\n"
            f"All recommendations:\n{rec_text}\n\n"
            "Review the recommendation. You may agree, object with "
            "reasoning, or suggest an alternative method."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        state = discussion.method_state
        recs = state.get("recommendations", [])
        rec_text = "\n".join(
            f"  {i+1}. **{r['display_name']}** (`{r['method_name']}`) — "
            f"{r['reasoning']}"
            for i, r in enumerate(recs)
        ) if recs else "  (no recommendations)"
        recommended = state.get("recommended_method", "unknown")

        if entity.id == discussion.moderator_id:
            return (
                "Review the participants' feedback on the method "
                "recommendation. Make the final selection.\n\n"
                "If a human participant explicitly requested a "
                "different method, honor that request.\n\n"
                "State your final choice clearly using the method's "
                f"registry name (e.g., `{recommended}`)."
            )

        return (
            f"The recommended discussion methods are:\n{rec_text}\n\n"
            f"Top recommendation: `{recommended}`\n\n"
            "Do you agree with this recommendation, or would you "
            "prefer a different method? If you disagree, explain why "
            "and suggest an alternative."
        )

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        """Extract chosen method from moderator's final selection."""
        state = discussion.method_state

        # Only the moderator's response sets the chosen method
        if entity.id != discussion.moderator_id:
            return ProcessedResponse(display_content=content)

        # Try to extract a backtick-quoted method name
        recs = state.get("recommendations", [])
        valid_names = {r["method_name"] for r in recs}

        chosen = None
        # Pattern: `method_name` in backticks
        backtick_matches = re.findall(r'`(\w+)`', content)
        for match in backtick_matches:
            if match in valid_names:
                chosen = match
                break

        # Fallback: check if any recommended method name appears in text
        if not chosen:
            for name in valid_names:
                if name in content.lower():
                    chosen = name
                    break

        # Final fallback: use the recommended method
        if not chosen:
            chosen = state.get("recommended_method")
            logger.info("Could not parse chosen method, falling back to recommended: %s", chosen)

        state["chosen_method"] = chosen
        return ProcessedResponse(display_content=content)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_triage_handlers.py::TestTriageConfirmHandler -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/triage_confirm.py tests/test_triage_handlers.py
git commit -m "feat(triage): add TriageConfirmHandler for method confirmation phase"
```

### Task 7: Export triage handlers from phases/__init__.py

**Files:**
- Modify: `consensus/methods/phases/__init__.py`

- [ ] **Step 1: Read current phases/__init__.py**

Read `consensus/methods/phases/__init__.py` to see the current exports.

- [ ] **Step 2: Add triage handler exports**

Add to the imports and `__all__` in `consensus/methods/phases/__init__.py`:

```python
from .triage_intake import TriageIntakeHandler
from .triage_recommend import TriageRecommendHandler
from .triage_confirm import TriageConfirmHandler
```

And add to `__all__`:
```python
"TriageIntakeHandler", "TriageRecommendHandler", "TriageConfirmHandler",
```

- [ ] **Step 3: Verify imports work**

Run: `python -c "from consensus.methods.phases import TriageIntakeHandler, TriageRecommendHandler, TriageConfirmHandler; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add consensus/methods/phases/__init__.py
git commit -m "feat(triage): export triage handlers from phases package"
```

---

## Chunk 3: TriageMethod and Method Transition

### Task 8: TriageMethod class

**Files:**
- Create: `consensus/methods/triage.py`
- Test: `tests/test_triage_handlers.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_triage_handlers.py`:

```python
class TestTriageMethod:
    def test_has_three_phases(self):
        from consensus.methods.triage import TriageMethod
        method = TriageMethod()
        assert len(method.default_phases) == 3
        names = [p.name for p in method.default_phases]
        assert names == ["intake", "recommend", "confirm"]

    def test_init_state_has_required_keys(self):
        from consensus.methods.triage import TriageMethod
        method = TriageMethod()
        disc = Discussion(topic="test", discussion_method="triage")
        state = method.init_state(disc)
        assert state["current_phase"] == "intake"
        assert "recommendations" in state
        assert "recommended_method" in state
        assert "chosen_method" in state

    def test_to_dict_metadata(self):
        from consensus.methods.triage import TriageMethod
        method = TriageMethod()
        d = method.to_dict()
        assert d["name"] == "triage"
        assert d["display_name"] == "Guided Triage"
        assert len(d["phases"]) == 3

    def test_registered_in_registry(self):
        from consensus.methods import get_method
        method = get_method("triage")
        assert method.name == "triage"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_triage_handlers.py::TestTriageMethod -v`
Expected: FAIL

- [ ] **Step 3: Implement TriageMethod**

Create `consensus/methods/triage.py`:

```python
"""Guided Triage — collaborative method selection meta-method.

Phases:
  1. INTAKE    — Moderator interviews human participants
  2. RECOMMEND — Moderator synthesizes and recommends methods
  3. CONFIRM   — Group confirms or adjusts the selection
"""

from __future__ import annotations

from .base import DiscussionMethod
from .phases.triage_intake import TriageIntakeHandler
from .phases.triage_recommend import TriageRecommendHandler
from .phases.triage_confirm import TriageConfirmHandler


class TriageMethod(DiscussionMethod):
    """Guided Triage — collaborative method selection."""

    name = "triage"
    display_name = "Guided Triage"
    description = (
        "Collaborative method selection: the moderator interviews "
        "participants about the problem type, decision context, and "
        "uncertainty structure, then recommends a discussion method "
        "for the group to confirm or adjust."
    )
    phase_handlers = (
        TriageIntakeHandler(),
        TriageRecommendHandler(),
        TriageConfirmHandler(),
    )
```

- [ ] **Step 4: Register in method registry**

Modify `consensus/methods/__init__.py`:

Add import:
```python
from .triage import TriageMethod
```

Add to `_METHODS` dict:
```python
    "triage": TriageMethod,
```

Add to `__all__`:
```python
    "TriageMethod",
```

Reset the metadata cache (since the registry is static, adding triage before any call is fine, but set `_METHODS_METADATA = None` is already the default).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_triage_handlers.py::TestTriageMethod -v`
Expected: PASS

- [ ] **Step 6: Run existing registry test to check no regressions**

Run: `python -m pytest tests/test_methods.py -v`
Expected: PASS (may need to update expected method count)

- [ ] **Step 7: Commit**

```bash
git add consensus/methods/triage.py consensus/methods/__init__.py
git commit -m "feat(triage): add TriageMethod and register in method registry"
```

### Task 9: switch_discussion_method() and triage integration in complete_turn

**Files:**
- Modify: `consensus/app_discussion_flow.py`
- Test: `tests/test_triage_handlers.py`

- [ ] **Step 1: Write failing tests for switch_discussion_method**

Append to `tests/test_triage_handlers.py`:

```python
from unittest.mock import MagicMock


class TestSwitchDiscussionMethod:
    def _make_discussion(self):
        disc = Discussion(topic="test", discussion_method="triage")
        disc.id = 1
        disc.is_active = True
        disc.status = "active"
        disc.method_state = {
            "current_phase": "confirm",
            "chosen_method": "ach",
        }
        disc.moderator_id = 3
        mod = Entity(name="Mod", entity_type=EntityType.AI, id=3)
        disc.entities = [mod]
        return disc

    def test_switches_method_and_reinitializes_state(self):
        from consensus.app_discussion_flow import switch_discussion_method
        disc = self._make_discussion()
        db = MagicMock()
        result = switch_discussion_method(disc, db, "ach")
        assert disc.discussion_method == "ach"
        assert disc.method_state.get("current_phase") == "hypothesize"
        assert result["name"] == "ach"

    def test_rejects_switching_to_triage(self):
        from consensus.app_discussion_flow import switch_discussion_method
        disc = self._make_discussion()
        db = MagicMock()
        result = switch_discussion_method(disc, db, "triage")
        assert "error" in result

    def test_rejects_unknown_method(self):
        from consensus.app_discussion_flow import switch_discussion_method
        disc = self._make_discussion()
        db = MagicMock()
        result = switch_discussion_method(disc, db, "nonexistent")
        assert "error" in result

    def test_persists_to_db(self):
        from consensus.app_discussion_flow import switch_discussion_method
        disc = self._make_discussion()
        db = MagicMock()
        switch_discussion_method(disc, db, "ach")
        db.update_discussion.assert_called()
        db.add_message.assert_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_triage_handlers.py::TestSwitchDiscussionMethod -v`
Expected: FAIL

- [ ] **Step 3: Implement switch_discussion_method**

Add to `consensus/app_discussion_flow.py`, after the imports:

```python
from .methods import get_method, serialize_method_state
```

(Note: `get_active_method` and `serialize_method_state` are already imported — just add `get_method` to the existing import.)

Add this function before `complete_turn`:

```python
def switch_discussion_method(
    discussion: Discussion, db: Database, method_name: str,
) -> dict:
    """Switch the discussion to a new method (used by triage).

    Reinitializes method_state, persists to DB, and adds a system
    message announcing the transition. Returns the new method's
    metadata dict, or an error dict.
    """
    if method_name == "triage":
        return {"error": "Cannot switch to triage method"}

    try:
        method = get_method(method_name)
    except KeyError:
        return {"error": f"Unknown method: {method_name!r}"}

    discussion.discussion_method = method_name
    discussion.method_state = method.init_state(discussion)

    if discussion.id:
        db.update_discussion(
            discussion.id,
            discussion_method=method_name,
            method_state=serialize_method_state(discussion.method_state),
        )

    # System message announcing the transition
    first_phase = method.default_phases[0] if method.default_phases else None
    phase_info = f" Beginning {first_phase.display_name} phase." if first_phase else ""
    transition_text = (
        f"**Discussion method set to {method.display_name}.**{phase_info}"
    )
    mod = discussion.moderator
    if mod and discussion.id:
        msg = Message(
            entity_id=mod.id, entity_name=mod.name,
            content=transition_text, role=MessageRole.SYSTEM,
        )
        discussion.messages.append(msg)
        db.add_message(
            discussion.id, mod.id, transition_text, "system",
            turn_number=discussion.turn_number,
        )

    return method.to_dict()
```

- [ ] **Step 4: Hook triage completion into complete_turn**

In `consensus/app_discussion_flow.py`, in the `complete_turn` function, find the block at lines 343-355 where `method_complete` is returned (when `advance_phase` returns `None`). Replace:

```python
            else:
                # All phases exhausted — conclude
                if discussion.id:
                    db.update_discussion(
                        discussion.id,
                        method_state=serialize_method_state(discussion.method_state),
                    )
                return {
                    "method_complete": True,
                    "turn_number": discussion.turn_number,
                    "current_round": discussion.current_round,
                    "state": get_state_fn(),
                }
```

With:

```python
            else:
                # All phases exhausted
                if discussion.id:
                    db.update_discussion(
                        discussion.id,
                        method_state=serialize_method_state(discussion.method_state),
                    )
                # Triage special case: switch to chosen method
                chosen = discussion.method_state.get("chosen_method")
                if (discussion.discussion_method == "triage"
                        and chosen):
                    switch_result = switch_discussion_method(
                        discussion, db, chosen)
                    if "error" not in switch_result:
                        # Reorder turns for the new method
                        new_method = get_active_method(discussion)
                        if new_method:
                            new_order = new_method.get_turn_order(
                                list(discussion.turn_order), discussion)
                            if new_order != list(discussion.turn_order):
                                discussion.turn_order = new_order
                                discussion.current_turn_index = 0
                        return {
                            "method_switched": True,
                            "new_method": switch_result,
                            "turn_number": discussion.turn_number,
                            "current_round": discussion.current_round,
                            "state": get_state_fn(),
                        }
                return {
                    "method_complete": True,
                    "turn_number": discussion.turn_number,
                    "current_round": discussion.current_round,
                    "state": get_state_fn(),
                }
```

- [ ] **Step 5: Run all triage tests**

Run: `python -m pytest tests/test_triage_handlers.py -v`
Expected: PASS

- [ ] **Step 6: Run existing flow tests to check no regressions**

Run: `python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add consensus/app_discussion_flow.py tests/test_triage_handlers.py
git commit -m "feat(triage): add switch_discussion_method() and triage completion hook"
```

---

## Chunk 4: App Integration, API, and Desktop Bridge

### Task 10: ConsensusApp.recommend_method()

**Files:**
- Modify: `consensus/app_discussion_setup.py`
- Modify: `consensus/app.py`

- [ ] **Step 1: Add recommend_method to app_discussion_setup.py**

Add to `consensus/app_discussion_setup.py` (after `set_discussion_method`):

```python
async def recommend_method(
    topic: str,
    answer_type: str,
    ai_client,
    provider: dict,
) -> list[dict]:
    """Get LLM-based method recommendations for a topic.

    Returns a list of recommendation dicts.
    """
    from .methods import list_methods
    from .methods.recommender import MethodRecommender

    recommender = MethodRecommender()
    catalog = list_methods()
    recommendations = await recommender.recommend(
        topic=topic,
        answer_type=answer_type,
        method_catalog=catalog,
        ai_client=ai_client,
        provider=provider,
    )
    return [r.to_dict() for r in recommendations]
```

- [ ] **Step 2: Add wrapper to ConsensusApp in app.py**

Find the `list_discussion_methods` method in `consensus/app.py` (around line 597) and add after it:

```python
    async def recommend_method(self, topic: str, answer_type: str) -> dict:
        """Get LLM-based method recommendations for a topic."""
        mod = self.discussion.moderator
        if not mod or not mod.ai_config:
            return {"error": "No AI moderator configured for recommendations"}
        api_key = self._resolve_key_for_moderator(
            mod.ai_config.provider_id, mod.ai_config.api_key_env,
        )
        from .ai_client import AIClient
        ai_client = AIClient(
            base_url=mod.ai_config.base_url, api_key=api_key,
        )
        provider = {"model": mod.ai_config.model}
        try:
            recs = await app_discussion_setup.recommend_method(
                topic, answer_type, ai_client, provider,
            )
            return {"recommendations": recs}
        except Exception as e:
            logger.exception("Method recommendation failed")
            return {"error": str(e)}
        finally:
            await ai_client.close()
```

- [ ] **Step 3: Verify import works**

Run: `python -c "from consensus.app import ConsensusApp; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add consensus/app_discussion_setup.py consensus/app.py
git commit -m "feat(triage): add recommend_method to ConsensusApp"
```

### Task 11: Server route and desktop bridge

**Files:**
- Modify: `consensus/server.py`
- Modify: `consensus/desktop.py`

- [ ] **Step 1: Add server route**

In `consensus/server.py`, find the `handlers` dict inside `handle_api` (around line 339). Add the new handler:

```python
            "recommend_method": lambda: app.recommend_method(
                data.get("topic", ""), data.get("answer_type", "")),
```

Note: since `recommend_method` is async, the existing handler dispatch in `handle_api` already handles coroutines (it uses `await` on the result if it's a coroutine).

- [ ] **Step 2: Add desktop bridge method**

In `consensus/desktop.py`, find `list_discussion_methods` (around line 194) and add after it:

```python
    def recommend_method(self, topic: str, answer_type: str) -> dict:
        """Get LLM-based method recommendations."""
        return self._run_async(self.app.recommend_method(topic, answer_type))
```

- [ ] **Step 3: Add API method to api.js**

In `consensus/static/api.js`, find `listDiscussionMethods` (around line 189) and add after:

```javascript
    async recommendMethod(topic, answerType) { return await this._post('recommend_method', { topic, answer_type: answerType }); }
```

Also add the same to the DesktopAPI class (the pywebview bridge version):

```javascript
    async recommendMethod(topic, answerType) { return await window.pywebview.api.recommend_method(topic, answerType); }
```

- [ ] **Step 4: Verify server starts without errors**

Run: `python -c "from consensus.server import create_app; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add consensus/server.py consensus/desktop.py consensus/static/api.js
git commit -m "feat(triage): add recommend_method API route and bridge"
```

---

## Chunk 5: Frontend UI Enhancement

### Task 12: Answer type selector and recommendation display

**Files:**
- Modify: `consensus/static/setup.js`
- Modify: `consensus/static/index.html`
- Modify: `consensus/static/style.css`

- [ ] **Step 1: Add answer type HTML to index.html**

In `consensus/static/index.html`, find the discussion method dropdown area (around the `#discussion-method` select element). Add after the method description element:

```html
<div id="method-recommendation-section" class="form-group">
  <label>What kind of answer are you looking for?</label>
  <div id="answer-type-options" class="answer-type-group">
    <label><input type="radio" name="answer_type" value="explore"> Explore from multiple perspectives</label>
    <label><input type="radio" name="answer_type" value="decide"> Make a decision between options</label>
    <label><input type="radio" name="answer_type" value="forecast"> Forecast or estimate something</label>
    <label><input type="radio" name="answer_type" value="risks"> Identify risks or failure modes</label>
    <label><input type="radio" name="answer_type" value="hypothesis"> Test a hypothesis or claim</label>
    <label><input type="radio" name="answer_type" value="disagreement"> Resolve a disagreement</label>
    <label><input type="radio" name="answer_type" value="other"> Something else / not sure</label>
  </div>
  <button id="suggest-method-btn" class="btn btn-secondary" disabled>Suggest Method</button>
  <div id="method-recommendations" class="recommendations-panel" style="display:none;"></div>
</div>
```

- [ ] **Step 2: Add CSS for recommendations**

Add to `consensus/static/style.css`:

```css
.answer-type-group {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  margin: 0.5rem 0;
}
.answer-type-group label {
  cursor: pointer;
  padding: 0.25rem 0;
}
.recommendations-panel {
  margin-top: 0.75rem;
  padding: 0.75rem;
  border: 1px solid var(--border);
  border-radius: var(--radius, 6px);
  background: var(--bg-secondary, #f8f8f8);
}
.recommendation-item {
  padding: 0.5rem;
  margin-bottom: 0.5rem;
  border-radius: var(--radius, 4px);
  cursor: pointer;
  transition: background 0.15s;
}
.recommendation-item:hover {
  background: var(--bg-hover, #e8e8e8);
}
.recommendation-item.top-pick {
  border-left: 3px solid var(--accent, #4a9eff);
  font-weight: 500;
}
.recommendation-item .confidence {
  font-size: 0.85em;
  opacity: 0.7;
}
.recommendation-item .reasoning {
  font-size: 0.9em;
  margin-top: 0.25rem;
}
```

- [ ] **Step 3: Add recommendation logic to setup.js**

Add to `consensus/static/setup.js`:

```javascript
// Answer type mapping (radio values → full text for API)
const ANSWER_TYPE_MAP = {
    explore: "Explore a topic from multiple perspectives",
    decide: "Make a decision between options",
    forecast: "Forecast or estimate something",
    risks: "Identify risks or failure modes",
    hypothesis: "Test a hypothesis or claim",
    disagreement: "Resolve a disagreement",
    other: "Something else / not sure",
};

export function initMethodRecommendation() {
    const btn = document.getElementById('suggest-method-btn');
    const radios = document.querySelectorAll('input[name="answer_type"]');
    if (!btn) return;

    radios.forEach(r => r.addEventListener('change', () => {
        const topic = document.getElementById('topic')?.value?.trim();
        btn.disabled = !topic;
    }));

    btn.addEventListener('click', requestRecommendation);
}

async function requestRecommendation() {
    const topic = document.getElementById('topic')?.value?.trim();
    const selected = document.querySelector('input[name="answer_type"]:checked');
    if (!topic || !selected) return;

    const btn = document.getElementById('suggest-method-btn');
    const panel = document.getElementById('method-recommendations');
    btn.disabled = true;
    btn.textContent = 'Analyzing...';

    const answerType = ANSWER_TYPE_MAP[selected.value] || selected.value;

    try {
        const result = await api.recommendMethod(topic, answerType);
        if (result?.error) {
            panel.innerHTML = `<p class="error">${result.error}</p>`;
        } else if (result?.recommendations) {
            renderRecommendations(result.recommendations);
        }
    } catch (e) {
        panel.innerHTML = '<p class="error">Recommendation failed.</p>';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Suggest Method';
        panel.style.display = '';
    }
}

function renderRecommendations(recs) {
    const panel = document.getElementById('method-recommendations');
    if (!panel || !recs.length) return;

    panel.innerHTML = recs.map((r, i) => `
        <div class="recommendation-item ${i === 0 ? 'top-pick' : ''}"
             data-method="${r.method_name}">
            <strong>${r.display_name}</strong>
            <span class="confidence">${Math.round(r.confidence * 100)}% match</span>
            <div class="reasoning">${r.reasoning}</div>
        </div>
    `).join('');

    panel.querySelectorAll('.recommendation-item').forEach(el => {
        el.addEventListener('click', () => {
            const select = document.getElementById('discussion-method');
            if (select) {
                select.value = el.dataset.method;
                select.dispatchEvent(new Event('change'));
            }
        });
    });
}
```

- [ ] **Step 4: Wire up initMethodRecommendation in the setup tab initialization**

Find where `loadDiscussionMethods()` is called (in the setup tab init code) and add after it:

```javascript
initMethodRecommendation();
```

- [ ] **Step 5: Test manually in the browser**

Start the web server: `python -m consensus --web --debug`
Open the browser, navigate to setup tab, verify:
1. Answer type radio buttons appear below the method dropdown
2. Selecting a radio + having a topic enables the "Suggest Method" button
3. Clicking the button shows loading state and then recommendations
4. Clicking a recommendation selects it in the dropdown

- [ ] **Step 6: Commit**

```bash
git add consensus/static/index.html consensus/static/setup.js consensus/static/style.css
git commit -m "feat(triage): add answer type selector and recommendation UI"
```

---

## Chunk 6: Triage Recommend Phase — Async Recommender Integration

### Task 13: Wire MethodRecommender into the triage flow

**Files:**
- Modify: `consensus/app_discussion_flow.py`

The `TriageRecommendHandler.process_response()` is synchronous but `MethodRecommender.recommend()` is async. The spec says the recommender call happens in `app_discussion_flow.py`. The cleanest integration point is in `generate_ai_turn()`, after `process_response()` runs for the recommend phase.

- [ ] **Step 1: Add post-response recommender hook**

The `generate_ai_turn` function needs a `key_resolver` parameter so the triage recommender can resolve API keys properly. This mirrors how `Moderator` receives a `key_resolver` callback.

In `consensus/app_discussion_flow.py`, update the `generate_ai_turn` signature to accept an optional `key_resolver`:

```python
async def generate_ai_turn(
    discussion: Discussion, moderator: Moderator, db: Database,
    pricing: PricingCache, key_resolver=None,
) -> dict:
```

Then, after the method `process_response` block (around lines 121-131), add:

```python
        # Triage recommend phase: run async MethodRecommender
        if (method and discussion.discussion_method == "triage"
                and discussion.method_state.get("current_phase") == "recommend"
                and key_resolver):
            await _run_triage_recommender(discussion, current, key_resolver)
```

Update the caller in `consensus/app.py` to pass the key resolver when calling `generate_ai_turn`:

```python
result = await app_discussion_flow.generate_ai_turn(
    self.discussion, self.moderator, self.db,
    self.db.pricing, key_resolver=self._resolve_key_for_moderator,
)
```

Then add this helper function to `app_discussion_flow.py`:

```python
async def _run_triage_recommender(
    discussion: Discussion, moderator_entity: Entity, key_resolver,
) -> None:
    """Call MethodRecommender after the triage moderator's synthesis turn."""
    from .ai_client import AIClient
    from .methods import list_methods
    from .methods.recommender import MethodRecommender

    state = discussion.method_state
    characterization = state.get("moderator_characterization", "")
    if not moderator_entity.ai_config:
        return

    api_key = key_resolver(
        moderator_entity.ai_config.provider_id,
        moderator_entity.ai_config.api_key_env,
    )
    ai_client = AIClient(
        base_url=moderator_entity.ai_config.base_url,
        api_key=api_key,
    )
    provider = {"model": moderator_entity.ai_config.model}

    recommender = MethodRecommender()
    try:
        recs = await recommender.recommend(
            topic=discussion.topic,
            answer_type="",
            method_catalog=list_methods(),
            ai_client=ai_client,
            provider=provider,
            additional_context=characterization,
        )
        state["recommendations"] = [r.to_dict() for r in recs]
        state["recommended_method"] = recs[0].method_name if recs else None
    except Exception:
        logger.exception("Triage recommender call failed")
        state["recommendations"] = []
        state["recommended_method"] = "open_discussion"
    finally:
        await ai_client.close()
```

- [ ] **Step 2: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add consensus/app_discussion_flow.py
git commit -m "feat(triage): wire async MethodRecommender into triage recommend phase"
```

---

## Chunk 7: Final Integration Testing

### Task 14: Integration tests and method count update

**Files:**
- Modify: `tests/test_methods.py` (update expected method count)
- Test: run full suite

- [ ] **Step 1: Update method registry test**

Read `tests/test_methods.py` and update any test that asserts a specific method count (should now be 12, not 11).

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 3: Commit if any test file changes were needed**

```bash
git add tests/test_methods.py
git commit -m "test: update method count for triage registration"
```

### Task 15: Final verification

- [ ] **Step 1: Verify clean import**

Run: `python -c "from consensus.methods import list_methods; methods = list_methods(); print(f'{len(methods)} methods'); assert any(m['name'] == 'triage' for m in methods)"`
Expected: `12 methods`

- [ ] **Step 2: Run full test suite one final time**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 3: Final commit if anything needed**

```bash
git add -A
git commit -m "feat(triage): method triage & recommendation system complete"
```
