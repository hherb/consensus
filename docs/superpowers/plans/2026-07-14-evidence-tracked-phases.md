# Evidence-tracked phases (#28) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, per-phase evidence provenance tracker that records what each contribution rests on and marks it grounded vs. reasoning-based — without ever blocking an ungrounded turn.

**Architecture:** A new cross-cutting module `consensus/evidence.py` holds a pure grounding classifier (tool-call path + inline-citation path), a recorder that appends to `method_state["evidence_log"]` and annotates the display text, and a conclusion summary builder. The flow layer (`app_discussion_flow.py`) calls the recorder as a generic post-step when the active phase sets `Phase.track_evidence=True`. Double Crux's `test_crux` is the sole opted-in phase; its conclusion and `crux_map` artifact enumerate the evidentiary basis.

**Tech Stack:** Python 3, dataclasses, `pytest`, `uv` for env management. Vanilla JS/HTML frontend in `consensus/static/`.

## Global Constraints

- Package management: `uv` only — never `pip`.
- TDD: write the failing test first, watch it fail, then implement.
- No magic numbers — all caps/limits as named module constants (`docs/llm/golden_rules.md`).
- Every new function/class carries a docstring + type hints.
- Files stay under ~500 lines.
- All grounded/ungrounded classification is computed in code, never by the model.
- Soft semantics: an ungrounded turn is annotated and logged, never rejected or retried.
- Run tests with `uv run pytest`.

---

### Task 1: `Phase.track_evidence` flag

**Files:**
- Modify: `consensus/methods/base.py:42-50` (the `Phase` dataclass)
- Test: `tests/test_evidence.py` (create)

**Interfaces:**
- Produces: `Phase.track_evidence: bool` (default `False`), readable via the existing `DiscussionMethod.current_phase(discussion) -> Optional[Phase]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_evidence.py`:

```python
"""Tests for evidence-tracked phases (#28)."""
from consensus.methods.base import Phase


class TestPhaseTrackEvidence:
    def test_defaults_to_false(self):
        p = Phase(name="x", display_name="X")
        assert p.track_evidence is False

    def test_can_opt_in(self):
        p = Phase(name="x", display_name="X", track_evidence=True)
        assert p.track_evidence is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_evidence.py::TestPhaseTrackEvidence -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'track_evidence'`

- [ ] **Step 3: Add the field**

In `consensus/methods/base.py`, inside the `Phase` dataclass, after `allow_tools: bool = True`:

```python
    allow_tools: bool = True  # whether tool use is enabled
    track_evidence: bool = False  # opt-in evidence provenance tracking (#28)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_evidence.py::TestPhaseTrackEvidence -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/base.py tests/test_evidence.py
git commit -m "feat(methods): add opt-in Phase.track_evidence flag (#28)"
```

---

### Task 2: Grounding classifier — tool-call path

**Files:**
- Create: `consensus/evidence.py`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Consumes: `consensus.tools.ToolCallRecord` (fields `tool_name: str`, `arguments: dict`, `result: str`, `is_error: bool`).
- Produces:
  - `EVIDENCE_TOOL_NAMES: frozenset[str]`
  - `GroundingResult` dataclass: `grounded: bool`, `sources: list[dict]`
  - `classify_turn_grounding(content: str, tool_calls: list) -> GroundingResult` (inline path added in Task 3)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_evidence.py`:

```python
from consensus.evidence import (
    EVIDENCE_TOOL_NAMES,
    GroundingResult,
    classify_turn_grounding,
)
from consensus.tools import ToolCallRecord


def _tc(tool_name, arguments=None, is_error=False):
    return ToolCallRecord(
        tool_name=tool_name, arguments=arguments or {}, result="ok",
        is_error=is_error,
    )


