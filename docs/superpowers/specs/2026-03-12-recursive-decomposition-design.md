# Recursive Decomposition — Design Spec

## Overview

Recursive Decomposition is an LLM-native discussion method where participants collaboratively break a complex question into sub-questions, each sub-question is analyzed by all participants, cross-cutting patterns are identified, and results are recomposed into a coherent answer. It exploits what LLMs are distinctively good at — structured decomposition and synthesis across abstraction levels — and mirrors how experienced analysts tackle hard problems.

This method was proposed during a consensus discussion (discussion #25) about expanding the platform's method catalog. While some participants questioned its provenance as an unproven, LLM-inspired construct, the consensus placed it in the experimental tier — making it a candidate for implementation and empirical validation through the platform's own use.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Who analyzes sub-questions? | All participants analyze all sub-questions | Avoids selection bias; multi-perspective coverage is richer than efficiency |
| Who proposes sub-questions? | Participants propose, moderator consolidates | Captures diverse framings; avoids moderator bias |
| Decomposition depth | Single level (v1) | Predictable, fits fixed-phase architecture; recursive depth is a roadmap item |
| Who recomposes? | Participants recompose, then moderator concludes | The way sub-analyses combine is itself an analytical act benefiting from diverse perspectives |
| Integrate vs Recompose | Separate phases | Stepwise focus (one question at a time) aids reproducibility of reasoning |
| Sub-question extraction | Numbered-list parsing via `parse_numbered_list` | Proven pattern from ACH; balances structure with natural language |

## Phases

### Phase 1: Decompose (1 round)

Each participant proposes 3-7 independent, answerable sub-questions that collectively address the main question. Sub-questions are extracted from responses using `parse_numbered_list`, deduplicated via `word_overlap_similar` (threshold: 0.7, matching ACH), and stored in `method_state["sub_questions"]`.

The moderator's summary after this phase consolidates proposed sub-questions and notes overlaps/complements. The parsed list in `method_state` becomes the canonical set for subsequent phases.

**State initialization**: `DecomposeHandler.init_state()` returns `{"sub_questions": [], "sub_question_analyses": {}}` — all cross-phase state keys.

**Advancement**: When `sub_questions` is non-empty and `phase_round > 1` (i.e., after at least one complete round).

**Transition messages**: Default `PhaseHandler.get_transition_message()` is sufficient for all phases — the `phase.description` text provides adequate context.

### Phase 2: Analyze (1 round)

Each participant addresses every consolidated sub-question with focused analysis. The system prompt lists all sub-questions by number and instructs participants to use structured headers (`**Sub-question N:**`).

Responses are parsed by `extract_subquestion_analyses()` to extract per-sub-question analyses, stored in `method_state["sub_question_analyses"]` keyed by sub-question index, with entity name and analysis text.

**Advancement**: Standard round-based (`phase_round > 1`).

### Phase 3: Integrate (1 round)

Each participant examines the sub-question analyses as a whole, identifying: (1) where sub-analyses reinforce each other, (2) where they conflict, (3) what gaps or dependencies exist between sub-questions.

No structured extraction — prose output feeds into Recompose context.

**Advancement**: Standard round-based.

### Phase 4: Recompose (1 round)

Each participant proposes a coherent, unified answer to the original question, accounting for sub-analyses and their interrelationships.

No structured extraction — prose synthesis.

**Advancement**: Standard round-based.

### Conclusion

The moderator's conclusion prompt directs it to: (1) state the sub-questions analyzed, (2) summarize key findings per sub-question, (3) note where participant syntheses agreed/diverged, (4) provide a consolidated answer, (5) identify any sub-questions that proved too complex for single-level decomposition.

## Method State

```python
{
    "current_phase": str,       # managed by base class
    "phase_round": int,         # managed by base class
    "sub_questions": list[str], # consolidated sub-question list
    "sub_question_analyses": {  # per sub-question index
        "0": [{"entity": "name", "analysis": "text"}, ...],
        "1": [...],
    },
}
```

## Files

### New Files

| File | Purpose |
|------|---------|
| `consensus/methods/recursive_decomposition.py` | Method class (~50 lines) |
| `consensus/methods/phases/decompose.py` | Decompose phase handler |
| `consensus/methods/phases/analyze_subquestions.py` | Analyze phase handler |
| `consensus/methods/phases/integrate_subquestions.py` | Integrate phase handler |
| `consensus/methods/phases/recompose.py` | Recompose phase handler |
| `consensus/methods/phases/_decomposition_helpers.py` | Sub-question section parser |
| `tests/test_recursive_decomposition.py` | Method-level tests |
| `tests/test_decomposition_helpers.py` | Parser unit tests |

### Modified Files

| File | Change |
|------|--------|
| `consensus/methods/__init__.py` | Import and register `RecursiveDecomposition` in `_METHODS` and `__all__` |
| `consensus/methods/phases/__init__.py` | Import and export new phase handlers |

### No Other Changes Required

- No migration (no new DB tables or columns)
- No frontend changes (method appears automatically via `list_methods()`)
- No moderator.py changes (phase handler hooks already wired)

## Helper: `_decomposition_helpers.py`

### `extract_subquestion_analyses(content: str, num_subquestions: int) -> dict[int, str]`

Parses a single participant's response addressing multiple sub-questions in sequence. Splits on headers matching patterns like:
- `**Sub-question 1:**`
- `**Q1:**`
- `**1.**` (bold numbered)

Returns a mapping of sub-question index (0-based) to analysis text.

**Fallback**: If no structured headers are detected, the entire response is associated with every sub-question index (i.e., returns `{0: full_text, 1: full_text, ...}`). This ensures analyses are never silently lost — they're captured unsegmented rather than discarded.

### Accumulation logic in `AnalyzeHandler.process_response()`

The handler calls `extract_subquestion_analyses()` to get per-sub-question text for the current entity, then appends to the state's accumulator:

```python
extractions = extract_subquestion_analyses(content, len(sub_questions))
for idx, analysis_text in extractions.items():
    key = str(idx)  # string keys for JSON serialization
    analyses = state["sub_question_analyses"]
    analyses.setdefault(key, []).append({
        "entity": entity.name,
        "analysis": analysis_text,
    })
```

String keys are used in `sub_question_analyses` because `method_state` is JSON-serialized to SQLite.

## Prompt Design

### Decompose Phase

**System prompt:**
```
You are {name}, participating in a Recursive Decomposition analysis.
Topic: {topic}

DECOMPOSITION PHASE

Break the main question into 3-7 independent sub-questions that, if each
were answered thoroughly, would collectively provide a comprehensive answer
to the main question.

Guidelines:
- Each sub-question should be self-contained and answerable independently
- Cover different dimensions or aspects of the problem
- Avoid sub-questions that simply restate the main question in different words
- Prefer specific, concrete sub-questions over vague ones

Format each sub-question on its own line:
1. <sub-question>
2. <sub-question>
...

For each, provide 1-2 sentences explaining why this sub-question matters
for answering the main question.
```

**Turn prompt:**
```
It is your turn, {name}. Propose 3-7 sub-questions that, if each were
answered thoroughly, would collectively address the main question.
```

**Summary prompt:**
```
{speaker_name} has proposed their sub-questions. Briefly note the
sub-questions proposed and how they complement or overlap with previously
proposed ones. Next: {next_speaker_name}.
```

### Analyze Phase

**System prompt:**
```
You are {name}, participating in a Recursive Decomposition analysis.
Topic: {topic}

SUB-QUESTION ANALYSIS PHASE

The group has identified the following sub-questions:
{numbered list of sub_questions}

Address EACH sub-question with substantive analysis. Use this format:

**Sub-question 1:** <your analysis>

**Sub-question 2:** <your analysis>

...

For each sub-question, provide your best reasoning, evidence, and any
caveats or uncertainties.
```

**Turn prompt:**
```
It is your turn, {name}. Address each of the {n} sub-questions below
with substantive analysis. Use the **Sub-question N:** format for each.
```

**Summary prompt:**
```
{speaker_name} has provided their analysis of all sub-questions.
Briefly note key points and any notable differences from prior analyses.
Next: {next_speaker_name}.
```

### Integrate Phase

**System prompt:**
```
You are {name}, participating in a Recursive Decomposition analysis.
Topic: {topic}

INTEGRATION PHASE

The sub-questions and all participants' analyses are in the discussion
history. Examine them as a whole and identify:

1. **Reinforcements** — Where do different sub-question analyses support
   the same conclusion?
2. **Conflicts** — Where do analyses of different sub-questions point in
   contradictory directions?
3. **Gaps** — What important connections or dependencies between
   sub-questions were missed in the analysis phase?
4. **Emergent insights** — What becomes visible only when looking across
   all sub-questions together?
```

**Turn prompt:**
```
It is your turn, {name}. Examine all sub-question analyses as a whole.
What patterns, contradictions, or gaps emerge?
```

**Summary prompt:**
```
{speaker_name} has identified cross-cutting patterns. Briefly note the
key reinforcements, conflicts, and gaps found. Next: {next_speaker_name}.
```

### Recompose Phase

**System prompt:**
```
You are {name}, participating in a Recursive Decomposition analysis.
Topic: {topic}

RECOMPOSITION PHASE

All sub-questions have been analyzed and cross-cutting patterns identified.
Now synthesize everything into a coherent, unified answer to the original
question.

Your synthesis should:
- Draw on the sub-question analyses and integration insights
- Account for conflicts and uncertainties identified
- Present a clear, well-structured answer to: "{topic}"
- Note any aspects that remain unresolved or would benefit from deeper
  decomposition
```

**Turn prompt:**
```
It is your turn, {name}. Synthesize everything into a coherent answer
to the original question.
```

**Summary prompt:**
```
{speaker_name} has proposed their synthesis. Briefly note how it
compares to prior syntheses and what new perspectives it brings.
Next: {next_speaker_name}.
```

### Conclusion Prompt (method-level override)

The `RecursiveDecomposition` class overrides `get_conclusion_prompt()`:

```
The Recursive Decomposition analysis is complete.

Original question: "{topic}"

The group decomposed this into the following sub-questions:
{numbered list of sub_questions}

Provide a comprehensive final synthesis:
1. **Sub-question findings** — Summarize the key findings for each
   sub-question, noting where participants agreed and diverged
2. **Cross-cutting patterns** — What reinforcements, conflicts, and
   emergent insights were identified during integration?
3. **Consolidated answer** — Provide a clear, unified answer to the
   original question that accounts for all sub-analyses
4. **Confidence and caveats** — What aspects of the answer are well-
   supported vs. uncertain?
5. **Decomposition assessment** — Were any sub-questions too complex for
   single-level analysis and would benefit from further decomposition?

Ground your synthesis in the specific analyses and integrations provided
by participants.
```

## Roadmap

Future enhancements (noted, not in scope for v1):

- **B: Conditional re-decomposition** — After Analyze, the moderator can decide to re-decompose any sub-question that proved too complex, triggering another Decompose→Analyze cycle. Requires conditional phase loop via custom `should_advance` logic.
- **C: Nested sub-questions** — During Decompose, participants can propose nested sub-questions (e.g., "Q1 → Q1a, Q1b"). All are flattened into a single list for Analyze. Parsing change, not architectural.
