# Evidence-tracked phases (#28) — Design

_Date: 2026-07-14 · Issue: #28 · Status: approved, pending implementation plan_

## Summary

Add an opt-in, per-phase capability that records what each participant
contribution rests on and marks it **grounded** (backed by a tool-sourced or
inline citation) versus **reasoning-based** (no citation). The distinction is
recorded in `method_state`, surfaced inline in the transcript, and enumerated
in the method's conclusion.

The mechanism is **soft**: an ungrounded turn is annotated and logged, never
rejected or forced to retry.

### Guiding principle (owner decision, 2026-07-14)

Some problems require creativity not grounded in prior evidence; absence of
evidence is not evidence of absence. The consensus system will face both
evidence-rich and evidence-sparse problems. The system's job is to *distinguish*
well-grounded contributions from reasoning-based ones and make that distinction
visible — not to suppress ungrounded reasoning. Therefore this feature is a
**provenance tracker + annotator**, not an enforcement gate. Participants
(human and AI) are held to the same standard; only the detection path differs.

## Scope

**In scope (first slice):**
- `Phase.track_evidence` flag (opt-in).
- Turn-level grounding classifier with two detection paths (tool calls; inline
  citations).
- Recording to `method_state["evidence_log"]`.
- Inline transcript annotation of grounded / reasoning-based turns.
- Minimal "Attach evidence" UI affordance for human participants.
- Conclusion enumeration wired into Double Crux (`test_crux` is the sole
  opted-in phase).

**Out of scope (follow-ups):**
- Per-claim citation mapping (which specific claim rests on which source).
- Hard rejection / retry of ungrounded turns.
- Extending `ToolCallRecord` to carry structured source metadata.
- Rich source-picker UI beyond the minimal marker inserter.
- Knowledge-graph grounding (no KG participant tool exists today).
- Opting in Adversarial Collaboration `gather_evidence` and ACH
  `present_evidence` — deferred until the mechanism is proven on `test_crux`.

## Architecture

Evidence tracking is **cross-cutting**, not method-specific. Classification and
recording run in the **flow layer** as a generic post-step after the existing
`process_response` / `process_structured_response` call — *not* threaded into
each phase handler. This keeps the 67-handler API unchanged and confines the
feature to one new module.

### New module: `consensus/evidence.py`

Holds all feature logic:

- `EVIDENCE_TOOL_NAMES: frozenset[str]` — the citation-bearing participant
  tools: `{doc_ask, doc_get_text, doc_summary, doc_get_sections,
  doc_get_chapter, web_search, fetch_webpage}`. Excludes `doc_add`, `doc_list`,
  `doc_get_length` (management/navigation, not evidence retrieval).
- `GroundingResult` dataclass: `grounded: bool`, `sources: list[dict]`.
- `classify_turn_grounding(content: str, tool_calls: list[ToolCallRecord]) ->
  GroundingResult` — pure function, unit-testable in isolation.
- `EVIDENCE_MARKER_RE` — regex for the inline `[evidence: …]` marker.
- `URL_RE` — regex for bare `http(s)://…` URLs.
- `record_and_annotate_evidence(discussion, entity, turn_number, content,
  tool_calls) -> str` — classifies, appends to
  `method_state["evidence_log"]`, and returns the (possibly annotated) display
  content. Called by the flow layer only when the active phase has
  `track_evidence=True`.
- `build_evidence_summary(state: dict) -> dict` — reads `evidence_log`, returns
  `{grounded: [...], reasoning_based: [...], counts: {...}}` for conclusion use.

### Changed: `consensus/methods/base.py`

Add one field to the `Phase` dataclass:

```python
track_evidence: bool = False  # opt-in evidence provenance tracking (#28)
```

Default `False` — no behavior change for any existing phase.

## Grounding classification

`classify_turn_grounding(content, tool_calls)` returns grounded/ungrounded plus
the sources it rests on. Two detection paths, evaluated together (a turn is
grounded if *either* path finds a citation):

### Tool path (primarily AI)

Grounded if the turn made **≥1 successful** (`is_error is False`) call to a tool
whose `tool_name` is in `EVIDENCE_TOOL_NAMES`. Sources are derived **from
`(tool_name, arguments)`**, robustly — *not* by parsing `result` strings:

- `doc_ask` / `doc_get_text` / `doc_summary` / `doc_get_sections` /
  `doc_get_chapter` → `{"type": "document", "document_id": <id>, "detail":
  <question or range>, "tool": <tool_name>}`.
- `web_search` → `{"type": "web_search", "query": <query>, "tool":
  "web_search"}`.
- `fetch_webpage` → `{"type": "web", "url": <url>, "tool": "fetch_webpage"}`.

Rationale: `ToolCallRecord` persists only `tool_name`, `arguments`, `result`
(string), `is_error` — it drops the `ToolResult.metadata` that carries richer
source detail. `arguments` is the reliable, structured signal.