class TestClassifyToolPath:
    def test_doc_ask_call_is_grounded(self):
        res = classify_turn_grounding(
            "Per the document, X.",
            [_tc("doc_ask", {"document_id": 3, "question": "what is X?"})],
        )
        assert res.grounded is True
        assert res.sources == [
            {"type": "document", "document_id": 3,
             "detail": "what is X?", "tool": "doc_ask"}
        ]

    def test_web_search_call_is_grounded(self):
        res = classify_turn_grounding(
            "Searching.", [_tc("web_search", {"query": "climate data"})])
        assert res.grounded is True
        assert res.sources == [
            {"type": "web_search", "query": "climate data",
             "tool": "web_search"}
        ]

    def test_fetch_webpage_call_is_grounded(self):
        res = classify_turn_grounding(
            "Read it.", [_tc("fetch_webpage", {"url": "https://a.example"})])
        assert res.grounded is True
        assert res.sources == [
            {"type": "web", "url": "https://a.example",
             "tool": "fetch_webpage"}
        ]

    def test_errored_evidence_call_is_not_grounded(self):
        res = classify_turn_grounding(
            "Tried.", [_tc("doc_ask", {"document_id": 3}, is_error=True)])
        assert res.grounded is False
        assert res.sources == []

    def test_non_evidence_tool_is_not_grounded(self):
        res = classify_turn_grounding("Listing.", [_tc("doc_list", {})])
        assert res.grounded is False
        assert res.sources == []

    def test_no_tool_calls_and_no_citation_is_not_grounded(self):
        res = classify_turn_grounding("Just reasoning.", [])
        assert res.grounded is False
        assert res.sources == []

    def test_evidence_tool_set_membership(self):
        assert "doc_ask" in EVIDENCE_TOOL_NAMES
        assert "web_search" in EVIDENCE_TOOL_NAMES
        assert "doc_list" not in EVIDENCE_TOOL_NAMES
        assert "doc_add" not in EVIDENCE_TOOL_NAMES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_evidence.py::TestClassifyToolPath -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'consensus.evidence'`

- [ ] **Step 3: Create `consensus/evidence.py` with the tool path**

```python
"""Evidence-tracked phases (#28).

Turn-level provenance tracking: classify each contribution as grounded
(backed by a tool-sourced or inline citation) or reasoning-based, record
what it rests on into ``method_state["evidence_log"]``, annotate the
display text, and summarise the evidentiary basis for the conclusion.

Soft by design: an ungrounded turn is annotated and logged, never
rejected.  Classification is computed in code, never by the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .tools import ToolCallRecord

#: Participant tools whose successful use grounds a contribution in
#: retrievable evidence.  Excludes management/navigation tools
#: (``doc_add``, ``doc_list``, ``doc_get_length``).
EVIDENCE_TOOL_NAMES: frozenset[str] = frozenset({
    "doc_ask",
    "doc_get_text",
    "doc_summary",
    "doc_get_sections",
    "doc_get_chapter",
    "web_search",
    "fetch_webpage",
})


@dataclass
class GroundingResult:
    """Whether a turn is grounded and the sources it rests on."""

    grounded: bool
    sources: list[dict] = field(default_factory=list)


def _source_from_tool_call(tc: "ToolCallRecord") -> dict | None:
    """Derive a source descriptor from a tool call's name + arguments.

    Robust extraction from ``arguments`` only — ``ToolCallRecord`` drops
    the richer ``ToolResult.metadata``.  Returns ``None`` for tools that
    do not ground a contribution.
    """
    name = tc.tool_name
    args = tc.arguments or {}
    if name in {"doc_ask", "doc_get_text", "doc_summary",
                "doc_get_sections", "doc_get_chapter"}:
        detail = args.get("question") or args.get("range") or ""
        return {"type": "document",
                "document_id": args.get("document_id"),
                "detail": detail, "tool": name}
    if name == "web_search":
        return {"type": "web_search",
                "query": args.get("query", ""), "tool": name}
    if name == "fetch_webpage":
        return {"type": "web", "url": args.get("url", ""), "tool": name}
    return None


def classify_turn_grounding(content: str,
                            tool_calls: list) -> GroundingResult:
    """Classify a turn as grounded or reasoning-based.

    Two detection paths, evaluated together — a turn is grounded if
    either finds a citation:

    * Tool path: a successful (``is_error is False``) call to a tool in
      :data:`EVIDENCE_TOOL_NAMES`.
    * Inline path (added later): a parseable citation in ``content``.
    """
    sources: list[dict] = []
    for tc in tool_calls or []:
        if getattr(tc, "is_error", False):
            continue
        if tc.tool_name in EVIDENCE_TOOL_NAMES:
            src = _source_from_tool_call(tc)
            if src is not None:
                sources.append(src)
    return GroundingResult(grounded=bool(sources), sources=sources)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_evidence.py::TestClassifyToolPath -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/evidence.py tests/test_evidence.py
git commit -m "feat(evidence): grounding classifier tool-call path (#28)"
```

---

### Task 3: Grounding classifier — inline-citation path

**Files:**
- Modify: `consensus/evidence.py`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Consumes: `classify_turn_grounding` from Task 2.
- Produces: same signature, now also grounds on inline citations in `content`:
  - `EVIDENCE_MARKER_RE` matches `[evidence: <ref>]`
  - bare `http(s)://…` URLs

- [ ] **Step 1: Write the failing test**

Append to `tests/test_evidence.py`:

```python
class TestClassifyInlinePath:
    def test_bare_url_is_grounded(self):
        res = classify_turn_grounding(
            "See https://example.org/paper for details.", [])
        assert res.grounded is True
        assert res.sources == [
            {"type": "web", "url": "https://example.org/paper"}
        ]

    def test_evidence_marker_doc_ref_is_grounded(self):
        res = classify_turn_grounding(
            "This holds [evidence: doc:5].", [])
        assert res.grounded is True
        assert res.sources == [{"type": "inline", "ref": "doc:5"}]

    def test_evidence_marker_url_ref_is_grounded(self):
        res = classify_turn_grounding(
            "As shown [evidence: https://a.example/x].", [])
        assert res.grounded is True
        assert res.sources == [
            {"type": "inline", "ref": "https://a.example/x"}
        ]

    def test_plain_text_is_not_grounded(self):
        res = classify_turn_grounding("No citation here at all.", [])
        assert res.grounded is False
        assert res.sources == []

    def test_tool_and_inline_sources_combine(self):
        res = classify_turn_grounding(
            "Per docs and https://a.example.",
            [_tc("doc_ask", {"document_id": 1, "question": "q"})])
        assert res.grounded is True
        assert len(res.sources) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_evidence.py::TestClassifyInlinePath -v`
Expected: FAIL — bare URL / marker cases assert grounded but classifier returns `grounded=False`.

- [ ] **Step 3: Add the inline path**

In `consensus/evidence.py`, add `import re` at the top of the stdlib imports and the two patterns below `EVIDENCE_TOOL_NAMES`:

```python
import re

#: Explicit inline citation marker, e.g. ``[evidence: doc:5]`` or
#: ``[evidence: https://…]``.  Inserted by the frontend "Attach
#: evidence" control; also typeable by hand.
EVIDENCE_MARKER_RE = re.compile(r"\[evidence:\s*([^\]]+?)\s*\]",
                                re.IGNORECASE)

#: Bare http(s) URL.
URL_RE = re.compile(r"https?://[^\s<>\]]+")
```

Add a helper and extend `classify_turn_grounding` to append inline sources:

```python
def _inline_sources(content: str) -> list[dict]:
    """Parse inline citations (explicit markers, then bare URLs)."""
    sources: list[dict] = []
    marked_spans: list[tuple[int, int]] = []
    for m in EVIDENCE_MARKER_RE.finditer(content or ""):
        sources.append({"type": "inline", "ref": m.group(1).strip()})
        marked_spans.append(m.span())
    for m in URL_RE.finditer(content or ""):
        # Skip URLs already captured inside an [evidence: …] marker.
        if any(s <= m.start() < e for s, e in marked_spans):
            continue
        sources.append({"type": "web", "url": m.group(0)})
    return sources
```

Then, in `classify_turn_grounding`, after the tool-call loop and before the `return`:

```python
    sources.extend(_inline_sources(content))
    return GroundingResult(grounded=bool(sources), sources=sources)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_evidence.py -v`
Expected: PASS (both classifier test classes)

- [ ] **Step 5: Commit**

```bash
git add consensus/evidence.py tests/test_evidence.py
git commit -m "feat(evidence): grounding classifier inline-citation path (#28)"
```

---

### Task 4: Recorder — `record_and_annotate_evidence`

**Files:**
- Modify: `consensus/evidence.py`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Consumes: `classify_turn_grounding`; `models.Discussion` (has `.method_state: dict`), `models.Entity` (has `.id: int`, `.name: str`).
- Produces:
  - Constants `GROUNDED_NOTE_PREFIX`, `UNGROUNDED_NOTE` (annotation strings — no magic literals scattered).
  - `record_and_annotate_evidence(discussion, entity, turn_number: int, content: str, tool_calls: list) -> str` — appends an entry to `discussion.method_state["evidence_log"]` and returns annotated `content`.
  - `format_sources(sources: list[dict]) -> str` — compact human string of sources.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_evidence.py`:

```python
from consensus.evidence import record_and_annotate_evidence
from consensus.models import Discussion, Entity, EntityType


def _discussion():
    d = Discussion(topic="t")
    d.method_state = {"current_phase": "test_crux"}
    return d


def _entity(eid=1, name="Alice"):
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


class TestRecordAndAnnotate:
    def test_grounded_turn_logs_and_annotates(self):
        d = _discussion()
        out = record_and_annotate_evidence(
            d, _entity(), turn_number=4, content="Per docs.",
            tool_calls=[_tc("doc_ask", {"document_id": 3, "question": "q"})])
        log = d.method_state["evidence_log"]
        assert len(log) == 1
        entry = log[0]
        assert entry["entity_id"] == 1
        assert entry["entity_name"] == "Alice"
        assert entry["turn"] == 4
        assert entry["phase"] == "test_crux"
        assert entry["grounded"] is True
        assert entry["sources"][0]["document_id"] == 3
        assert "sources:" in out.lower()
        assert out.startswith("Per docs.")

    def test_ungrounded_turn_logs_and_annotates(self):
        d = _discussion()
        out = record_and_annotate_evidence(
            d, _entity(2, "Bob"), turn_number=5,
            content="Pure reasoning.", tool_calls=[])
        entry = d.method_state["evidence_log"][0]
        assert entry["grounded"] is False
        assert entry["sources"] == []
        assert "reasoning-based" in out.lower()

    def test_appends_across_turns(self):
        d = _discussion()
        record_and_annotate_evidence(
            d, _entity(), 1, "a", [_tc("web_search", {"query": "x"})])
        record_and_annotate_evidence(d, _entity(2, "Bob"), 2, "b", [])
        assert len(d.method_state["evidence_log"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_evidence.py::TestRecordAndAnnotate -v`
Expected: FAIL — `ImportError: cannot import name 'record_and_annotate_evidence'`

- [ ] **Step 3: Implement the recorder**

In `consensus/evidence.py`, add the annotation constants below the regexes:

```python
#: Prefix for the grounded-turn sources footer.
GROUNDED_NOTE_PREFIX = "\n\n— sources: "
#: Footer appended to reasoning-based (ungrounded) turns.
UNGROUNDED_NOTE = "\n\n— reasoning-based contribution (no cited evidence)"
```

Then add:

```python
def format_sources(sources: list[dict]) -> str:
    """Render sources as a compact ``; ``-joined human string."""
    parts: list[str] = []
    for s in sources:
        if s.get("type") == "document":
            parts.append(f"document {s.get('document_id')}")
        elif s.get("type") in {"web", "web_search"}:
            parts.append(s.get("url") or s.get("query", "web"))
        elif s.get("type") == "inline":
            parts.append(str(s.get("ref", "")))
    return "; ".join(p for p in parts if p)


def record_and_annotate_evidence(discussion: Any, entity: Any,
                                 turn_number: int, content: str,
                                 tool_calls: list) -> str:
    """Classify the turn, log it, and return annotated display content.

    Appends an entry to ``discussion.method_state["evidence_log"]`` and
    annotates ``content`` (grounded → sources footer; ungrounded →
    reasoning-based note).  Never rejects.
    """
    state = discussion.method_state
    result = classify_turn_grounding(content, tool_calls)
    state.setdefault("evidence_log", []).append({
        "entity_id": entity.id,
        "entity_name": entity.name,
        "turn": turn_number,
        "phase": state.get("current_phase", ""),
        "grounded": result.grounded,
        "sources": result.sources,
    })
    if result.grounded:
        return content + GROUNDED_NOTE_PREFIX + format_sources(result.sources)
    return content + UNGROUNDED_NOTE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_evidence.py::TestRecordAndAnnotate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/evidence.py tests/test_evidence.py
git commit -m "feat(evidence): record + annotate turn grounding (#28)"
```

---

### Task 5: Conclusion summary — `build_evidence_summary`

**Files:**
- Modify: `consensus/evidence.py`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Consumes: `method_state["evidence_log"]` entries from Task 4.
- Produces: `build_evidence_summary(state: dict) -> dict` returning
  `{"grounded": [...], "reasoning_based": [...], "counts": {"grounded": int, "reasoning_based": int}}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_evidence.py`:

```python
from consensus.evidence import build_evidence_summary


class TestBuildEvidenceSummary:
    def test_empty_log(self):
        assert build_evidence_summary({}) == {
            "grounded": [], "reasoning_based": [],
            "counts": {"grounded": 0, "reasoning_based": 0},
        }

    def test_partitions_entries(self):
        state = {"evidence_log": [
            {"entity_name": "Alice", "grounded": True,
             "sources": [{"type": "document", "document_id": 3}]},
            {"entity_name": "Bob", "grounded": False, "sources": []},
            {"entity_name": "Alice", "grounded": True,
             "sources": [{"type": "web", "url": "https://a"}]},
        ]}
        out = build_evidence_summary(state)
        assert out["counts"] == {"grounded": 2, "reasoning_based": 1}
        assert len(out["grounded"]) == 2
        assert out["reasoning_based"] == [{"entity_name": "Bob"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_evidence.py::TestBuildEvidenceSummary -v`
Expected: FAIL — `ImportError: cannot import name 'build_evidence_summary'`

- [ ] **Step 3: Implement the summary builder**

In `consensus/evidence.py`:

```python
def build_evidence_summary(state: dict) -> dict:
    """Summarise ``evidence_log`` for the conclusion / artifact.

    Partitions logged turns into grounded (with their sources) and
    reasoning-based, with counts.  Deterministic.
    """
    log = state.get("evidence_log", []) if state else []
    grounded: list[dict] = []
    reasoning: list[dict] = []
    for e in log:
        if e.get("grounded"):
            grounded.append({"entity_name": e.get("entity_name"),
                             "sources": e.get("sources", [])})
        else:
            reasoning.append({"entity_name": e.get("entity_name")})
    return {
        "grounded": grounded,
        "reasoning_based": reasoning,
        "counts": {"grounded": len(grounded),
                   "reasoning_based": len(reasoning)},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_evidence.py::TestBuildEvidenceSummary -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add consensus/evidence.py tests/test_evidence.py
git commit -m "feat(evidence): conclusion evidence summary builder (#28)"
```

---

### Task 6: Flow wiring — AI and human turns

**Files:**
- Modify: `consensus/app_discussion_flow.py` (`generate_ai_turn` ~lines 243-260; `submit_human_message` ~lines 117-127)
- Test: `tests/test_evidence_flow.py` (create)

**Interfaces:**
- Consumes: `record_and_annotate_evidence` (Task 4); `DiscussionMethod.current_phase(discussion)` (existing, returns a `Phase` with `.track_evidence`).
- Produces: when the active phase has `track_evidence=True`, both entry points route the (possibly structured) display content through `record_and_annotate_evidence` before persisting; AI passes `resp.tool_calls`, humans pass `[]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_evidence_flow.py`. The `tmp_db` fixture is provided by
`tests/conftest.py` (real temp SQLite). The `Discussion` construction
mirrors `_make_discussion` in `tests/test_turn_order_flow.py`; a single
human whose turn is current lets us drive `submit_human_message` with no
network. `get_active_method` is monkeypatched to a fake so this task is
independent of Task 7 opting `test_crux` in.

```python
"""Flow-level wiring for evidence-tracked phases (#28)."""
from consensus.app_discussion_flow import submit_human_message
from consensus.methods.base import Phase, ProcessedResponse
from consensus.models import Discussion, Entity


class _FakePhaseMethod:
    """Minimal active method exposing a track_evidence phase."""

    def __init__(self, track):
        self._phase = Phase(name="test_crux", display_name="Crux",
                            track_evidence=track)

    def current_phase(self, discussion):
        return self._phase

    def process_response(self, content, entity, discussion):
        return ProcessedResponse(display_content=content)


def _human_turn_discussion(db, phase="test_crux"):
    """An active discussion whose current speaker is a human."""
    eid = db.add_entity("Alice", "human", "#123456")
    alice = Entity.from_db_row(db.get_entity(eid))
    disc = Discussion(
        topic="t", entities=[alice],
        turn_order=[alice.id], base_turn_order=[alice.id],
        current_turn_index=0, turn_number=1,
        is_active=True, status="active", discussion_method="double_crux",
    )
    disc.id = db.create_discussion(disc.topic, alice.id)
    disc.method_state = {"current_phase": phase}
    return disc, alice


def test_human_turn_in_tracked_phase_is_logged(tmp_db, monkeypatch):
    disc, alice = _human_turn_discussion(tmp_db, phase="test_crux")
    monkeypatch.setattr(
        "consensus.app_discussion_flow.get_active_method",
        lambda d: _FakePhaseMethod(track=True))
    submit_human_message(disc, tmp_db, alice.id,
                         "It holds, see https://a.example/x")
    log = disc.method_state["evidence_log"]
    assert log and log[0]["grounded"] is True
    assert log[0]["sources"][0]["url"] == "https://a.example/x"


def test_human_turn_untracked_phase_no_log(tmp_db, monkeypatch):
    disc, alice = _human_turn_discussion(tmp_db, phase="positions")
    monkeypatch.setattr(
        "consensus.app_discussion_flow.get_active_method",
        lambda d: _FakePhaseMethod(track=False))
    submit_human_message(disc, tmp_db, alice.id, "Just my opinion.")
    assert "evidence_log" not in disc.method_state
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_evidence_flow.py -v`
Expected: FAIL — `evidence_log` is never created (wiring absent).

- [ ] **Step 3: Wire the human path**

In `consensus/app_discussion_flow.py`, add the import near the top:

```python
from .evidence import record_and_annotate_evidence
```

In `submit_human_message`, replace the post-processing block (currently
lines ~119-127):

```python
    method = get_active_method(discussion)
    if method and not is_pass(content):
        processed = method.process_response(content, entity, discussion)
        content = processed.display_content
        phase = method.current_phase(discussion)
        if phase is not None and phase.track_evidence:
            content = record_and_annotate_evidence(
                discussion, entity, discussion.turn_number, content,
                tool_calls=[])
        if discussion.id:
            db.update_discussion(
                discussion.id,
                method_state=serialize_method_state(discussion.method_state),
            )
```

- [ ] **Step 4: Wire the AI path**

In `generate_ai_turn`, inside `if method and not passed:` after
`content = processed.display_content` (line ~254) and before the persist
block:

```python
            content = processed.display_content
            phase = method.current_phase(discussion)
            if phase is not None and phase.track_evidence:
                content = record_and_annotate_evidence(
                    discussion, current, discussion.turn_number, content,
                    resp.tool_calls)
            # Persist updated method_state
            if discussion.id:
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_evidence_flow.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add consensus/app_discussion_flow.py tests/test_evidence_flow.py
git commit -m "feat(flow): record evidence provenance on tracked-phase turns (#28)"
```

---

### Task 7: Opt in `test_crux` and wire Double Crux conclusion + artifact

**Files:**
- Modify: `consensus/methods/phases/test_crux.py:26-35` (set `track_evidence=True`)
- Modify: `consensus/methods/phases/_crux_helpers.py:424-432` (`build_crux_map` return — add `evidence`)
- Modify: `consensus/methods/double_crux.py:70-96` (`get_conclusion_prompt` factual branch — enumerate evidence)
- Test: `tests/test_crux_helpers.py`, `tests/test_phases_double_crux.py`

**Interfaces:**
- Consumes: `build_evidence_summary` (Task 5); `Phase.track_evidence` (Task 1); `record_and_annotate_evidence` wiring (Task 6).
- Produces: `build_crux_map(state)["evidence"]` carries the summary; the conclusion prompt lists grounded sources and notes reasoning-based contributions.

- [ ] **Step 1: Write the failing test (artifact)**

Append to `tests/test_crux_helpers.py`:

```python
def test_build_crux_map_includes_evidence_summary():
    from consensus.methods.phases._crux_helpers import build_crux_map
    state = {
        "crux_verdict": "factual",
        "evidence_log": [
            {"entity_name": "Alice", "grounded": True,
             "sources": [{"type": "document", "document_id": 3}]},
            {"entity_name": "Bob", "grounded": False, "sources": []},
        ],
    }
    artifact = build_crux_map(state)
    assert artifact["evidence"]["counts"] == {
        "grounded": 1, "reasoning_based": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crux_helpers.py::test_build_crux_map_includes_evidence_summary -v`
Expected: FAIL — `KeyError: 'evidence'`

- [ ] **Step 3: Add `evidence` to the artifact**

In `consensus/methods/phases/_crux_helpers.py`, add the import near the
other imports at the top:

```python
from ...evidence import build_evidence_summary
```

In `build_crux_map`, add to the returned dict (after `"caveats": caveats,`):

```python
        "caveats": caveats,
        "evidence": build_evidence_summary(state),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_crux_helpers.py::test_build_crux_map_includes_evidence_summary -v`
Expected: PASS

- [ ] **Step 5: Opt in `test_crux` and verify the flag**

In `consensus/methods/phases/test_crux.py`, add `track_evidence=True` to
the `Phase(...)` constructor (after `rounds=TEST_CRUX_ROUNDS,`):

```python
        rounds=TEST_CRUX_ROUNDS,
        track_evidence=True,
    )
```

Append to `tests/test_phases_double_crux.py`:

```python
def test_test_crux_phase_tracks_evidence():
    from consensus.methods.phases.test_crux import TestCruxHandler
    assert TestCruxHandler().phase.track_evidence is True
```

Run: `uv run pytest tests/test_phases_double_crux.py::test_test_crux_phase_tracks_evidence -v`
Expected: PASS

- [ ] **Step 6: Enumerate evidence in the conclusion prompt**

In `consensus/methods/double_crux.py`, add the import near the top:

```python
from ..evidence import build_evidence_summary, format_sources
```

Add a module-level helper (near the other `format_*` uses) and call it in
the factual branch of `get_conclusion_prompt`. Add this function above the
`DoubleCrux` class:

```python
def _format_evidence_basis(state: dict) -> str:
    """Render the grounded/reasoning-based basis for the conclusion."""
    summary = build_evidence_summary(state)
    if not summary["grounded"] and not summary["reasoning_based"]:
        return "Evidentiary basis: no contributions were logged."
    lines = [
        f"Grounded contributions ({summary['counts']['grounded']}):"
    ]
    for g in summary["grounded"]:
        src = format_sources(g["sources"]) or "(sources recorded)"
        lines.append(f"  - {g['entity_name']}: {src}")
    reasoning = summary["reasoning_based"]
    if reasoning:
        names = ", ".join(r["entity_name"] for r in reasoning)
        lines.append(
            f"Reasoning-based (no cited evidence): {names}")
    return "\n".join(lines)
```

In the `VERDICT_FACTUAL` branch of `get_conclusion_prompt`, insert the
basis into the prompt just before the `"Provide a comprehensive synthesis:"`
line:

```python
                f"{format_belief_shifts(state)}\n\n"
                f"{_format_evidence_basis(state)}\n\n"
                "Provide a comprehensive synthesis:\n"
```

- [ ] **Step 7: Write the conclusion test**

Append to `tests/test_phases_double_crux.py`:

```python
def test_conclusion_prompt_lists_evidence_basis():
    from consensus.methods.double_crux import DoubleCrux
    from consensus.models import Discussion
    d = Discussion(topic="t")
    d.method_state = {
        "crux_verdict": "factual",
        "shared_crux": {}, "positions": {}, "resolutions": [],
        "evidence_log": [
            {"entity_name": "Alice", "grounded": True,
             "sources": [{"type": "web", "url": "https://a.example"}]},
            {"entity_name": "Bob", "grounded": False, "sources": []},
        ],
    }
    prompt = DoubleCrux().get_conclusion_prompt(d)
    assert "Grounded contributions (1)" in prompt
    assert "https://a.example" in prompt
    assert "Reasoning-based" in prompt
    assert "Bob" in prompt
```

- [ ] **Step 8: Run the Double Crux suite**

Run: `uv run pytest tests/test_phases_double_crux.py tests/test_crux_helpers.py tests/test_double_crux_structured.py -v`
Expected: PASS (new tests + no regressions)

- [ ] **Step 9: Commit**

```bash
git add consensus/methods/phases/test_crux.py consensus/methods/phases/_crux_helpers.py consensus/methods/double_crux.py tests/test_crux_helpers.py tests/test_phases_double_crux.py
git commit -m "feat(double-crux): track evidence in test_crux; enumerate basis in conclusion (#28)"
```

---

### Task 8: Minimal "Attach evidence" UI

**Files:**
- Modify: `consensus/static/index.html:358-363` (input row)
- Modify: `consensus/static/app.js:172-201` (the `data-action` `switch`)
- Modify: `consensus/static/discussion.js:282-290` (`updateInputArea` human-turn branch)
- Manual verification (no JS test harness in this project)

**Interfaces:**
- Produces: an "Attach evidence" button that inserts an `[evidence: ]` marker (caret placed just before `]`) into `#message-input`. The marker is exactly what `EVIDENCE_MARKER_RE` (Task 3) parses.

