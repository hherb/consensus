# Same-Model Panel Warning (#29) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Warn when a Delphi / Belief State Diffusion panel is dominated by one model (inline at setup + toast at start) and disclose the panel's model composition in each method's conclusion prompt.

**Architecture:** A pure `panel_diversity` module analyses the estimator panel's models; a declarative `assumes_independent_panel` flag on `DiscussionMethod` opts the two forecasting methods in. `get_state()` surfaces a `panel_advisory` consumed by both the setup banner and the start toast; the two methods prepend a composition disclosure to their conclusion prompts via a base-class helper.

**Tech Stack:** Python 3 (dataclasses, `collections.Counter`), pytest, vanilla ES-module JS frontend.

## Global Constraints

- **`uv` only** — run tests with `uv run pytest`; never `pip`.
- **No magic numbers** — thresholds are named constants (`DIVERSITY_WARN_FRACTION = 0.5`).
- **Docstrings + type hints mandatory** on every function/method.
- **Pure functions preferred**, packaged as a reusable module; unit-tested.
- **Files under ~500 lines.**
- **CSS: relative units + custom properties only** — no hard-coded pixel sizes.
- **Detection rule:** estimator panel = AI entities excluding the moderator (humans/experts/`ai_config is None` excluded). Group by exact `ai_config.model` string. Concerning ⟺ `panel_size >= 2` and `dominant_count > DIVERSITY_WARN_FRACTION * panel_size`. Unanimous ⟺ `panel_size >= 2` and `dominant_count == panel_size`.
- **Documented simplifications** (keep in the module docstring): exact-model grouping only (no family-level detection); moderator always excluded even when it participates.

---

## File Structure

**New:**
- `consensus/methods/panel_diversity.py` — pure analysis: `PanelDiversityReport`, `estimator_models`, `analyze_panel_diversity`, `format_setup_warning`, `format_conclusion_disclosure`, `DIVERSITY_WARN_FRACTION`.
- `tests/test_panel_diversity.py` — unit tests for the module.

**Modified:**
- `consensus/methods/base.py` — `assumes_independent_panel` class attr + `panel_composition_disclosure` helper.
- `consensus/methods/delphi.py` — opt in + prepend disclosure.
- `consensus/methods/belief_diffusion.py` — opt in + prepend disclosure (normal path only).
- `consensus/app.py` — compute `panel_advisory` in `get_state()`.
- `consensus/static/index.html` — advisory container element.
- `consensus/static/setup.js` — render the advisory.
- `consensus/static/discussion-actions.js` — toast the advisory at start.
- `consensus/static/style.css` — `.method-advisory` style.

---

### Task 1: Pure analysis core (`analyze_panel_diversity` + report)

**Files:**
- Create: `consensus/methods/panel_diversity.py`
- Test: `tests/test_panel_diversity.py`

**Interfaces:**
- Produces: `DIVERSITY_WARN_FRACTION: float`; `PanelDiversityReport` (frozen dataclass with fields `panel_size: int`, `distinct_models: int`, `dominant_model: str`, `dominant_count: int`, `is_concerning: bool`, `is_unanimous: bool`, `model_counts: tuple[tuple[str, int], ...]`); `analyze_panel_diversity(models: list[str], *, fraction: float = DIVERSITY_WARN_FRACTION) -> PanelDiversityReport`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_panel_diversity.py`:

```python
"""Tests for consensus.methods.panel_diversity — same-model panel detection (#29)."""

import pytest

from consensus.methods.panel_diversity import (
    DIVERSITY_WARN_FRACTION,
    PanelDiversityReport,
    analyze_panel_diversity,
)


