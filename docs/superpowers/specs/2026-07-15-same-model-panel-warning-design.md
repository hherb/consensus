# Same-Model Panel Warning (Delphi / Belief Diffusion) — Design Specification

**Date:** 2026-07-15
**Issue:** #29
**Status:** Approved (design)

## Context

Delphi and Belief State Diffusion derive their power from *independent*
estimators whose errors partially cancel. When every AI participant is a
persona of the same base model, their estimates are strongly correlated —
anonymisation and IQR-convergence then largely measure the model agreeing
with itself, producing unearned confidence.

This is the most misleading failure mode of the two forecasting methods, and
it is cheap to detect: the provider registry already records each entity's
model (`Entity.ai_config.model`).

## Scope

Issue #29 proposes three increments. This design builds **items 1 and 2** and
**defers item 3**:

1. **Setup warning** — detect when the AI estimator panel is dominated by one
   model and surface a non-blocking warning, both inline in the setup tab and
   as a toast when the discussion starts.
2. **Conclusion disclosure** — the two methods' conclusion prompts disclose the
   panel's model composition so the synthesis can caveat the convergence claim.
3. **(Deferred)** A "diversify" helper that auto-suggests spreading
   participants across configured providers/models. The issue itself marks this
   "Later"; not built this slice.

### Owner decisions captured during brainstorming (2026-07-15)

- **Surface:** *both* — inline setup advisory **and** a start-time toast.
- **Trigger:** *majority share one model* — warn when the dominant model covers
  more than half the estimator panel, with a stronger note when unanimous.
- **Scope:** items 1 + 2 only; defer item 3.

## Detection Rule (pure)

The **estimator panel** is the set of AI participants whose independent
estimates the method aggregates:

```
panel = [e for e in discussion.entities
         if e.entity_type == EntityType.AI and e.id != discussion.moderator_id]
```

Humans and experts are excluded (they do not correlate by model). The moderator
is excluded because its role is synthesis, not estimation.

Each estimator contributes its exact `ai_config.model` string. Let:

- `panel_size` = number of estimators
- `dominant_count` = size of the largest same-model group
- `distinct_models` = number of distinct model strings

Then:

- **Concerning** ⟺ `panel_size >= 2` **and**
  `dominant_count > DIVERSITY_WARN_FRACTION * panel_size`
  (`DIVERSITY_WARN_FRACTION = 0.5`, i.e. *strictly more than half*).
- **Unanimous** ⟺ `panel_size >= 2` **and** `dominant_count == panel_size`.

Worked examples:

| panel_size | dominant_count | concerning? | unanimous? |
|---|---|---|---|
| 2 | 2 | yes | yes |
| 3 | 2 | yes (2 > 1.5) | no |
| 4 | 2 | no (2 > 2 false) | no |
| 4 | 3 | yes (3 > 2) | no |
| 3 | 1 | no | no |
| 1 | 1 | no | no |
| 0 | 0 | no | no |

### Documented simplifications

- **Exact-model grouping only.** Family-level correlation (e.g. `gpt-4o` vs
  `gpt-4o-mini`, or the same model served under different provider name
  strings) is *not* detected. Near-duplicate model names undercount the
  dominant group. Family grouping is a future refinement (pricing.py already
  has fuzzy model-name matching that could seed it).
- **Moderator always excluded**, even when `moderator_participates` is set (in
  which case the moderator is also an estimator). Excluding it keeps the setup
  computation — where `moderator_participates` is not yet known — consistent
  with the conclusion-time computation. The dominant correlation risk is among
  the participant estimators regardless.

## Architecture

### New module: `consensus/methods/panel_diversity.py`

Pure functions plus a frozen dataclass. No side effects, no I/O.