### Inline path (human, and any turn)

Grounded if the content contains ≥1 parseable citation:

- An explicit marker `[evidence: doc:<N> | <url> | "<quote>"]` — the form the
  "Attach evidence" UI inserts. Parsed into
  `{"type": "inline", "ref": <parsed value>}`.
- A bare `http(s)://…` URL → `{"type": "web", "url": <url>}`.

### Result

If neither path yields a source, the turn is **ungrounded** (recorded and
annotated, never rejected).

## Recording and annotation

When the active phase has `track_evidence=True`, the flow post-step calls
`record_and_annotate_evidence`, which:

1. Appends to `method_state["evidence_log"]` an entry:
   `{"entity_id", "entity_name", "turn", "phase", "grounded", "sources"}`.
2. Returns display content annotated via the existing
   `ProcessedResponse.display_content` "appended annotation" convention:
   - grounded → a compact sources footer (e.g. `— sources: document 3;
     https://…`);
   - ungrounded → a compact note (e.g. `— reasoning-based contribution (no
     cited evidence)`).

Annotation is presentational and non-blocking; the transcript itself carries
the grounded / reasoning-based distinction.

## Flow integration

Two call sites, both gated on the active phase's `track_evidence`:

- `app_discussion_flow.generate_ai_turn` — after the existing
  `processed = method.process_response(...)` /
  `process_structured_response(...)`, when the phase is evidence-tracked, pass
  `resp.tool_calls` (empty for pass turns) through
  `record_and_annotate_evidence` and use its returned content. Persist
  `method_state` (already persisted on this path).
- `app_discussion_flow.submit_human_message` — same post-step with an **empty**
  tool-call list, so human grounding is decided by the inline path.

A small helper resolves the active `Phase` object from the current phase name
(the handler's `phase`) to read `track_evidence`.

## Conclusion enumeration (Double Crux)

`test_crux` is the sole opted-in phase in this slice. `build_evidence_summary`
is wired into Double Crux two ways:

- **Artifact:** `build_crux_map` (`resolve_crux.py`) gains an `evidence` field
  carrying the summary, so the machine-readable `crux_map` records the
  evidentiary basis.
- **Conclusion prompt:** the existing "Evidence" section of
  `DoubleCrux.get_conclusion_prompt` (`double_crux.py`) enumerates the actual
  grounded sources and notes which contributions were reasoning-based, instead
  of relying on model memory.

## Human "Attach evidence" UI (minimal)

For evidence-tracked phases, the participant input area gains an **"Attach
evidence"** control that inserts an `[evidence: …]` marker into the message
text. It only produces the parseable marker the classifier already understands;
no rich source-picker. Frontend lives in `consensus/static/` alongside the
existing input handling. This gives humans a concrete evidence-provision path
under the same standard as AI participants. The button is shown only while a
`track_evidence` phase is active — `get_state` exposes a `track_evidence_phase`
boolean for the current phase, and the frontend gates visibility on it so the
marker is never offered where it would be inert text.

## Grounding vs. verification

"Grounded" means a citation is *present* — a successful evidence-tool call, a
bare URL, or an `[evidence: …]` marker — not that the cited source was fetched,
read, or actually supports the claim. A turn that only mentions a URL in prose
is classified grounded. The tracker distinguishes grounded from reasoning-based
contributions and makes that visible; confirming the evidence backs the claim is
deliberately out of scope (consistent with the soft-by-design decision).

## Testing

- **Unit — `tests/test_evidence.py`:**
  - `classify_turn_grounding`: AI evidence-tool call → grounded with correct
    source; errored evidence-tool call → not grounded; non-evidence tool
    (`doc_list`) → not grounded; human inline URL → grounded; `[evidence:]`
    marker → grounded with parsed ref; empty/no citation → ungrounded.
  - Source extraction from `arguments` for each evidence tool.
  - `build_evidence_summary` output shape (grounded / reasoning_based / counts).
- **Flow — extend Double Crux tests:** drive `test_crux` through
  `complete_turn` (real-pipeline convention, human moderator +
  `moderator_summary`, no network):
  - a turn with a stubbed `doc_ask` tool call records grounded + sources in
    `evidence_log`;
  - a bare-reasoning turn records ungrounded + carries the annotation;
  - a human turn with an inline URL records grounded.
- **Regression:** phases without `track_evidence` produce no `evidence_log` and
  unchanged display content.

## Conventions honored

- No magic numbers; any caps/limits as named constants
  (`docs/llm/golden_rules.md`).
- New module and functions carry docstrings + type hints; files stay under
  ~500 lines.
- TDD: failing tests first.
- `uv` only.
- All grounded/ungrounded classification is computed in code, never by the
  model — consistent with the "numbers computed in code" contract of the scored
  methods.