class TestAnalyzePanelDiversity:
    def test_unanimous_two(self):
        r = analyze_panel_diversity(["gpt-4o", "gpt-4o"])
        assert r.panel_size == 2
        assert r.dominant_model == "gpt-4o"
        assert r.dominant_count == 2
        assert r.distinct_models == 1
        assert r.is_concerning is True
        assert r.is_unanimous is True

    def test_majority_two_of_three(self):
        r = analyze_panel_diversity(["gpt-4o", "gpt-4o", "claude"])
        assert r.dominant_count == 2
        assert r.is_concerning is True   # 2 > 0.5 * 3
        assert r.is_unanimous is False

    def test_half_of_four_not_concerning(self):
        r = analyze_panel_diversity(["a", "a", "b", "b"])
        assert r.dominant_count == 2
        assert r.is_concerning is False  # 2 > 0.5 * 4 is False
        assert r.is_unanimous is False

    def test_three_of_four_concerning(self):
        r = analyze_panel_diversity(["a", "a", "a", "b"])
        assert r.dominant_count == 3
        assert r.is_concerning is True   # 3 > 0.5 * 4
        assert r.is_unanimous is False

    def test_all_distinct_not_concerning(self):
        r = analyze_panel_diversity(["a", "b", "c"])
        assert r.dominant_count == 1
        assert r.distinct_models == 3
        assert r.is_concerning is False

    def test_single_estimator(self):
        r = analyze_panel_diversity(["a"])
        assert r.panel_size == 1
        assert r.is_concerning is False
        assert r.is_unanimous is False

    def test_empty(self):
        r = analyze_panel_diversity([])
        assert r.panel_size == 0
        assert r.dominant_model == ""
        assert r.dominant_count == 0
        assert r.is_concerning is False
        assert r.model_counts == ()

    def test_model_counts_sorted_desc_then_name(self):
        r = analyze_panel_diversity(["b", "a", "a"])
        assert r.model_counts == (("a", 2), ("b", 1))
        assert r.dominant_model == "a"

    def test_report_is_frozen(self):
        r = analyze_panel_diversity(["a"])
        with pytest.raises(Exception):
            r.panel_size = 5  # frozen dataclass

    def test_fraction_constant(self):
        assert DIVERSITY_WARN_FRACTION == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_panel_diversity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'consensus.methods.panel_diversity'`.

- [ ] **Step 3: Write minimal implementation**

Create `consensus/methods/panel_diversity.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_panel_diversity.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/panel_diversity.py tests/test_panel_diversity.py
git commit -m "feat(methods): pure same-model panel analysis (#29)"
```

---

### Task 2: `estimator_models` adapter

**Files:**
- Modify: `consensus/methods/panel_diversity.py`
- Test: `tests/test_panel_diversity.py`

**Interfaces:**
- Consumes: `consensus.models.Discussion`, `Entity`, `AIConfig`, `EntityType`.
- Produces: `estimator_models(discussion: Discussion) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_panel_diversity.py`:

```python
from consensus.methods.panel_diversity import estimator_models
from consensus.models import (
    AIConfig, Discussion, Entity, EntityType,
)


def _ai(name, eid, model):
    return Entity(name=name, entity_type=EntityType.AI, id=eid,
                  ai_config=AIConfig(model=model))


class TestEstimatorModels:
    def test_excludes_moderator(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("Mod", 100, "gpt-4o"),
                      _ai("A", 1, "claude"),
                      _ai("B", 2, "claude")],
            moderator_id=100,
        )
        assert estimator_models(disc) == ["claude", "claude"]

    def test_excludes_humans(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("A", 1, "gpt-4o"),
                      Entity(name="Human", entity_type=EntityType.HUMAN, id=2)],
            moderator_id=None,
        )
        assert estimator_models(disc) == ["gpt-4o"]

    def test_excludes_experts(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("A", 1, "gpt-4o"),
                      Entity(name="Exp", entity_type=EntityType.EXPERT, id=2,
                             ai_config=AIConfig(model="gpt-4o"))],
            moderator_id=None,
        )
        assert estimator_models(disc) == ["gpt-4o"]

    def test_skips_ai_without_config(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("A", 1, "gpt-4o"),
                      Entity(name="B", entity_type=EntityType.AI, id=2)],
            moderator_id=None,
        )
        assert estimator_models(disc) == ["gpt-4o"]

    def test_empty_when_no_ai(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[Entity(name="H", entity_type=EntityType.HUMAN, id=1)],
            moderator_id=None,
        )
        assert estimator_models(disc) == []
```

> Note: confirm `EntityType.HUMAN` is the correct enum member name before running (grep `class EntityType` in `consensus/models.py`); adjust if it differs.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_panel_diversity.py::TestEstimatorModels -q`
Expected: FAIL — `ImportError: cannot import name 'estimator_models'`.

- [ ] **Step 3: Write minimal implementation**