- [ ] **Step 1: Add the button to the input row**

In `consensus/static/index.html`, inside `<div class="input-row">` after
the `consult-expert-btn` button (line ~362):

```html
                        <button id="attach-evidence-btn" class="btn btn-outline hidden" data-action="attach-evidence-btn"
                                title="Insert an evidence citation marker">Attach evidence</button>
```

- [ ] **Step 2: Add the click handler to the app.js switch**

In `consensus/static/app.js`, in the `data-action` `switch` (the block
ending with `case 'consult-expert-btn': showConsultExpertDialog(); break;`
at line ~200), add a new case:

```javascript
            case 'attach-evidence-btn': insertEvidenceMarker(); break;
```

Then define the helper (place it near the other input helpers in
`app.js`, or export it from `discussion-actions.js` and import it — match
where `showConsultExpertDialog` lives):

```javascript
function insertEvidenceMarker() {
    const input = document.getElementById('message-input');
    if (!input) return;
    const marker = '[evidence: ]';
    const pos = input.selectionStart ?? input.value.length;
    input.value = input.value.slice(0, pos) + marker + input.value.slice(pos);
    // Place caret just before the closing bracket.
    const caret = pos + marker.length - 1;
    input.focus();
    input.setSelectionRange(caret, caret);
}
```

