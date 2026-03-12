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