Add to `consensus/methods/panel_diversity.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_panel_diversity.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/panel_diversity.py tests/test_panel_diversity.py
git commit -m "feat(methods): estimator_models roster adapter (#29)"
```

---

### Task 3: Message formatters

**Files:**
- Modify: `consensus/methods/panel_diversity.py`
- Test: `tests/test_panel_diversity.py`

**Interfaces:**
- Consumes: `PanelDiversityReport` (Task 1).
- Produces: `format_setup_warning(report: PanelDiversityReport) -> str | None`; `format_conclusion_disclosure(report: PanelDiversityReport) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_panel_diversity.py`:

```python
from consensus.methods.panel_diversity import (
    format_setup_warning,
    format_conclusion_disclosure,
)


class TestFormatSetupWarning:
    def test_none_when_not_concerning(self):
        r = analyze_panel_diversity(["a", "b", "c"])
        assert format_setup_warning(r) is None

    def test_unanimous_wording(self):
        r = analyze_panel_diversity(["gpt-4o", "gpt-4o"])
        msg = format_setup_warning(r)
        assert msg is not None
        assert "All 2" in msg
        assert "gpt-4o" in msg
        assert "independent estimators" in msg

    def test_majority_wording(self):
        r = analyze_panel_diversity(["gpt-4o", "gpt-4o", "claude"])
        msg = format_setup_warning(r)
        assert msg is not None
        assert "2 of 3" in msg
        assert "gpt-4o" in msg


class TestFormatConclusionDisclosure:
    def test_empty_below_two(self):
        assert format_conclusion_disclosure(analyze_panel_diversity(["a"])) == ""
        assert format_conclusion_disclosure(analyze_panel_diversity([])) == ""

    def test_diverse_composition_no_caveat(self):
        r = analyze_panel_diversity(["a", "b", "c"])
        text = format_conclusion_disclosure(r)
        assert "Panel composition" in text
        assert "caveat" not in text.lower()

    def test_concerning_includes_caveat(self):
        r = analyze_panel_diversity(["gpt-4o", "gpt-4o", "claude"])
        text = format_conclusion_disclosure(r)
        assert "Panel composition" in text
        assert "2 of 3" in text
        assert "caveat" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_panel_diversity.py::TestFormatSetupWarning -q`
Expected: FAIL — `ImportError: cannot import name 'format_setup_warning'`.

- [ ] **Step 3: Write minimal implementation**

Add to `consensus/methods/panel_diversity.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_panel_diversity.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/panel_diversity.py tests/test_panel_diversity.py
git commit -m "feat(methods): setup + conclusion message formatters (#29)"
```

---

### Task 4: Base-class flag + disclosure helper

**Files:**
- Modify: `consensus/methods/base.py` (add attr near `name`/`display_name` ~line 111; add method to the class body)
- Test: `tests/test_panel_diversity.py`

**Interfaces:**
- Consumes: `estimator_models`, `analyze_panel_diversity`, `format_conclusion_disclosure` (Tasks 1–3).
- Produces: `DiscussionMethod.assumes_independent_panel: bool = False`; `DiscussionMethod.panel_composition_disclosure(self, discussion) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_panel_diversity.py`:

```python
from consensus.methods.base import DiscussionMethod


class _IndepMethod(DiscussionMethod):
    name = "indep_test"
    display_name = "Independent Test"
    assumes_independent_panel = True
    phase_handlers = ()


class _PlainMethod(DiscussionMethod):
    name = "plain_test"
    display_name = "Plain Test"
    phase_handlers = ()


class TestPanelCompositionDisclosure:
    def test_default_flag_false(self):
        assert DiscussionMethod.assumes_independent_panel is False
        assert _PlainMethod().assumes_independent_panel is False

    def test_plain_method_returns_empty(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("A", 1, "gpt-4o"), _ai("B", 2, "gpt-4o")],
            moderator_id=None,
        )
        assert _PlainMethod().panel_composition_disclosure(disc) == ""

    def test_indep_method_discloses_concerning(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("A", 1, "gpt-4o"), _ai("B", 2, "gpt-4o")],
            moderator_id=None,
        )
        text = _IndepMethod().panel_composition_disclosure(disc)
        assert "Panel composition" in text
        assert "caveat" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_panel_diversity.py::TestPanelCompositionDisclosure -q`
