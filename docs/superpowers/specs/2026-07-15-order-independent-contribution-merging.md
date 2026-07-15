# Order-independent contribution merging (#42) — Design

_Date: 2026-07-15 · Issue: #42 · Status: approved, pending implementation plan_

## Summary

Every phase that merges near-duplicate participant contributions by word
overlap currently does so **incrementally and greedily**: the first-arriving
phrasing founds a bucket and owns its display label permanently, and each later
near-duplicate is matched against whichever buckets happen to exist so far. Two
consequences, both inherent to greedy first-match merging (issue #42):

1. **First-name-wins** — the first-submitted phrasing owns the merged item; a
   later, better-phrased near-duplicate can never rename it, so a refinement
   round cannot repair a poor initial label.
2. **Order dependence** — with chained similarity (`A~B`, `B~C`, `A≁C`) the
   resulting grouping depends on submission order.

This design replaces greedy incremental merging at the three display/aggregate
sites with **store-raw, then cluster-then-label**: every raw contribution is
retained untouched, and the merged view is derived by clustering the *whole*
set deterministically. Grouping and labeling then depend only on the set of
contributions and the symmetric similarity relation — never on order.

Severity of #42 is low (weights/votes already aggregate correctly; only the
display name and grouping granularity are affected), so the fix is a shared
canonicalisation helper adopted by the merge sites — not a re-architecture and
not an LLM-in-the-loop rename turn (which would cut against the catalog
convention that all scored quantities are computed in code, never by the model).

## Scope

**In scope — the three display/aggregate merge sites** (owner decision,
2026-07-15):

- `_ngt_helpers.record_ideas` (NGT `generate_ideas` phase) → `state["ideas"]`
- `_tot_helpers.record_thoughts` (ToT `propose_thoughts` phase) →
  `state["thoughts"]`
- `_mcda_helpers.record_criteria` (MCDA `weight_criteria` phase) →
  `state["criteria"]` (dicts carrying `weight_votes`)

**Out of scope:**

- The pure `if not any(word_overlap_similar(...))` dedup gates in
  `decompose.py`, `hypothesize.py`, `surface_assumptions.py` — they drop
  duplicate hypotheses/subquestions feeding a downstream LLM phase and own no
  displayed, aggregated label. Order-dependence there is even lower severity.
- `_crux_helpers.record_cruxes` — its similarity check is **per-entity**
  (cross-entity claim overlap is deliberately kept as the shared-crux signal),
  so #42 barely applies; leave it unchanged.
- `define_criteria.py` — belongs to a *different* method, stores a list of
  strings, and dedups by exact `c not in existing` (not word overlap). Not a
  #42 site.
- A moderator rename/regroup turn and any LLM-driven labeling (rejected: puts
  the model in the loop for a quantity the catalog computes in code).

## Approach

### 1. Shared primitives in `consensus/methods/parsing.py`

- **`word_overlap_ratio(a, b) -> float`** — extract the existing ratio math
  (`|w1 ∩ w2| / max(|w1|, |w2|)`, `0.0` when either token set is empty).
  `word_overlap_similar(a, b, threshold=0.7)` becomes
  `word_overlap_ratio(a, b) > threshold` — behavior identical, so the ~8
  existing callers (including the out-of-scope dedup gates) are unaffected.

- **`cluster_by_similarity(members, text_of, threshold=0.7) -> list[list]`** —
  connected components over the similarity graph (union-find or BFS), where an
  edge exists between two members whose texts are `word_overlap_similar` at
  `threshold`. Returns clusters as lists of the original member objects.
  - Clusters are ordered by **ascending minimum original index**; members
    within a cluster keep their original order.
  - Grouping is **order-independent**: it depends only on the set of members
    and the symmetric similarity relation, not on their order.
  - Grouping is **transitive** by construction: `A~B, B~C, A≁C` yields one
    cluster. This is the deterministic price of order-independence and is
    documented in the docstring as the intended semantics.

- **`canonical_index(members, text_of) -> int`** — the **medoid**:
  `argmax_i Σ_j word_overlap_ratio(text_i, text_j)` over the cluster's members.
  Tie-break: longest text, then lexicographically smallest. Fully
  deterministic. Returns an index into `members`.

The shared helper owns *grouping + canonical-label selection*; each caller owns
building its own view dict (differing payloads). This keeps the shared surface
small and independently testable.

### 2. Refactor the three call sites

Each `record_*` helper changes from "greedily merge into `state["<x>"]`" to:

1. Clean and filter this turn's texts using the **unchanged** existing rules
   (`MIN_IDEA_LENGTH` / `MIN_THOUGHT_LENGTH` / `MIN_CRITERION_LENGTH`,
   `rstrip('.')`, MCDA weight coercion + clamp to `[WEIGHT_MIN, WEIGHT_MAX]`).