- [ ] **Step 3: Reveal the button on a human participant's turn**

In `consensus/static/discussion.js`, in `updateInputArea`, mirror the
`consultBtn` show/hide pattern (lines ~253-258). Add near the top of the
function, after `const consultBtn = ...` block, a default hide:

```javascript
    const evidenceBtn = $('#attach-evidence-btn');
    if (evidenceBtn) hide(evidenceBtn);
```

Then in the human-turn `else` branch (lines ~285-290, where
`input.disabled = false`), reveal it:

```javascript
    } else {
        turnInfo.textContent = `${speaker.name}'s turn to speak`;
        input.disabled = false; sendBtn.disabled = false;
        input.placeholder = `Type ${speaker.name}'s message...`;
        if (evidenceBtn) show(evidenceBtn);
        input.focus();
    }
```

The marker is harmless in any phase (only `track_evidence` phases parse
it), so revealing it on every human turn is acceptable for this slice.

- [ ] **Step 4: Manual verification**

Run the app: `uv run python -m consensus --web --debug`
Start a Double Crux discussion with a human participant, reach a human
turn, click **Attach evidence**, confirm `[evidence: ]` is inserted with
the caret before `]`, type `doc:3`, send, and confirm the stored message
contains `[evidence: doc:3]` and (in `test_crux`) an entry appears in
`method_state["evidence_log"]` marked grounded.

- [ ] **Step 5: Commit**

```bash
git add consensus/static/index.html consensus/static/discussion-actions.js
git commit -m "feat(ui): minimal Attach evidence marker button (#28)"
```

---

### Task 9: Full-suite regression + HANDOVER update

**Files:**
- Modify: `HANDOVER.md`
- Test: full suite

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: all pass (2250 prior + new evidence tests).

- [ ] **Step 2: Update HANDOVER.md**

Move #28 from "Open work → Cross-cutting quality" into the "What is done"
ledger with a one-line entry and the new PR number placeholder, and add a
short follow-up note listing the deferred items (per-claim mapping;
`gather_evidence`/`present_evidence` opt-in; hard-retry variant; rich
attach-evidence UI). Keep it terse — one or two lines each.

- [ ] **Step 3: Commit**

```bash
git add HANDOVER.md
git commit -m "docs: record #28 evidence-tracked phases in HANDOVER"
```

---

## Notes for the implementer

- **Do not block turns.** Every path here is additive: classify, log,
  annotate. If classification raises, prefer logging the turn as ungrounded
  over propagating an exception into the turn flow — but keep the classifier
  total (it does not raise on empty/None inputs by construction).
- **`current_phase` never returns `None` in practice** for an active method
  (it falls back to the first phase), but the wiring guards `is not None`
  anyway — keep the guard.
- **Passes are excluded**: the AI wiring sits inside `if method and not
  passed:`, and the human wiring inside `if method and not is_pass(content):`,
  so passed turns are never logged. This is intended.
- **Source `document_id` may be `None`** if a model omits it; that is fine —
  the entry still records the tool as grounding evidence.