```python
DIVERSITY_WARN_FRACTION: float = 0.5

@dataclass(frozen=True)
class PanelDiversityReport:
    panel_size: int
    distinct_models: int
    dominant_model: str        # "" when panel is empty
    dominant_count: int
    is_concerning: bool
    is_unanimous: bool

def estimator_models(discussion) -> list[str]:
    """Extract AI estimator model strings from a Discussion.

    Excludes the moderator, humans, and experts.
    """

def analyze_panel_diversity(
    models: list[str], *, fraction: float = DIVERSITY_WARN_FRACTION,
) -> PanelDiversityReport:
    """Pure analysis over a list of model-name strings."""

def format_setup_warning(report: PanelDiversityReport) -> str | None:
    """One-line setup warning, or None when the panel is not concerning."""

def format_conclusion_disclosure(report: PanelDiversityReport) -> str:
    """Composition disclosure for conclusion prompts.

    Returns "" when there are fewer than 2 estimators (nothing to disclose).
    Always states the composition when there are >= 2; appends the
    "discount the convergence" caveat only when the panel is concerning.
    """
```

`analyze_panel_diversity` is the tested core. `estimator_models` is the thin
Discussion adapter. The two `format_*` functions separate presentation from
data so both are independently testable.

### Declarative flag: `DiscussionMethod.assumes_independent_panel`

Add to the `DiscussionMethod` ABC (`consensus/methods/base.py`):

```python
class DiscussionMethod(ABC):
    #: Methods whose validity depends on estimators being independent
    #: (Delphi, Belief State Diffusion). Drives the same-model panel
    #: warning and the conclusion-time composition disclosure (#29).
    assumes_independent_panel: bool = False
```

A base helper renders the conclusion disclosure so the two call sites stay
tiny and the "off" case is centralised:

```python
def panel_composition_disclosure(self, discussion) -> str:
    """Return the model-composition disclosure for the conclusion prompt.

    Empty string unless the method assumes an independent panel.
    """
    if not self.assumes_independent_panel:
        return ""
    from .panel_diversity import analyze_panel_diversity, estimator_models, \
        format_conclusion_disclosure
    report = analyze_panel_diversity(estimator_models(discussion))
    return format_conclusion_disclosure(report)
```

`DelphiMethod` and `BeliefDiffusion` set `assumes_independent_panel = True` and
prepend `self.panel_composition_disclosure(discussion)` (when non-empty) to
their existing `get_conclusion_prompt` output.

### Data flow

**Setup (inline advisory).** `ConsensusApp.get_state()` (`consensus/app.py`)
computes an optional `panel_advisory`:

```python
state["panel_advisory"] = None
try:
    method = get_method(self.discussion.discussion_method)
except KeyError:
    method = None
if method is not None and method.assumes_independent_panel:
    report = analyze_panel_diversity(estimator_models(self.discussion))
    msg = format_setup_warning(report)
    if msg:
        state["panel_advisory"] = {"level": "warning", "message": msg}
```