Expected: FAIL — `AttributeError: ... has no attribute 'assumes_independent_panel'` (or the method).

- [ ] **Step 3: Write minimal implementation**

In `consensus/methods/base.py`, add the class attribute just after `display_name` (~line 112):

```python
    #: When True, the method's validity depends on estimators being
    #: independent (Delphi, Belief State Diffusion).  Drives the same-model
    #: panel warning and conclusion-time composition disclosure (#29).
    assumes_independent_panel: bool = False
```

Add this method to the `DiscussionMethod` class body (e.g. just below `requires_structured_output`):

```python
    def panel_composition_disclosure(self, discussion: "Discussion") -> str:
        """Return the model-composition disclosure for the conclusion prompt.

        Empty string unless ``assumes_independent_panel`` is set.  Used by
        independence-assuming methods to caveat convergence when the panel
        shares a model (#29).
        """
        if not self.assumes_independent_panel:
            return ""
        from .panel_diversity import (
            analyze_panel_diversity, estimator_models,
            format_conclusion_disclosure,
        )
        report = analyze_panel_diversity(estimator_models(discussion))
        return format_conclusion_disclosure(report)
```

> If `base.py` does not already have `Discussion` importable for the type hint, it is under `if TYPE_CHECKING:` — confirm with `grep -n "TYPE_CHECKING\|import Discussion" consensus/methods/base.py`; the string annotation `"Discussion"` needs no runtime import.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_panel_diversity.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/base.py tests/test_panel_diversity.py
git commit -m "feat(methods): assumes_independent_panel flag + disclosure helper (#29)"
```

---

### Task 5: Opt Delphi + Belief Diffusion in

**Files:**
- Modify: `consensus/methods/delphi.py` (~line 33 class attrs; `get_conclusion_prompt` ~line 64)
- Modify: `consensus/methods/belief_diffusion.py` (~line 41 class attrs; `get_conclusion_prompt` final return ~line 121)
- Test: `tests/test_panel_diversity.py`

**Interfaces:**
- Consumes: `panel_composition_disclosure` (Task 4).
- Produces: `DelphiMethod.assumes_independent_panel == True`, `BeliefDiffusion.assumes_independent_panel == True`; both prepend the disclosure to their (normal) conclusion prompts.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_panel_diversity.py`:

```python
from consensus.methods.delphi import DelphiMethod
from consensus.methods.belief_diffusion import BeliefDiffusion


def _delphi_disc(models):
    entities = [_ai("Mod", 100, "gpt-4o")]
    for i, m in enumerate(models):
        entities.append(_ai(f"E{i}", i + 1, m))
    disc = Discussion(
        id=1, topic="p?", entities=entities, moderator_id=100,
        discussion_method="delphi",
    )
    disc.method_state = {"estimates": []}
    return disc


class TestMethodOptIn:
    def test_flags_set(self):
        assert DelphiMethod().assumes_independent_panel is True
        assert BeliefDiffusion().assumes_independent_panel is True

    def test_delphi_conclusion_discloses_same_model(self):
        disc = _delphi_disc(["claude", "claude", "claude"])
        prompt = DelphiMethod().get_conclusion_prompt(disc)
        assert "Panel composition" in prompt
        assert "caveat" in prompt.lower()
        # Original body preserved:
        assert "Delphi Method process is complete" in prompt

    def test_delphi_conclusion_diverse_no_caveat(self):
        disc = _delphi_disc(["a", "b", "c"])
        prompt = DelphiMethod().get_conclusion_prompt(disc)
        assert "caveat" not in prompt.lower()
        assert "Delphi Method process is complete" in prompt

    def test_belief_conclusion_discloses_same_model(self):
        entities = [_ai("Mod", 100, "gpt-4o"),
                    _ai("A", 1, "claude"), _ai("B", 2, "claude")]
        disc = Discussion(
            id=1, topic="p?", entities=entities, moderator_id=100,
            discussion_method="belief_diffusion",
        )
        disc.method_state = {
            "hypotheses": ["H1", "H2"], "beliefs": [], "diffuse_round": 0,
        }
        prompt = BeliefDiffusion().get_conclusion_prompt(disc)
        assert "Panel composition" in prompt
        assert "Belief State Diffusion process is complete" in prompt
```

