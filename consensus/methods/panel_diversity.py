"""Same-model panel detection for independence-assuming methods (#29).

Delphi and Belief State Diffusion assume *independent* estimators whose
errors partially cancel.  When every AI participant is a persona of the
same base model, their estimates are strongly correlated and apparent
convergence largely measures the model agreeing with itself.  This module
detects that situation from the discussion roster.

Pure functions only — no I/O, no engine-state coupling beyond reading
``discussion.entities``.

Documented simplifications:
  * Exact-model grouping only.  Family-level correlation (e.g. ``gpt-4o``
    vs ``gpt-4o-mini``, or one model served under different provider name
    strings) is not detected; near-duplicate names undercount the dominant
    group.  Family grouping is a deferred refinement.
  * The moderator is always excluded from the estimator panel, even when it
    participates — the correlation risk of interest is among the participant
    estimators, and excluding the moderator keeps the setup-time and
    conclusion-time computations identical.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import EntityType

if TYPE_CHECKING:
    from ..models import Discussion

#: The dominant model must cover strictly more than this fraction of the
#: estimator panel for the panel to be flagged (i.e. "more than half").
DIVERSITY_WARN_FRACTION: float = 0.5


@dataclass(frozen=True)
class PanelDiversityReport:
    """Summary of model diversity across an estimator panel."""

    panel_size: int
    distinct_models: int
    dominant_model: str
    dominant_count: int
    is_concerning: bool
    is_unanimous: bool
    #: (model, count) pairs, sorted by count desc then model name asc.
    model_counts: tuple[tuple[str, int], ...]


def analyze_panel_diversity(
    models: list[str], *, fraction: float = DIVERSITY_WARN_FRACTION,
) -> PanelDiversityReport:
    """Analyse model diversity over a list of estimator model names.

    Args:
        models: The exact model-name string of each AI estimator.
        fraction: Dominance threshold; the panel is concerning when the
            most-common model's share strictly exceeds this fraction.

    Returns:
        A :class:`PanelDiversityReport` describing the panel.
    """
    panel_size = len(models)
    counter = Counter(models)
    # Deterministic order: count desc, then model name asc.
    ordered = tuple(
        sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    if panel_size == 0:
        return PanelDiversityReport(
            panel_size=0, distinct_models=0, dominant_model="",
            dominant_count=0, is_concerning=False, is_unanimous=False,
            model_counts=(),
        )
    dominant_model, dominant_count = ordered[0]
    is_concerning = panel_size >= 2 and dominant_count > fraction * panel_size
    is_unanimous = panel_size >= 2 and dominant_count == panel_size
    return PanelDiversityReport(
        panel_size=panel_size,
        distinct_models=len(counter),
        dominant_model=dominant_model,
        dominant_count=dominant_count,
        is_concerning=is_concerning,
        is_unanimous=is_unanimous,
        model_counts=ordered,
    )


def estimator_models(discussion: "Discussion") -> list[str]:
    """Return the model strings of a discussion's AI estimators.

    Excludes the moderator, humans, experts, and AI entities with no
    resolved ``ai_config``.
    """
    models: list[str] = []
    for e in discussion.entities:
        if e.entity_type != EntityType.AI:
            continue
        if e.id == discussion.moderator_id:
            continue
        if e.ai_config is None:
            continue
        models.append(e.ai_config.model)
    return models