Because setup actions (add/remove entity, set method) return `get_state()` and
trigger `onStateUpdate`, the advisory updates reactively. `setup.js` renders
`state.panel_advisory.message` near the method selector, reusing the existing
`.capability-warning` styling (relative units, CSS custom properties per the
project's CSS rule — no hard-coded pixel sizes).

**Start (toast).** `start_discussion` succeeds by returning `get_state()`, so
`result.panel_advisory` is already present. `onStartDiscussion`
(`consensus/static/discussion-actions.js`) reads it after a successful start
and, if present, calls `showToast(message, <duration>, 'warning')`. Non-blocking
— the discussion still starts.

**Conclusion (item 2).** Delivered by the base helper wired into the two
`get_conclusion_prompt` methods (above).

### Message copy

- **Setup, unanimous:** "All N AI participants use the same model ('X').
  This method assumes independent estimators — with one shared model, apparent
  convergence largely reflects the model agreeing with itself. Consider
  assigning different models or providers."
- **Setup, majority (not unanimous):** "M of N AI participants share the model
  'X'. This method assumes independent estimators; shared models correlate
  their errors and inflate apparent convergence. Consider diversifying models
  or providers."
- **Conclusion, always (>= 2 estimators):** "Panel composition: <breakdown of
  models and counts>."
- **Conclusion, concerning (appended):** "Because M of N participants shared a
  model, treat the convergence above as partly the model agreeing with itself
  and caveat the confidence accordingly."

The `{Method}` name is not available inside the pure formatter; the setup
message uses a generic phrasing ("these methods"/"this method") to stay
method-agnostic. (The method name *is* available at the `get_state` call site
if a per-method phrasing is later wanted — deferred to keep the formatter
pure.)

## Files

### New

| File | Description |
|---|---|
| `consensus/methods/panel_diversity.py` | Pure module: `PanelDiversityReport`, `estimator_models`, `analyze_panel_diversity`, `format_setup_warning`, `format_conclusion_disclosure`, `DIVERSITY_WARN_FRACTION` |
| `tests/test_panel_diversity.py` | Pure-function unit tests |

### Modified

| File | Change |
|---|---|
| `consensus/methods/base.py` | Add `assumes_independent_panel` class attr + `panel_composition_disclosure` helper |
| `consensus/methods/delphi.py` | Set flag `True`; prepend disclosure in `get_conclusion_prompt` |
| `consensus/methods/belief_diffusion.py` | Set flag `True`; prepend disclosure in `get_conclusion_prompt` |
| `consensus/app.py` | Compute `panel_advisory` in `get_state()` |
| `consensus/static/setup.js` | Render `state.panel_advisory` inline near the method selector |
| `consensus/static/discussion-actions.js` | Toast `result.panel_advisory` after a successful start |
| `consensus/static/*.css` | Reuse `.capability-warning` (add a `.panel-advisory` wrapper only if layout needs it; relative units only) |

### Not needed

- **No DB migration** — the advisory is derived state, never persisted.
- **No `models.py` change** — `ai_config.model` already exists.
- **No new method registration** — no new discussion method.

## Regression Safety

1. **`assumes_independent_panel` defaults `False`** — every existing method is
   unaffected; only Delphi and Belief Diffusion opt in.
2. **`panel_advisory` is additive** — a new optional key in `get_state()`;
   `None` for all other methods and diverse panels. No existing consumer reads
   it.
3. **Conclusion disclosure is additive** — prepended only when the flag is set
   *and* the disclosure is non-empty; the existing prompt text is preserved.
4. **Pure module has no dependencies on engine state** beyond reading
   `discussion.entities` — no ordering, DB, or network coupling.
5. **Frontend changes are display-only** and guarded on `state.panel_advisory`
   presence.

## Verification

### Unit tests (`tests/test_panel_diversity.py`) — the bulk

- `analyze_panel_diversity`: boundary cases from the worked-examples table
  (2/2, 2/3, 2/4, 3/4, 3/1, single, empty), tie between two equally-common
  models (dominant_count picks the tie size; not concerning unless > half),
  all-distinct panel.
- `estimator_models`: excludes moderator, humans, experts; includes AI
  participants; empty when no AI participants.
- `format_setup_warning`: `None` when not concerning; unanimous vs majority
  wording; includes counts and the dominant model name.
- `format_conclusion_disclosure`: `""` for <2 estimators; composition line for
  a diverse panel with no caveat; composition line + caveat for a concerning
  panel.

### Method / integration tests

- `DiscussionMethod.assumes_independent_panel` is `False` by default; `True`
  only for `DelphiMethod` and `BeliefDiffusion`.
- `DelphiMethod.get_conclusion_prompt` / `BeliefDiffusion.get_conclusion_prompt`
  include the disclosure for a same-model roster and omit the caveat (but may
  still show composition) for a diverse roster; a non-independence method's
  conclusion prompt is unchanged.
- `get_state()` emits `panel_advisory` for a Delphi discussion with a same-model
  roster, omits it (`None`) for a diverse roster, and omits it for a
  non-independence method — all constructed in-memory, no network.

### Manual / static

- Setup tab: select Delphi with a same-model roster → inline advisory appears;
  change one participant's model → advisory clears. (Verified statically; this
  project has no JS test harness.)
- Start such a discussion → warning toast appears, discussion proceeds.