> Note: the Belief test's `method_state` must have non-empty `hypotheses` so `get_conclusion_prompt` takes the normal (non-abort) path. `build_trajectory_summary` should tolerate an empty `beliefs` list — if it raises, add the minimal keys it reads (inspect `consensus/methods/phases/_belief_helpers.py`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_panel_diversity.py::TestMethodOptIn -q`
Expected: FAIL — flags are `False` / disclosure absent.

- [ ] **Step 3: Write minimal implementation**

In `consensus/methods/delphi.py`, add to the class attributes (after `description`/`phase_handlers`, before the methods):

```python
    assumes_independent_panel = True
```

Change the start of `get_conclusion_prompt` to capture the body and prepend the disclosure. Replace the `return (` at the end with:

```python
    def get_conclusion_prompt(self, discussion: Discussion) -> str:
        summary = self._build_full_trajectory(discussion)

        body = (
            "The Delphi Method process is complete.\n\n"
            f"Estimate trajectories:\n{summary}\n\n"
            "Provide a comprehensive synthesis:\n"
            "1. **Final distribution** — Report the median, mean, range, "
            "and inter-quartile range of the final estimates\n"
            "2. **Convergence analysis** — Did the group converge?  How "
            "much did estimates change from initial to final round?\n"
            "3. **Outlier analysis** — Were there persistent outliers?  "
            "Did their reasoning contain unique insights or errors?\n"
            "4. **Key arguments** — Which arguments were most influential "
            "in driving convergence or maintaining divergence?\n"
            "5. **Confidence assessment** — Based on the convergence "
            "pattern and reasoning quality, how confident should we be "
            "in the group estimate?\n"
            "6. **Final answer** — State the group's best estimate with "
            "uncertainty bounds.\n\n"
            "Present actual numbers and cite specific reasoning."
        )
        disclosure = self.panel_composition_disclosure(discussion)
        return f"{disclosure}\n\n{body}" if disclosure else body
```

In `consensus/methods/belief_diffusion.py`, add to the class attributes:

```python
    assumes_independent_panel = True
```

In `get_conclusion_prompt`, wrap only the **normal** (has-hypotheses) return — leave the abort path untouched. Replace the final `return (` block with:

```python
        body = (
            "The Belief State Diffusion process is complete.\n\n"
            f"Hypotheses:\n{hyp_list}\n\n"
            f"Belief trajectories:\n{trajectory}\n\n"
            "Provide a comprehensive diagnosis:\n"
            "1. **Final consensus** — What is the group's final distribution? "
            "Compute the mean probability for each hypothesis.\n"
            "2. **Convergence analysis** — Did beliefs converge? Where does "
            "persistent disagreement remain?\n"
            "3. **Persuasion analysis** — Which arguments caused the largest "
            "belief shifts? Were any participants resistant to strong evidence?\n"
            "4. **Consistency check** — Did any participant's stated reasoning "
            "contradict their actual belief shift? (e.g., said an argument was "
            "compelling but didn't change their beliefs)\n"
            "5. **Conclusion** — What does the group's final belief distribution "
            "tell us about the original question?\n\n"
            "Be specific and cite the data."
        )
        disclosure = self.panel_composition_disclosure(discussion)
        return f"{disclosure}\n\n{body}" if disclosure else body
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_panel_diversity.py tests/test_phases_delphi.py tests/test_phases_belief_diffusion.py tests/test_belief_diffusion_abort.py -q`
Expected: PASS (new tests pass; existing Delphi/Belief tests still pass — the abort path is unchanged).

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/delphi.py consensus/methods/belief_diffusion.py tests/test_panel_diversity.py
git commit -m "feat(methods): disclose panel composition in Delphi + Belief conclusions (#29)"
```

---

### Task 6: `get_state()` panel advisory

**Files:**
- Modify: `consensus/app.py` (`get_state`, near the `track_evidence_phase` block ~line 492)
- Test: `tests/test_panel_diversity.py`

**Interfaces:**
- Consumes: `estimator_models`, `analyze_panel_diversity`, `format_setup_warning` (Tasks 1–3); `assumes_independent_panel` (Task 5); `consensus.methods.get_method`.
- Produces: `state["panel_advisory"]` = `{"level": "warning", "message": str}` or `None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_panel_diversity.py`:

```python
from consensus.app import ConsensusApp


