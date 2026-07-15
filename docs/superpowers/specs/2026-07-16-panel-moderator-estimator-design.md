# Participating Moderator as Panel Estimator (#48) — Design Specification

**Date:** 2026-07-16
**Issue:** #48 (follow-up to #29 / PR #47)
**Status:** Approved (design)

## Context

The same-model panel warning (#29) protects Delphi and Belief State Diffusion —
methods whose validity rests on *independent* estimators — by flagging a panel
where one model covers more than half of the AI estimators. The detection core
lives in `consensus/methods/panel_diversity.py`; `estimator_models(discussion)`
builds the roster of estimator model strings that `analyze_panel_diversity`
then scores.

As a documented simplification, #29's `estimator_models` **always** excludes the
moderator. That is wrong in exactly the most-correlated configuration: when the
discussion runs with `moderator_participates`, the moderator takes estimate
turns like everyone else and is a genuine estimator. Excluding it undercounts
the dominant model and can suppress a warranted warning.

Worked example from the issue:

- Moderator = `gpt-4o` (participating), A = `gpt-4o`, B = `claude`.
- Real panel is `[gpt-4o, gpt-4o, claude]` → 2/3 share one model → concerning.
- #29 sees only `[gpt-4o, claude]` → 1/2 → **not** concerning → **no warning**.

## The design decision

`moderator_participates` is a parameter of `start_discussion`, not part of setup
state. So at **setup time** (`get_state` before the discussion starts) the app
cannot know whether the moderator will participate, while at **start time** and
**conclusion time** it can. The setup-time advisory and the conclusion-time
disclosure may therefore legitimately differ — this is the "small design
decision" the issue calls out, and the reason #29 excluded the moderator
unconditionally.

**Resolved (owner decision, 2026-07-16): ground-truth via `base_turn_order`.**
`estimator_models` counts the moderator as an estimator **iff**
`moderator_id in discussion.base_turn_order`. That set is:

- **empty pre-start** → moderator excluded → the setup banner is unchanged (we
  do not warn about a choice the user has not yet made); and
- **populated post-start** → a participating moderator is counted, so the
  start-time toast and the conclusion disclosure both include it.

Rejected alternatives:

- **Pessimistic pre-start warning** (warn at setup "if you enable moderator
  participation, the panel would be dominated…") — over-warns whenever the user
  ultimately leaves participation off; the single-message advisory field makes
  mixing actual vs. conditional warnings awkward.
- **Conclusion-disclosure-only** — leaves the start toast under-warning, since
  the toast is produced by the same `get_state`/`estimator_models` path.

## Why `base_turn_order` is the correct signal

`base_turn_order` holds the moderator's id **iff** the moderator is in the
estimator rotation:

- At `start_discussion`, the moderator is added to `turn_order` (and thus
  `base_turn_order`) only when `moderator_participates` is true
  (`app_discussion_setup.py`).
- On DB reload, `base_turn_order` is rebuilt from `discussion_members` rows that
  carry a non-null `turn_position` (`app_discussion_state.py`), so a resumed
  discussion preserves the signal.

Delphi and Belief State Diffusion do not override `get_turn_order` at the method
level, and neither their estimate nor revise phase handlers narrow the moderator
out of the rotation. A moderator in `base_turn_order` therefore genuinely takes
estimate turns — it is a real correlated estimator, not a proxy for one.

## Scope

**In scope:**

1. `estimator_models` counts a participating AI moderator as an estimator,
   keyed on `moderator_id in base_turn_order`.
2. Unit + integration tests covering the participating vs. non-participating
   cases across all three surfaces (roster, start toast, conclusion disclosure).
3. Docstring + HANDOVER/ROADMAP updates.

**Out of scope (per issue):** family-level model grouping (e.g. `gpt-4o` vs
`gpt-4o-mini`, or one model under different provider name strings). Exact-model
grouping only, unchanged from #29.

## Change surface

Single behavioural change, one function:

```python
def estimator_models(discussion):
    """Return the model strings of a discussion's AI estimators.

    Excludes humans, experts, and AI entities with no resolved ``ai_config``.
    Excludes the moderator UNLESS it participates — i.e. its id is present in
    ``discussion.base_turn_order`` (the estimator rotation).  A participating
    same-model moderator is the strongest form of estimator correlation, so it
    must count toward the panel (#48).
    """
    mod_participates = discussion.moderator_id in discussion.base_turn_order
    models = []
    for e in discussion.entities:
        if e.entity_type != EntityType.AI:
            continue
        if e.id == discussion.moderator_id and not mod_participates:
            continue
        if e.ai_config is None:
            continue
        models.append(e.ai_config.model)
    return models
```

No caller changes are required. Both consumers already call
`estimator_models(discussion)`:

- `app.py::get_state` → setup banner + start toast (`panel_advisory`).
- `methods/base.py::panel_composition_disclosure` → Delphi/Belief conclusion
  prompt.

The fix propagates to all three surfaces from the one edit.

### Edge cases (all handled by the three existing guards)

| Condition | `mod_participates` | Result |
|-----------|--------------------|--------|
| Never started (`base_turn_order == []`) | `False` | Moderator excluded — **behaviour unchanged** |
| `moderator_id is None` | `None in [...]` → `False` | Excluded, no crash |
| Human participating moderator in rotation | `True` | Skipped by `entity_type != AI` guard — not counted |
| AI participating moderator, same model | `True` | Counted → panel correctly flagged |
| AI non-participating moderator | `False` | Excluded — unchanged |

The unchanged-when-empty property is what keeps every existing test green: they
construct discussions with the default empty `base_turn_order`, i.e. the
non-participating case.

## Testing

Follows the issue's acceptance criteria and the project convention of driving
real state rather than idealised handler inputs.

**Pure unit (`TestEstimatorModels`):**

- Participating AI same-model moderator is counted:
  `base_turn_order=[100, 1, 2]`, Mod=`gpt-4o`, A=`gpt-4o`, B=`claude`
  → `estimator_models` returns `["gpt-4o", "gpt-4o", "claude"]`, and
  `analyze_panel_diversity` reports `is_concerning`.
- Non-participating moderator excluded (make the existing implicit case
  explicit): same roster, `base_turn_order=[]` → `["claude", ...]` without the
  moderator's model.
- Participating **human** moderator not counted:
  `base_turn_order=[100, 1]`, Mod is HUMAN → moderator's slot absent.
- `moderator_id=None` with a non-empty `base_turn_order` → no crash, moderator
  logic inert.

**Conclusion disclosure (`TestMethodOptIn` / `TestPanelCompositionDisclosure`):**

- `DelphiMethod().panel_composition_disclosure(disc)` with a same-model
  participating moderator in `base_turn_order` emits the "shared a model" caveat.

**Start-path integration (`TestGetStatePanelAdvisory`):**

- `start_discussion(moderator_participates=True)` (no network in that path) with
  a same-model AI moderator, then `get_state()` → `panel_advisory` is set.
- Same roster with `moderator_participates=False` → `panel_advisory` is `None`.

## Documentation

- Rewrite the second "Documented simplifications" bullet in the
  `panel_diversity.py` module docstring: the moderator is excluded **unless it
  participates** (present in `base_turn_order`).
- Mark #48 resolved in `HANDOVER.md` and `ROADMAP.md`; close the GitHub issue on
  merge.

## Risks

- **Low.** One pure function, no caller changes, backward-compatible by
  construction (empty `base_turn_order` reproduces prior behaviour). The only
  behavioural delta is that a genuinely-participating same-model moderator now
  correctly triggers the warning/disclosure it previously suppressed.
