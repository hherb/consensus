# Participating Moderator as Panel Estimator (#48) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Count a participating AI moderator as an estimator in the same-model panel warning, so the strongest form of estimator correlation can no longer suppress the warning/disclosure.

**Architecture:** One behavioural change to the pure function `estimator_models` in `consensus/methods/panel_diversity.py`: the moderator is counted iff `moderator_id in discussion.base_turn_order` (ground-truth estimator rotation). No caller changes — the two consumers (`app.py::get_state` for setup banner + start toast, `methods/base.py::panel_composition_disclosure` for the conclusion prompt) already call `estimator_models(discussion)`, so the fix propagates to all three surfaces.

**Tech Stack:** Python 3, pytest, `uv` for env/test running.

## Global Constraints

- Package/test management via `uv` only — never `pip`/`venv` (`uv run pytest`).
- Docstrings + type hints mandatory on all functions.
- No magic numbers — reuse existing constants (`DIVERSITY_WARN_FRACTION`).
- Files stay under ~500 lines where feasible.
- TDD: failing test first, then minimal implementation.
- All tests must pass before committing (`uv run pytest`).
- Exact-model grouping only; family-level grouping is out of scope (#48).

---

### Task 1: `estimator_models` counts a participating moderator

**Files:**
- Modify: `consensus/methods/panel_diversity.py` (function `estimator_models`, lines 92-107, + the module-docstring "Documented simplifications" bullet, lines 17-20)
- Test: `tests/test_panel_diversity.py` (add to classes `TestEstimatorModels`, `TestMethodOptIn`, `TestGetStatePanelAdvisory`)

**Interfaces:**
- Consumes: `Discussion.base_turn_order: list[int]`, `Discussion.moderator_id: int | None`, `Discussion.entities`, `Entity.entity_type`, `Entity.ai_config.model` (all existing).
- Produces: `estimator_models(discussion: "Discussion") -> list[str]` — same signature; the returned list now includes a participating AI moderator's model. Downstream `analyze_panel_diversity`, `format_setup_warning`, `format_conclusion_disclosure`, `panel_composition_disclosure`, and `get_state`'s `panel_advisory` are unchanged and pick up the new roster automatically.

- [ ] **Step 1: Write the failing pure-unit tests**

Add these four methods to `class TestEstimatorModels` in `tests/test_panel_diversity.py` (after `test_empty_when_no_ai`). The existing `test_excludes_moderator` (empty `base_turn_order`) stays as-is — it is the never-started case.

```python
    def test_includes_participating_moderator(self):
        # Moderator in base_turn_order == it takes estimate turns.
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("Mod", 100, "gpt-4o"),
                      _ai("A", 1, "gpt-4o"),
                      _ai("B", 2, "claude")],
            moderator_id=100,
            base_turn_order=[100, 1, 2],
        )
        assert estimator_models(disc) == ["gpt-4o", "gpt-4o", "claude"]
        assert analyze_panel_diversity(estimator_models(disc)).is_concerning

    def test_excludes_non_participating_moderator(self):
        # Started, but moderator not in the rotation -> excluded.
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("Mod", 100, "gpt-4o"),
                      _ai("A", 1, "gpt-4o"),
                      _ai("B", 2, "claude")],
            moderator_id=100,
            base_turn_order=[1, 2],
        )
        assert estimator_models(disc) == ["gpt-4o", "claude"]
        assert not analyze_panel_diversity(estimator_models(disc)).is_concerning

    def test_participating_human_moderator_not_counted(self):
        # A human moderator estimates but does not correlate a model.
        disc = Discussion(
            id=1, topic="t",
            entities=[Entity(name="Mod", entity_type=EntityType.HUMAN, id=100),
                      _ai("A", 1, "gpt-4o"),
                      _ai("B", 2, "claude")],
            moderator_id=100,
            base_turn_order=[100, 1, 2],
        )
        assert estimator_models(disc) == ["gpt-4o", "claude"]

    def test_moderator_id_none_with_turn_order_safe(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("A", 1, "gpt-4o"), _ai("B", 2, "claude")],
            moderator_id=None,
            base_turn_order=[1, 2],
        )
        assert estimator_models(disc) == ["gpt-4o", "claude"]
```

- [ ] **Step 2: Write the failing conclusion-disclosure tests**

Add these two methods to `class TestMethodOptIn` (after `test_belief_conclusion_discloses_same_model`). `_delphi_disc(["gpt-4o", "claude"])` builds Mod (id 100, `gpt-4o`) + E0 (id 1, `gpt-4o`) + E1 (id 2, `claude`).

```python
    def test_delphi_conclusion_participating_moderator_discloses(self):
        disc = _delphi_disc(["gpt-4o", "claude"])
        disc.base_turn_order = [100, 1, 2]  # moderator participates
        prompt = DelphiMethod().get_conclusion_prompt(disc)
        assert "Panel composition" in prompt
        assert "caveat" in prompt.lower()

    def test_delphi_conclusion_non_participating_moderator_no_caveat(self):
        # Same roster, moderator NOT in rotation -> panel is [gpt-4o, claude].
        disc = _delphi_disc(["gpt-4o", "claude"])  # base_turn_order stays empty
        prompt = DelphiMethod().get_conclusion_prompt(disc)
        assert "caveat" not in prompt.lower()
```

- [ ] **Step 3: Write the failing start-path integration tests**

Add these two methods to `class TestGetStatePanelAdvisory`. Fresh test DB has no OpenRouter pricing data, so `supports_tools` returns `None` (unknown → allowed) and Delphi's structured-output gate passes. `start_discussion` performs setup only — no network.

```python
    def test_participating_moderator_same_model_sets_advisory_on_start(
        self, tmp_path,
    ):
        app = self._app(tmp_path)
        pid = app.db.add_provider("Local", "http://x/v1", "")
        mod = app.db.add_entity("Mod", "ai", "#a", pid, "gpt-4o", 0.5, 512, "")
        a = app.db.add_entity("A", "ai", "#b", pid, "gpt-4o", 0.7, 512, "")
        b = app.db.add_entity("B", "ai", "#c", pid, "claude", 0.7, 512, "")
        app.set_topic("Estimate X")
        app.add_to_discussion(mod, is_moderator=True)
        app.add_to_discussion(a)
        app.add_to_discussion(b)
        app.set_discussion_method("delphi")
        # Pre-start: participation unknown -> panel [gpt-4o, claude] -> quiet.
        assert app.get_state()["panel_advisory"] is None
        result = app.start_discussion(moderator_participates=True)
        assert "error" not in result
        assert result["panel_advisory"] is not None
        assert result["panel_advisory"]["level"] == "warning"
        assert "gpt-4o" in result["panel_advisory"]["message"]

    def test_non_participating_moderator_same_model_no_advisory_on_start(
        self, tmp_path,
    ):
        app = self._app(tmp_path)
        pid = app.db.add_provider("Local", "http://x/v1", "")
        mod = app.db.add_entity("Mod", "ai", "#a", pid, "gpt-4o", 0.5, 512, "")
        a = app.db.add_entity("A", "ai", "#b", pid, "gpt-4o", 0.7, 512, "")
        b = app.db.add_entity("B", "ai", "#c", pid, "claude", 0.7, 512, "")
        app.set_topic("Estimate X")
        app.add_to_discussion(mod, is_moderator=True)
        app.add_to_discussion(a)
        app.add_to_discussion(b)
        app.set_discussion_method("delphi")
        result = app.start_discussion(moderator_participates=False)
        assert "error" not in result
        assert result["panel_advisory"] is None
```

- [ ] **Step 4: Run the new tests to verify they FAIL**

Run:
```bash
uv run pytest tests/test_panel_diversity.py -k "participating or non_participating or moderator_id_none" -v
```
Expected: the new tests FAIL. `test_includes_participating_moderator` fails on `assert estimator_models(disc) == ["gpt-4o", "gpt-4o", "claude"]` (current code excludes the moderator, returning `["gpt-4o", "claude"]`); the disclosure/start tests fail because the panel is not flagged as concerning.

- [ ] **Step 5: Implement the fix + update the docstring**

In `consensus/methods/panel_diversity.py`, replace `estimator_models` (lines 92-107):

```python
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
```

Then rewrite the second "Documented simplifications" bullet in the module docstring (lines 17-20) to:

```python
  * The moderator is excluded from the estimator panel *unless it
    participates* — i.e. its id is in ``base_turn_order`` (#48).  Before
    start, ``base_turn_order`` is empty, so the setup-time advisory and the
    conclusion-time disclosure may legitimately differ: participation is
    only known once the discussion starts.
```

- [ ] **Step 6: Run the panel-diversity tests to verify they PASS**

Run:
```bash
uv run pytest tests/test_panel_diversity.py -v
```
Expected: all tests PASS, including the four new unit tests, two conclusion tests, two start-path tests, and the pre-existing `test_excludes_moderator` (still green — empty `base_turn_order`).

- [ ] **Step 7: Run the full suite to verify no regression**

Run:
```bash
uv run pytest -q
```
Expected: `2363 passed` (2355 baseline + 8 new tests), no failures.

- [ ] **Step 8: Commit**

```bash
git add consensus/methods/panel_diversity.py tests/test_panel_diversity.py
git commit -m "fix(methods): count participating moderator as panel estimator (#48)

estimator_models excluded the moderator unconditionally, so a
participating same-model moderator — the strongest estimator
correlation — could suppress a warranted same-model panel warning.
Count it iff moderator_id is in base_turn_order (empty pre-start, so
the setup advisory is unchanged; populated once moderator_participates
is known). Fix propagates to the setup banner, start toast, and
conclusion disclosure via the shared function.

Closes #48

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: HANDOVER + ROADMAP reflect #48 resolved

**Files:**
- Modify: `HANDOVER.md` (Open work → Cross-cutting quality: the #48 bullet; test-count line; done table)
- Modify: `ROADMAP.md` (the "Same-model panel warning" row)

**Interfaces:** none (docs only).

- [ ] **Step 1: Update HANDOVER.md**

In `HANDOVER.md`:
- In the "What is done" table, add a row:
  `| Participating moderator counted as estimator | #48 | (this PR) |`
- Update the test-count line under the table to the new total (`2363 passing`).
- Remove the `#48 moderator excluded from estimator panel` bullet from "Open work → Cross-cutting quality" (it is now resolved), and drop the "(follow-up #48 filed)" phrasing from the header's last-updated note.

- [ ] **Step 2: Update ROADMAP.md**

In `ROADMAP.md`, in the "Same-model panel warning" row (the `✅ Done` row), replace the trailing "Exact-model grouping; family-level and a 'diversify' auto-suggest helper deferred" with:
"A participating same-model moderator is counted as an estimator (#48). Exact-model grouping; family-level grouping and a 'diversify' auto-suggest helper deferred."

- [ ] **Step 3: Verify suite still green (docs-only, sanity)**

Run:
```bash
uv run pytest -q
```
Expected: `2363 passed`.

- [ ] **Step 4: Commit**

```bash
git add HANDOVER.md ROADMAP.md
git commit -m "docs: mark #48 (participating moderator estimator) resolved

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- Core change (`estimator_models` keyed on `base_turn_order`) → Task 1 Step 5. ✓
- No caller changes; propagation to all three surfaces → verified by Task 1 Steps 2-3 (conclusion + start-path) and Step 6. ✓
- Edge cases (empty `base_turn_order`, `moderator_id=None`, human moderator) → Task 1 Step 1 tests. ✓
- Tests: participating vs non-participating across roster/toast/conclusion → Task 1 Steps 1-3. ✓
- Docstring update → Task 1 Step 5. ✓
- HANDOVER/ROADMAP + issue closure → Task 2 + `Closes #48` in Task 1 Step 8. ✓
- Out-of-scope family grouping → not touched. ✓

**2. Placeholder scan:** No TBD/TODO; every test and implementation block is complete code. ✓

**3. Type consistency:** `estimator_models(discussion) -> list[str]` signature unchanged; `base_turn_order`/`moderator_id`/`entity_type`/`ai_config.model` names match `models.py`; `_ai`, `_delphi_disc`, `self._app`, `set_topic`, `set_discussion_method`, `add_to_discussion`, `start_discussion` match existing test/app usage. ✓

**Note on test count:** the `2363` figure assumes exactly 8 new tests on the 2355 baseline; if the baseline has shifted, use the actual `collected` number and adjust the HANDOVER line accordingly.
