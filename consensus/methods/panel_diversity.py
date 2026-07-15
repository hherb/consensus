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
  * The moderator is excluded from the estimator panel *unless it
    participates* — i.e. its id is in ``base_turn_order`` (#48).  Before
    start, ``base_turn_order`` is empty, so the setup-time advisory and the
    conclusion-time disclosure may legitimately differ: participation is
    only known once the discussion starts.
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

    Excludes humans, experts, and AI entities with no resolved
    ``ai_config``.  Excludes the moderator UNLESS it participates — i.e.
    its id is present in ``discussion.base_turn_order`` (the estimator
    rotation).  A participating same-model moderator is the strongest form
    of estimator correlation, so it must count toward the panel (#48).
    ``base_turn_order`` is empty before the discussion starts, so the
    setup-time advisory is unchanged; it is populated once
    ``moderator_participates`` is known at start.
    """
    # base_turn_order is the full setup roster (not the phase-narrowed
    # order), so moderator_id is present iff the moderator takes estimate
    # turns. Delphi/Belief never narrow the moderator out of estimate phases,
    # so a moderator in it is a genuine correlated estimator (a future
    # independence-assuming method that did narrow it out would over-count —
    # a harmless, non-blocking false positive).
    mod_participates = discussion.moderator_id in discussion.base_turn_order
    models: list[str] = []
    for e in discussion.entities:
        if e.entity_type != EntityType.AI:
            continue
        if e.id == discussion.moderator_id and not mod_participates:
            continue
        if e.ai_config is None:
            continue
        models.append(e.ai_config.model)
    return models


def format_setup_warning(report: PanelDiversityReport) -> str | None:
    """Return a one-line setup warning, or ``None`` if not concerning.

    Phrased method-agnostically ("this method") because the method name is
    not available to this pure formatter.
    """
    if not report.is_concerning:
        return None
    n = report.panel_size
    model = report.dominant_model
    if report.is_unanimous:
        return (
            f"All {n} AI participants use the same model ('{model}'). "
            "This method assumes independent estimators — with one shared "
            "model, apparent convergence largely reflects the model agreeing "
            "with itself. Consider assigning different models or providers."
        )
    return (
        f"{report.dominant_count} of {n} AI participants share the model "
        f"'{model}'. This method assumes independent estimators; shared "
        "models correlate their errors and inflate apparent convergence. "
        "Consider diversifying models or providers."
    )


def format_conclusion_disclosure(report: PanelDiversityReport) -> str:
    """Return the panel-composition disclosure for a conclusion prompt.

    Empty when there are fewer than 2 estimators.  Always states the
    composition otherwise; appends a "discount the convergence" caveat only
    when the panel is concerning.
    """
    if report.panel_size < 2:
        return ""
    composition = ", ".join(
        f"{count}× {model}" for model, count in report.model_counts
    )
    base = f"Panel composition: {composition}."
    if not report.is_concerning:
        return base
    caveat = (
        f" Note: {report.dominant_count} of {report.panel_size} AI "
        "participants shared a model, so treat the convergence below as "
        "partly the model agreeing with itself and caveat the confidence "
        "accordingly."
    )
    return base + caveat