class TestGetStatePanelAdvisory:
    def _app(self, tmp_path):
        return ConsensusApp(db_path=str(tmp_path / "adv.db"))

    def test_delphi_same_model_sets_advisory(self, tmp_path):
        app = self._app(tmp_path)
        pid = app.db.add_provider("Local", "http://x/v1", "")
        mod = app.db.add_entity("Mod", "ai", "#a", pid, "llama3", 0.5, 512, "")
        a = app.db.add_entity("A", "ai", "#b", pid, "gpt-4o", 0.7, 512, "")
        b = app.db.add_entity("B", "ai", "#c", pid, "gpt-4o", 0.7, 512, "")
        app.add_to_discussion(mod, is_moderator=True)
        app.add_to_discussion(a)
        app.add_to_discussion(b)
        app.set_discussion_method("delphi")
        adv = app.get_state()["panel_advisory"]
        assert adv is not None
        assert adv["level"] == "warning"
        assert "gpt-4o" in adv["message"]

    def test_delphi_diverse_no_advisory(self, tmp_path):
        app = self._app(tmp_path)
        pid = app.db.add_provider("Local", "http://x/v1", "")
        mod = app.db.add_entity("Mod", "ai", "#a", pid, "llama3", 0.5, 512, "")
        a = app.db.add_entity("A", "ai", "#b", pid, "gpt-4o", 0.7, 512, "")
        b = app.db.add_entity("B", "ai", "#c", pid, "claude", 0.7, 512, "")
        app.add_to_discussion(mod, is_moderator=True)
        app.add_to_discussion(a)
        app.add_to_discussion(b)
        app.set_discussion_method("delphi")
        assert app.get_state()["panel_advisory"] is None

    def test_open_discussion_no_advisory(self, tmp_path):
        app = self._app(tmp_path)
        pid = app.db.add_provider("Local", "http://x/v1", "")
        a = app.db.add_entity("A", "ai", "#b", pid, "gpt-4o", 0.7, 512, "")
        b = app.db.add_entity("B", "ai", "#c", pid, "gpt-4o", 0.7, 512, "")
        app.add_to_discussion(a, is_moderator=True)
        app.add_to_discussion(b)
        # discussion_method defaults to open_discussion
        assert app.get_state()["panel_advisory"] is None
```

> Note: confirm `set_discussion_method` is the correct app method name (grep `def set_discussion_method` in `consensus/app.py`); also confirm `add_to_discussion(id, is_moderator=True)` matches the signature used in `tests/test_app.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_panel_diversity.py::TestGetStatePanelAdvisory -q`
Expected: FAIL — `KeyError: 'panel_advisory'`.

- [ ] **Step 3: Write minimal implementation**

In `consensus/app.py` `get_state`, just after the `track_evidence_phase` block (before the `pending_user_input` block), add:

```python
        # Same-model panel advisory (#29): for methods that assume
        # independent estimators, warn when one model dominates the panel.
        state["panel_advisory"] = None
        from .methods import get_method
        from .methods.panel_diversity import (
            analyze_panel_diversity, estimator_models, format_setup_warning,
        )
        try:
            cur_method = get_method(self.discussion.discussion_method)
        except KeyError:
            cur_method = None
        if cur_method is not None and cur_method.assumes_independent_panel:
            report = analyze_panel_diversity(estimator_models(self.discussion))
            msg = format_setup_warning(report)
            if msg:
                state["panel_advisory"] = {"level": "warning", "message": msg}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_panel_diversity.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add consensus/app.py tests/test_panel_diversity.py
git commit -m "feat(app): surface panel_advisory in get_state (#29)"
```

---

### Task 7: Frontend — inline advisory + start toast + CSS

**Files:**
- Modify: `consensus/static/index.html` (after `#method-description` ~line 161)
- Modify: `consensus/static/setup.js` (`renderSetupTab`, `onMethodChange`)
- Modify: `consensus/static/discussion-actions.js` (`onStartDiscussion` ~line 22)
- Modify: `consensus/static/style.css` (after `.capability-warning` ~line 1262)

**Interfaces:**
- Consumes: `state.panel_advisory` from `get_state()` (Task 6); `showToast` from `utils.js`.