2. Append this turn's cleaned contributions as raw entries to a new
   `state["<x>_raw"]` list (each entry: `{entity_id, entity_name, text}`, plus
   `weight` for criteria).
3. Recompute the derived view `state["<x>"]` from the full raw list via the
   shared helper. The derived view keeps the **exact same shape** downstream
   consumers already read:
   - ideas / thoughts: `{id, text, entity_id, entity_name}`
   - criteria: `{id, name, weight_votes}`
4. Return the derived clusters that contain at least one of this turn's
   newly-appended raw items (this replaces the old `accepted` / `touched`
   return, and is used for the turn's response display — now showing the
   canonical label).

Per-cluster derivation:

- `id` = cluster rank in the helper's anchor-index order (`1..n`).
- `text` / `name` = text of the cluster's `canonical_index` member (medoid).
- `entity_id` / `entity_name` = the **founder** = the min-original-index
  member's entity (preserves today's "founder owns the item" attribution).
- criteria `weight_votes` = `{str(entity_id): weight}` where each entity's
  weight is taken from its **latest** raw member in the cluster (preserves the
  existing last-write-wins refinement semantic: a later restatement replaces an
  entity's earlier weight for the same criterion).

### 3. Id stability

`id` must be stable at the moment the *next* phase begins referencing items by
label (`C2` in MCDA `score_options`, `T3` in ToT `score_thoughts` /
`expand_thoughts`). This holds because:

- Each `record_*` runs in **exactly one** collection phase
  (`record_ideas`→`generate_ideas`, `record_thoughts`→`propose_thoughts`,
  `record_criteria`→`weight_criteria`); no new raw items are added once the
  scoring phase starts, so the cluster set is frozen across the phase boundary.
- Within collection, MCDA refinement matches by **text similarity** (an entity
  restates the criterion), not by referring to an id, so mid-collection id
  drift is harmless.
- The anchor-index id rule is **append-stable**: a genuinely new cluster gets
  the next-higher id and existing clusters keep theirs. Ids only renumber when
  a bridging contribution merges two previously-separate clusters — which
  necessarily changes the count and is the correct outcome.

## Behavior changes

- **First-name-wins fixed**: the label is the medoid, so a better later
  phrasing can become canonical.
- **Order-independence**: permuting the submission order yields identical
  clusters, labels, and (MCDA) weight aggregation.
- NGT `record_ideas` and ToT `record_thoughts` previously **dropped**
  near-duplicates; they now retain all raw contributions but collapse each
  cluster to one canonical medoid. Observable effect: the surviving label is
  the medoid rather than the first arrival, grouping is order-independent, and
  the same-or-fewer displayed items still feed the downstream clustering
  (NGT) / scoring + pruning (ToT) unchanged in shape.

## Testing (TDD — failing tests first)

`tests/` additions/updates:

- **parsing**: `word_overlap_ratio` values + empty-set `0.0`;
  `cluster_by_similarity` order-independence (permuted input → identical
  clustering), transitive-closure chain (`A~B, B~C, A≁C` → one cluster),
  singletons, threshold boundary; `canonical_index` medoid selection and both
  tie-breaks (longest, then lexicographic).
- **record_ideas / record_thoughts / record_criteria**: order-independence
  (submit in different orders → identical final `state["<x>"]`); first-name-wins
  fixed (a better later phrasing becomes the label); MCDA weight aggregation
  preserved and order-independent; id determinism across the collection→scoring
  boundary; the new return value.
- **Regression**: the existing NGT / MCDA / ToT handler-level and structured-
  output suites stay green (shape of `state["<x>"]` is unchanged).

## Accepted trade-offs / notes

- **Cost**: `cluster_by_similarity` is `O(n²)` in cluster comparisons and is
  recomputed each turn (`O(n³)` over a phase). `n` is a handful of
  ideas/criteria/thoughts per phase, so this is negligible; recomputing per
  turn keeps the running display correct with the simplest code. Documented.
- **State size**: `state["<x>_raw"]` roughly doubles this phase's serialized
  `method_state`. Acceptable. It is a plain (non-`_`-prefixed) key, so it
  shares the same method-switch survival lifecycle as today's
  `ideas`/`criteria`/`thoughts` (i.e. not preserved across a Triage switch —
  unchanged from current behavior).
- **Transitive-closure merging** and **medoid tie-break determinism** are the
  two non-obvious semantics; both are documented in the `parsing.py` helper
  docstrings as the single source of truth.

## Project conventions honored

- `uv` only; TDD (failing test first); files under ~500 lines; docstrings +
  type hints mandatory; no magic numbers (thresholds/bounds remain named
  constants at the call sites). Deterministic aggregation stays in code, never
  in the model — consistent with the catalog-wide correctness contract.
