# Tree of Thoughts — Design (issue #26)

_Date: 2026-07-14.  Status: approved for implementation (autonomous
session; design decisions resolved from issue #26, the owner decisions
of 2026-07-12, and the NGT/MCDA/Double Crux precedents — each decision
below records its rationale so the owner can veto cheaply at PR
review)._

## Problem

The catalog's generative arm (NGT) creates and prioritises options in
one pass.  Issue #26 asks for an **LLM-native iterative explorer**:
generate parallel solution approaches, score them, prune to a beam,
deep-dive the survivors, and repeat until the beam stabilises or a
depth budget is spent — then synthesise.  It was blocked on
phase-machine loop support (#22), which is now merged.

## Method shape

Method `tree_of_thoughts` (`consensus/methods/tree_of_thoughts.py`),
five phases assembled from new handlers in
`consensus/methods/phases/` over a shared pure-function helper module
`_tot_helpers.py`:

```
propose → score → prune ──(continue)──→ expand
             ↑                             │
             └─────────────────────────────┘
                prune ──(converged | depth budget | degenerate)──→ synthesise → (end)
```

1. **PROPOSE** (`propose_thoughts.py`, participants, rounds=1) —
   each participant independently proposes 2–5 distinct solution
   approaches ("thoughts") via the forced `submit_thoughts` tool.
   Context is anonymised (Delphi-style, reusing `anonymise_content`)
   per the issue: "anonymised to avoid anchoring".  Same-author and
   cross-author near-duplicates are word-overlap deduped at record
   time (the MCDA `record_options` pattern — unlike Double Crux,
   cross-entity overlap carries no signal here).  Zero thoughts after
   `MAX_PROPOSE_ROUNDS` aborts the method (`generate_ideas.py`
   pattern) — every later phase needs candidates.

   Anonymisation holds for the **whole method** (the NGT precedent:
   every phase filters context through `anonymise_content`) — the
   propose turns replay in later phases' context, so dropping the
   filter after propose would leak authorship exactly when scoring
   needs to be blind.

2. **SCORE** (`score_thoughts.py`, participants, rounds=1) — every
   participant scores every *eligible* thought on the issue's three
   fixed dimensions — **feasibility, impact, risk** — each an integer
   `SCORE_MIN..SCORE_MAX` (1–5), via the forced `submit_thought_scores`
   tool.  Eligible = all thoughts on the first pass, the current beam
   on later passes (pruned branches are dead; validator rejects their
   labels).  Unlike MCDA there is no criteria-elicitation phase: the
   dimensions are fixed by the method, so the schema maps thought
   labels (`T1`, `T2`, ...; `additionalProperties`) to an object with
   three required integer properties.  Partial coverage is allowed
   (MCDA precedent); per-thought entries are complete by schema.
   Re-scoring merges per-thought (an entity's later submission
   replaces its own earlier scores for that thought only).

3. **PRUNE** (`prune_thoughts.py`, moderator-only, rounds=1) — the
   beam is computed **deterministically in code, never by the model**
   (the MCDA sensitivity / Double Crux belief-shift convention):
   composite per scorer = `feasibility + impact + (SCORE_MIN +
   SCORE_MAX − risk)` (risk inverted), mean over scorers, thoughts
   nobody scored default to the all-midpoint composite with a caveat,
   ties broken by thought id (deterministic).  **Scored thoughts
   always rank above unscored ones** — an invented default composite
   must never beat real data into the beam (partial coverage is a
   normal path, not an edge case).  Top `BEAM_WIDTH` (3) survive.  The moderator takes one free-text presentational turn
   (the `rank_ideas.py` / `analyse_sensitivity.py` pattern) explaining
   the cut.  Routing happens in this handler's `next_phase` (#22):
   - **converged** — the new **ordered** beam equals the previous
     iteration's ordered beam *and at least one score was recorded
     during the pass* → jump to `synthesise`.  (Ordered, not set,
     equality: eligibility restricts scoring to the previous beam, so
     the id *set* is necessarily unchanged after the first prune —
     reordering is the only movement re-scoring can produce, and a
     stable order means the deep-dives changed nothing.  The
     fresh-scores gate — `scores_by_pass`, stamped by
     `record_thought_scores` — prevents declaring convergence when a
     re-score pass recorded nothing at all: stability under zero new
     data proves nothing);
   - **depth budget** — `MAX_TOT_DEPTH` (3) prune passes done → jump
     to `synthesise`;
   - **degenerate** — fewer than 2 surviving thoughts (nothing to
     explore in parallel) → jump to `synthesise` with a caveat;
   - otherwise → linear to `expand`.
   The beam record (`beam_history` append) and, when routing to
   `synthesise`, the final `tot_artifact` are both written inside
   `next_phase` — the one hook that runs exactly once per pass.

4. **EXPAND** (`expand_thoughts.py`, participants, rounds=1) — each
   participant deep-dives the surviving thoughts via the forced
   `submit_expansions` tool: per beam thought, a `refinement` (how to
   strengthen/concretise it) and `obstacles` (what could make it
   fail).  Expansions are tagged with the current depth and shown in
   the next scoring pass's context so re-scores are informed by the
   deep-dive.  Thought texts themselves stay immutable — label
   stability is what makes re-scoring and convergence meaningful.
   `next_phase` always jumps back to `score` (the loop edge; the
   linear successor `synthesise` is only reachable via prune's jump).

5. **SYNTHESISE** (`synthesise_thoughts.py`, moderator-only,
   rounds=1) — the moderator presents the outcome (final beam, score
   trajectories, obstacles) in one free-text turn; the method then
   completes linearly.  `get_conclusion_prompt` builds the final
   synthesis instruction from the artifact.

## Loop accounting

`tot_depth` ≙ `len(beam_history)` (number of completed prune passes).
Worst case transitions: propose→score, then `MAX_TOT_DEPTH` passes of
score→prune→expand→score minus the final expand, plus prune→synthesise
= 1 + 3×3 − 1 + 1 ≈ 10, comfortably under the auto loop guard
(5 phases × 5 = 25).  `max_phase_entries` stays auto.

## Structured output (issue #23 pattern)

Three structured phases, each with a required `reasoning` field, JSON
Schemas in `_tot_helpers.py`, `validate_output` returning
human-readable errors, shared `record_*` helpers used by both the
structured and free-text paths, and `process_response` kept as the
human/fallback layer:

| Tool | Phase | Payload |
|------|-------|---------|
| `submit_thoughts` | propose | `thoughts: [str]` (min length gated), `reasoning` |
| `submit_thought_scores` | score | `scores: {T<n>: {feasibility, impact, risk}}` (labels validated against the eligible set), `reasoning` |
| `submit_expansions` | expand | `expansions: [{thought_id, refinement, obstacles?: [str]}]` (ids validated against the beam), `reasoning` |

Free-text fallbacks: numbered-list parse for propose
(`parse_numbered_list`), fenced/inline JSON-block parse for score and
expand (the `extract_scores` balanced-brace pattern).  Prune and
synthesise are free-text moderator phases (nothing to extract — all
numbers are computed in code).

## Outcome artifact

`method_state["tot_artifact"]` (mirrors MCDA's `decision_artifact` /
Double Crux's `crux_map`), built deterministically when prune routes
to synthesise:

```python
{
  "recommendation": {"id": int, "text": str, "composite": float},
  "converged": bool,          # beam stabilised (vs depth budget/degenerate)
  "stop_reason": "converged" | "depth_budget" | "degenerate",
  "depth": int,               # prune passes completed
  "final_beam": [{"id", "text", "composite", "scorer_count"}],
  "beam_history": [{"depth", "beam_ids", "ranking"}],
  "expansions": [...],        # depth-tagged deep-dives incl. obstacles
  "caveats": [str],           # e.g. unscored thoughts defaulted, zero scorers
}
```

## Method state keys (handler `init_state`, no collisions)

- `thoughts: list[dict]` — `{"id", "entity_id", "entity_name", "text"}` (propose)
- `thought_scores: dict[str, dict[str, dict[str, int]]]` — entity id →
  thought label → `{feasibility, impact, risk}` (score)
- `beam_history: list[dict]`; `tot_artifact: dict` (prune)
- `expansions: list[dict]` — `{"depth", "entity_id", "entity_name",
  "thought_id", "refinement", "obstacles"}` (expand)

All method-local; nothing needs adding to the `switch_discussion_method`
preserved set.

## Constants (no magic numbers)

`MAX_PROPOSE_ROUNDS = 3`, `BEAM_WIDTH = 3` (issue: "top 2-3"),
`MAX_TOT_DEPTH = 3`, `SCORE_MIN = 1`, `SCORE_MAX = 5`,
`DEFAULT_DIMENSION_SCORE = 3`, `MIN_THOUGHT_LENGTH = 10`,
`MIN_REFINEMENT_LENGTH = 10`, `SIMILARITY_THRESHOLD = 0.7`.

## Alternatives considered

- **Moderator-clustered consolidation (NGT's cluster phase) instead of
  record-time dedup** — rejected: the issue's five steps have no
  clustering stage; word-overlap dedup at record time (MCDA options
  precedent) keeps the method at five phases.  A noisy thought list is
  self-correcting here because scoring + pruning is itself the filter.
- **Model-chosen beam (moderator picks survivors via a structured
  verdict)** — rejected: the platform convention is that numbers and
  rankings are computed deterministically (MCDA, Double Crux belief
  shifts); a model-picked beam would be unauditable and add a
  structured phase + give-up cap for no gain.  The moderator still
  narrates the cut.
- **Reusing MCDA's `score_options` machinery directly** — rejected:
  MCDA scores dynamic criteria elicited in an earlier phase; ToT's
  dimensions are fixed, so a dedicated (much simpler) fixed-key schema
  beats threading a synthetic criteria list through `_mcda_helpers`.
  The *pattern* (labels, `additionalProperties`, midpoint defaults,
  partial coverage) is reused, per the issue's "reuse matrix-scoring
  machinery".
- **Convergence by leader stability (top-1 unchanged) or beam-set
  equality** — rejected: a stable leader with churning runners-up
  means exploration is still moving, and the beam *set* is vacuously
  stable once eligibility restricts scoring to the previous beam.
  Ordered-beam equality is the strictest fully deterministic test that
  can actually vary between passes.

## Testing

Per the HANDOVER conventions (real-pipeline emphasis):
- `tests/test_tot_helpers.py` — pure helper functions: validators,
  recording/dedup/merge, composite/beam computation (ties, unscored
  defaults, risk inversion), artifact building, formatting.
- `tests/test_phases_tot.py` — handler prompts, free-text fallbacks,
  advancement, abort, prune routing (loop / converged / depth budget /
  degenerate), turn orders, transition messages, method assembly,
  conclusion prompt, registry + taxonomy.
- `tests/test_tot_structured.py` — per-#23 conversion tests: specs,
  `validate_output` error surfaces, `process_structured_response`
  recording/display, `requires_structured_output` flags.

Known gap carried forward (HANDOVER already tracks it for NGT/MCDA/
Double Crux): no `complete_turn`-driven end-to-end flow test; ToT joins
that list rather than adding one method-specific e2e now.

## Documentation

`docs/devel/15-discussion-methods.md` (file list + method table) and
`docs/user_manual/05_discussion_methods.md` (method section + choosing
table), per the Double Crux precedent.  Recommender `_TAXONOMY` gains:
"Open-ended problem-solving by exploring, scoring, and iteratively
refining parallel solution paths → Tree of Thoughts".