No JS unit-test harness exists in this project (per HANDOVER); verify by static review + the manual steps below.

- [ ] **Step 1: Add the advisory container to `index.html`**

After line 161 (`<span id="method-description" ...>`), inside the same `<div>`, add:

```html
                    <div id="method-advisory" class="method-advisory" style="display:none; flex-basis:100%;"></div>
```

- [ ] **Step 2: Render the advisory in `setup.js`**

Add this function (e.g. below `updateMethodDescription`):

```javascript
/**
 * Show or hide the same-model panel advisory based on current state (#29).
 */
function renderPanelAdvisory() {
    const el = $('#method-advisory');
    if (!el) return;
    const advisory = state.panel_advisory;
    if (advisory && advisory.message) {
        el.textContent = `⚠ ${advisory.message}`;
        el.style.display = '';
    } else {
        el.textContent = '';
        el.style.display = 'none';
    }
}
```

Call `renderPanelAdvisory();` at the end of `renderSetupTab()` (covers roster/moderator/role changes) and at the end of `onMethodChange()` after `onStateUpdate(s)` (covers method changes). Ensure `renderPanelAdvisory` is in scope for both (same module — it is).

- [ ] **Step 3: Toast the advisory on start in `discussion-actions.js`**

In `onStartDiscussion`, after `onStateUpdate(result);` (line ~24) and before hiding setup, add:

```javascript
    if (result.panel_advisory?.message) {
        showToast(result.panel_advisory.message, 6000, 'warning');
    }
```

(`showToast` is already imported at the top of the file.)

- [ ] **Step 4: Add CSS in `style.css`**

After the `.capability-warning` rule (~line 1262), add:

```css
.method-advisory {
  font-size: 0.85em;
  color: var(--warning, #b8860b);
  margin-top: 0.25rem;
}
```

- [ ] **Step 5: Static verification**

Run the existing suite to confirm no backend regressions:
Run: `uv run pytest -q`
Expected: full suite passes.

Then confirm by reading the diffs:
- `#method-advisory` exists in `index.html` within the method row.
- `renderPanelAdvisory()` is called from both `renderSetupTab` and `onMethodChange`.
- `onStartDiscussion` toasts `result.panel_advisory.message`.

- [ ] **Step 6: Manual verification (optional, if running the app)**

Launch desktop mode (`python -m consensus`), create 3 AI participants on the same model + a moderator, select **Delphi** → the inline advisory appears near the method selector; change one participant's model → it clears. Start the discussion with a same-model panel → a warning toast appears and the discussion proceeds.

- [ ] **Step 7: Commit**

```bash
git add consensus/static/index.html consensus/static/setup.js consensus/static/discussion-actions.js consensus/static/style.css
git commit -m "feat(ui): same-model panel advisory + start toast (#29)"
```

---

## Final verification

- [ ] Run the full suite: `uv run pytest -q` — expect all tests passing (baseline 2324 + new `tests/test_panel_diversity.py`).
- [ ] `git log --oneline` shows the 7 task commits (+ the design-spec commit).

## Self-Review notes (author)

- **Spec coverage:** item 1 setup warning → Tasks 6 (state) + 7 (inline) + 7 (toast); item 2 conclusion disclosure → Tasks 3–5; detection rule → Tasks 1–2; declarative flag → Task 4; item 3 correctly absent (deferred).
- **Type consistency:** `PanelDiversityReport` fields, `analyze_panel_diversity`/`estimator_models`/`format_setup_warning`/`format_conclusion_disclosure` signatures, `assumes_independent_panel`, `panel_composition_disclosure`, and `state["panel_advisory"]` shape are used identically across tasks.
- **Grounding caveats — all pre-verified 2026-07-15:** `EntityType.HUMAN` is the correct member (`consensus/models.py:37`); `app.add_to_discussion(entity_id, is_moderator=…)` (`app.py:600`) and `app.set_discussion_method(method_name)` (`app.py:651`) match; `base.py:22` already imports `Discussion` under `TYPE_CHECKING` (string annotation needs no runtime import); `build_trajectory_summary` reads `state.get("belief_history", [])` and returns `"(No data)"` when absent, so the Belief test only needs non-empty `hypotheses`. The inline "confirm before running" notes are belt-and-suspenders — no changes needed.
