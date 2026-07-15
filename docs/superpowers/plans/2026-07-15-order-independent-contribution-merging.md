# Order-independent Contribution Merging (#42) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace greedy, order-dependent word-overlap merging at the four
display/aggregate contribution sites with a shared, deterministic
cluster-then-label step so grouping and labels no longer depend on submission
order (issue #42).

**Architecture:** Add three pure primitives plus one merge helper to
`consensus/methods/parsing.py` — `word_overlap_ratio` (continuous form behind
the existing `word_overlap_similar`), `cluster_by_similarity` (connected
components over the similarity graph, order-independent + transitive),
`canonical_index` (medoid label pick with deterministic tie-breaks), and
`cluster_text_contributions` (turns raw text contributions into the labelled
view). Each `record_*` helper stops merging greedily: it appends every raw
contribution to a new `state["<x>_raw"]` list and rebuilds the derived view
`state["<x>"]` (unchanged shape) from the whole raw set. Downstream consumers
are untouched.

**Tech Stack:** Python 3, `pytest`, `uv` for env/deps. No new dependencies.

## Global Constraints

- Package management: `uv` only, never `pip`.
- TDD: write the failing test first, watch it fail, then implement.
- Files stay under ~500 lines; every function has a docstring and type hints.
- No magic numbers: reuse the existing `SIMILARITY_THRESHOLD = 0.7`,
  `MIN_*_LENGTH`, `WEIGHT_MIN`/`WEIGHT_MAX`/`DEFAULT_WEIGHT` constants at each
  site — do not inline literals.
- All aggregation (clustering, medoid, weight votes) is computed in code, never
  by the model — this is the catalog-wide correctness contract.
- Run tests with `python -m pytest` from the repo root.
- Commit after each green step; reference `#42` in commit messages.

---

### Task 1: Shared clustering primitives in `parsing.py`

**Files:**
- Modify: `consensus/methods/parsing.py` (add imports; add
  `word_overlap_ratio`, refactor `word_overlap_similar`, add
  `cluster_by_similarity`, `canonical_index`, `cluster_text_contributions`)
- Test: `tests/test_methods_parsing.py`

**Interfaces:**
- Produces:
  - `word_overlap_ratio(a: str, b: str) -> float`
  - `word_overlap_similar(a: str, b: str, threshold: float = 0.7) -> bool`
    (behavior unchanged)
  - `cluster_by_similarity(members: list, text_of: Callable[[Any], str], threshold: float = 0.7) -> list[list]`
  - `canonical_index(members: list, text_of: Callable[[Any], str]) -> int`
  - `cluster_text_contributions(raw: list[dict], since: int = 0, text_key: str = "text", threshold: float = 0.7) -> tuple[list[dict], list[dict]]`
    returning `(view, touched)` where each view dict is
    `{"id": int, text_key: str, "entity_id": ..., "entity_name": ...}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_methods_parsing.py`. First extend the import block at the top
of the file to include the new names:

```python
import itertools

import pytest
from consensus.methods.parsing import (
    canonical_index,
    cluster_by_similarity,
    cluster_text_contributions,
    extract_json_block,
    parse_numbered_list,
    word_overlap_ratio,
    word_overlap_similar,
)
```

Then append these test classes to the end of the file:

```python
class TestWordOverlapRatio:
    def test_identical_is_one(self):
        assert word_overlap_ratio("alpha beta", "alpha beta") == 1.0

    def test_disjoint_is_zero(self):
        assert word_overlap_ratio("alpha beta", "gamma delta") == 0.0

    def test_empty_is_zero(self):
        assert word_overlap_ratio("", "alpha") == 0.0

    def test_partial_overlap(self):
        # {a, b, c} vs {a, b} -> 2 / max(3, 2) = 2/3
        assert word_overlap_ratio("a b c", "a b") == pytest.approx(2 / 3)

    def test_similar_still_thresholds_on_ratio(self):
        assert word_overlap_similar("a b c d e", "a b c d f")  # 4/5 > 0.7
        assert not word_overlap_similar("a b c d e", "a b c x y")  # 3/5


class TestClusterBySimilarity:
    def test_all_distinct_are_singletons(self):
        items = ["alpha beta gamma", "delta epsilon zeta", "eta theta iota"]
        clusters = cluster_by_similarity(items, text_of=lambda s: s)
        assert [len(c) for c in clusters] == [1, 1, 1]

    def test_merges_similar_pair(self):
        items = ["build a plugin marketplace for third parties",
                 "build a plugin marketplace for the third parties"]
        clusters = cluster_by_similarity(items, text_of=lambda s: s)
        assert len(clusters) == 1 and len(clusters[0]) == 2

    def test_transitive_chain_is_one_cluster(self):
        a = "one two three four five"
        b = "one two three four nine"   # shares 4/5 with a
        c = "one two three eight nine"  # shares 4/5 with b, 3/5 with a
        assert word_overlap_similar(a, b)
        assert word_overlap_similar(b, c)
        assert not word_overlap_similar(a, c)
        clusters = cluster_by_similarity([a, b, c], text_of=lambda s: s)
        assert len(clusters) == 1

    def test_grouping_is_order_independent(self):
        items = ["alpha beta gamma delta", "alpha beta gamma epsilon",
                 "totally different words here"]
        seen = []
        for perm in itertools.permutations(items):
            clusters = cluster_by_similarity(list(perm), text_of=lambda s: s)
            seen.append({frozenset(c) for c in clusters})
        assert all(s == seen[0] for s in seen)

    def test_clusters_ordered_by_min_index(self):
        items = ["zzz distinct singleton", "aaa shared cluster text",
                 "aaa shared cluster text too"]
        clusters = cluster_by_similarity(items, text_of=lambda s: s)
        assert clusters[0] == ["zzz distinct singleton"]


class TestCanonicalIndex:
    def test_single_member(self):
        assert canonical_index(["only one here"], text_of=lambda s: s) == 0

    def test_medoid_is_most_central(self):
        members = ["cost total ownership money", "cost total ownership",
                   "cost total spend"]
        assert canonical_index(members, text_of=lambda s: s) == 1

    def test_tie_breaks_to_longest(self):
        members = ["total cost of ownership", "total cost of ownership now"]
        assert canonical_index(members, text_of=lambda s: s) == 1

    def test_tie_breaks_lexicographically_when_same_length(self):
        members = ["bbbb cost", "aaaa cost"]
        assert canonical_index(members, text_of=lambda s: s) == 1


class TestClusterTextContributions:
    def test_view_labels_and_touched(self):
        raw = [
            {"entity_id": 1, "entity_name": "Alice",
             "text": "shared idea alpha beta"},
            {"entity_id": 2, "entity_name": "Bob",
             "text": "distinct thing entirely here"},
            {"entity_id": 2, "entity_name": "Bob",
             "text": "shared idea alpha beta too"},
        ]
        view, touched = cluster_text_contributions(raw, since=1)
        assert [v["id"] for v in view] == [1, 2]
        assert view[0]["entity_name"] == "Alice"      # founder = min index
        assert view[0]["text"] == "shared idea alpha beta too"  # medoid
        assert len(touched) == 2                       # both clusters have idx>=1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_methods_parsing.py -q`
Expected: FAIL with `ImportError: cannot import name 'word_overlap_ratio'`.

- [ ] **Step 3: Implement the primitives**

In `consensus/methods/parsing.py`, change the typing import line:

```python
from typing import Any, Callable, Optional, Union
```

Replace the existing `word_overlap_similar` function (lines 59-69) with the
ratio + thin predicate, then add the three new helpers immediately after:

```python
def word_overlap_ratio(a: str, b: str) -> float:
    """Fraction of the larger token set shared by two strings (0.0-1.0).

    Tokens are whitespace-split and lowercased.  Returns 0.0 when either
    string has no tokens.  This is the continuous form behind
    ``word_overlap_similar`` and the edge/centrality weight used by
    ``cluster_by_similarity`` and ``canonical_index``.
    """
    w1 = set(a.lower().split())
    w2 = set(b.lower().split())
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / max(len(w1), len(w2))


def word_overlap_similar(a: str, b: str, threshold: float = 0.7) -> bool:
    """Check if two strings are substantially similar by word overlap.

    Returns True if the Jaccard-like overlap ratio exceeds *threshold*.
    """
    return word_overlap_ratio(a, b) > threshold


def cluster_by_similarity(members: list,
                          text_of: Callable[[Any], str],
                          threshold: float = 0.7) -> list[list]:
    """Group *members* into clusters by word-overlap similarity.

    Two members share a cluster when their texts (via *text_of*) are
    ``word_overlap_similar`` at *threshold*; clusters are the connected
    components of that graph.  Grouping is **order-independent** — it
    depends only on the set of members and the symmetric similarity
    relation, not their order — and **transitive**: ``A~B`` and ``B~C``
    place A, B and C together even when A and C are not directly similar
    (this is the deterministic price of order-independence).  Clusters
    are returned ordered by the smallest original index they contain;
    members keep their original order within a cluster.
    """
    n = len(members)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:  # attach larger root to smaller -> root is the min index
            parent[max(ri, rj)] = min(ri, rj)

    texts = [text_of(m) for m in members]
    for i in range(n):
        for j in range(i + 1, n):
            if word_overlap_similar(texts[i], texts[j], threshold):
                union(i, j)

    groups: dict[int, list] = {}
    for idx, member in enumerate(members):
        groups.setdefault(find(idx), []).append(member)
    # dict insertion order == ascending component-min index (the min member
    # is the first of its component reached), so this is deterministic.
    return list(groups.values())


def canonical_index(members: list,
                    text_of: Callable[[Any], str]) -> int:
    """Index of the medoid of *members* — the most representative one.

    The medoid maximises total ``word_overlap_ratio`` to the other
    members (the phrasing most central to the group).  Ties break toward
    the longest text, then the lexicographically smallest, so the result
    is fully deterministic.  *members* must be non-empty.
    """
    texts = [text_of(m) for m in members]
    central = [sum(word_overlap_ratio(texts[i], texts[j])
                   for j in range(len(texts)) if j != i)
               for i in range(len(texts))]
    best = 0
    for i in range(1, len(texts)):
        key_i = (central[i], len(texts[i]))
        key_best = (central[best], len(texts[best]))
        if key_i > key_best or (key_i == key_best
                                and texts[i] < texts[best]):
            best = i
    return best


def cluster_text_contributions(
        raw: list[dict], since: int = 0,
        text_key: str = "text",
        threshold: float = 0.7) -> tuple[list[dict], list[dict]]:
    """Cluster raw text contributions into an order-independent view.

    *raw* is the full list of contribution dicts, each carrying
    *text_key* plus ``entity_id`` / ``entity_name``.  Returns
    ``(view, touched)``:

    * *view* — one dict per cluster,
      ``{"id", text_key, "entity_id", "entity_name"}`` — labelled with
      the cluster medoid (``canonical_index``) and attributed to its
      founder (the earliest, min-index member).  Ids are the cluster
      rank in min-index order.
    * *touched* — the subset of *view* whose cluster contains a
      contribution at index >= *since* (i.e. the current turn's
      additions), for the turn's response display.
    """
    groups = cluster_by_similarity(
        list(range(len(raw))),
        text_of=lambda i: raw[i][text_key],
        threshold=threshold)
    view: list[dict] = []
    touched: list[dict] = []
    for cid, group in enumerate(groups, 1):
        canon = group[canonical_index(
            group, text_of=lambda i: raw[i][text_key])]
        founder = raw[min(group)]
        item = {"id": cid, text_key: raw[canon][text_key],
                "entity_id": founder["entity_id"],
                "entity_name": founder["entity_name"]}
        view.append(item)
        if any(i >= since for i in group):
            touched.append(item)
    return view, touched
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_methods_parsing.py -q`
Expected: PASS (all new classes plus the pre-existing parsing tests).

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/parsing.py tests/test_methods_parsing.py
git commit -m "feat(methods): order-independent clustering primitives (#42)"
```

---

### Task 2: NGT `record_ideas` — cluster-then-label

**Files:**
- Modify: `consensus/methods/phases/_ngt_helpers.py` (import line 15;
  `record_ideas` at lines 132-159)
- Test: `tests/test_ngt_helpers.py` (`TestRecordIdeas`)

**Interfaces:**
- Consumes: `cluster_text_contributions` from Task 1.
- Produces: `record_ideas(state, entity, texts) -> list[dict]` — appends to
  `state["ideas_raw"]`, rebuilds `state["ideas"]` (shape unchanged:
  `{id, text, entity_id, entity_name}`), returns the clusters this turn's
  ideas landed in.

- [ ] **Step 1: Write the failing tests**

In `tests/test_ngt_helpers.py`, replace the `test_dedups_by_word_overlap`
method inside `class TestRecordIdeas` with the two methods below (leave the
other three methods in that class unchanged):

```python
    def test_merges_and_returns_touched_clusters(self):
        state: dict = {}
        record_ideas(state, _entity(1, "Alice"), IDEAS_PAYLOAD["ideas"])
        touched = record_ideas(
            state, _entity(2, "Bob"),
            ["Offer a self-serve onboarding checklist inside the product now",
             "Publish a searchable public knowledge base"],
        )
        # Bob's near-duplicate merges into Alice's checklist cluster; his
        # new idea founds its own -> three clusters total.
        assert len(state["ideas"]) == 3
        # Bob touched both the merged cluster and the new one.
        assert len(touched) == 2
        assert any("knowledge base" in c["text"] for c in touched)
        # First-name-wins is fixed: the medoid (Bob's longer phrasing)
        # labels the merged cluster.
        checklist = next(c for c in state["ideas"] if "checklist" in c["text"])
        assert checklist["text"].endswith("now")

    def test_grouping_is_order_independent(self):
        forward: dict = {}
        record_ideas(forward, _entity(1, "Alice"),
                     ["alpha beta gamma delta epsilon"])
        record_ideas(forward, _entity(2, "Bob"),
                     ["alpha beta gamma delta zeta"])
        reverse: dict = {}
        record_ideas(reverse, _entity(2, "Bob"),
                     ["alpha beta gamma delta zeta"])
        record_ideas(reverse, _entity(1, "Alice"),
                     ["alpha beta gamma delta epsilon"])
        assert ([i["text"] for i in forward["ideas"]]
                == [i["text"] for i in reverse["ideas"]])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_ngt_helpers.py::TestRecordIdeas -q`
Expected: FAIL — `test_merges_and_returns_touched_clusters` asserts
`len(touched) == 2` but the current code drops the duplicate and returns 1.

- [ ] **Step 3: Implement the refactor**

In `consensus/methods/phases/_ngt_helpers.py`, change the import on line 15:

```python
from ..parsing import cluster_text_contributions, extract_json_block
```

Replace the whole `record_ideas` function (lines 132-159) with:

```python
def record_ideas(state: dict, entity: Entity,
                 texts: list[str]) -> list[dict]:
    """Append this turn's ideas as raw contributions, rebuild the
    order-independent clustered view, and return the clusters this
    turn's ideas landed in.

    Every submission is retained in ``state["ideas_raw"]``; the merged
    view ``state["ideas"]`` is derived by clustering the whole raw set
    and labelling each cluster with its medoid, so grouping and label
    are independent of submission order (issue #42).  The clustering
    phase still merges whatever survives this coarse gate.  Shared by
    the free-text and structured-output paths (issue #23).
    """
    raw = state.setdefault("ideas_raw", [])
    since = len(raw)
    for text in texts:
        cleaned = str(text).strip().rstrip('.')
        if len(cleaned) < MIN_IDEA_LENGTH:
            continue
        raw.append({"entity_id": entity.id, "entity_name": entity.name,
                    "text": cleaned})
    view, touched = cluster_text_contributions(
        raw, since=since, threshold=SIMILARITY_THRESHOLD)
    state["ideas"] = view
    return touched
```

- [ ] **Step 4: Run the site's tests to verify they pass**

Run: `python -m pytest tests/test_ngt_helpers.py tests/test_phases_ngt.py tests/test_ngt_structured.py -q`
Expected: PASS. (`TestRecordIdeas`'s other three methods still pass — distinct
ideas still form one cluster each, ids stay `[1, 2]`, trailing periods are
stripped, short items dropped.)

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/_ngt_helpers.py tests/test_ngt_helpers.py
git commit -m "fix(ngt): order-independent idea merging via medoid labels (#42)"
```

---

### Task 3: ToT `record_thoughts` — cluster-then-label

**Files:**
- Modify: `consensus/methods/phases/_tot_helpers.py` (import line 15;
  `record_thoughts` at lines 187-217)
- Test: `tests/test_tot_helpers.py` (`TestRecordThoughts`)

**Interfaces:**
- Consumes: `cluster_text_contributions` from Task 1.
- Produces: `record_thoughts(state, entity, texts) -> list[dict]` — appends to
  `state["thoughts_raw"]`, rebuilds `state["thoughts"]` (shape unchanged:
  `{id, text, entity_id, entity_name}`), returns clusters touched this turn.

- [ ] **Step 1: Write the failing tests**

In `tests/test_tot_helpers.py`, replace `test_dedups_word_overlap_across_entities`
inside `class TestRecordThoughts` with the two methods below (leave the other
three methods in that class unchanged):

```python
    def test_merges_across_entities_and_returns_touched(self):
        state: dict = {}
        record_thoughts(state, _entity(1, "Alice"),
                        ["Build a plugin marketplace for third parties"])
        touched = record_thoughts(state, _entity(2, "Bob"), [
            "Build a plugin marketplace for the third parties",
            "Rewrite the core engine in Rust for performance",
        ])
        assert [t["id"] for t in state["thoughts"]] == [1, 2]
        assert len(touched) == 2
        assert any(t["text"].startswith("Rewrite the core engine")
                   for t in touched)

    def test_grouping_is_order_independent(self):
        forward: dict = {}
        record_thoughts(forward, _entity(1, "Alice"),
                        ["scale gradually through many partner networks"])
        record_thoughts(forward, _entity(2, "Bob"),
                        ["scale gradually through several partner networks"])
        reverse: dict = {}
        record_thoughts(reverse, _entity(2, "Bob"),
                        ["scale gradually through several partner networks"])
        record_thoughts(reverse, _entity(1, "Alice"),
                        ["scale gradually through many partner networks"])
        assert ([t["text"] for t in forward["thoughts"]]
                == [t["text"] for t in reverse["thoughts"]])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_tot_helpers.py::TestRecordThoughts -q`
Expected: FAIL — `test_merges_across_entities_and_returns_touched` asserts
`len(touched) == 2` but the current code drops the duplicate and returns 1.

- [ ] **Step 3: Implement the refactor**

In `consensus/methods/phases/_tot_helpers.py`, change the import on line 15:

```python
from ..parsing import cluster_text_contributions
```

Replace the whole `record_thoughts` function (lines 187-217) with:

```python
def record_thoughts(state: dict, entity: Entity,
                    texts: list[str]) -> list[dict]:
    """Append this turn's thoughts as raw contributions, rebuild the
    order-independent clustered view, and return the clusters this
    turn's thoughts landed in.

    Every submission is retained in ``state["thoughts_raw"]``; the merged
    view ``state["thoughts"]`` is derived by clustering the whole raw set
    and labelling each cluster with its medoid, so grouping and label are
    independent of submission order (issue #42).  Scoring + pruning
    filters whatever survives this coarse gate.  Shared by the free-text
    and structured-output paths (issue #23).
    """
    raw = state.setdefault("thoughts_raw", [])
    since = len(raw)
    for text in texts:
        cleaned = str(text).strip()
        if cleaned.endswith(".") and not cleaned.endswith(".."):
            cleaned = cleaned[:-1]  # lone full stop, not an ellipsis
        if len(cleaned) < MIN_THOUGHT_LENGTH:
            continue
        raw.append({"entity_id": entity.id, "entity_name": entity.name,
                    "text": cleaned})
    view, touched = cluster_text_contributions(
        raw, since=since, threshold=SIMILARITY_THRESHOLD)
    state["thoughts"] = view
    return touched
```

- [ ] **Step 4: Run the site's tests to verify they pass**

Run: `python -m pytest tests/test_tot_helpers.py tests/test_phases_tot.py tests/test_tot_structured.py -q`
Expected: PASS. (`TestRecordThoughts`'s other three methods still pass — the
ellipsis-vs-trailing-period strip is preserved, short thoughts dropped,
distinct thoughts keep ids `[1, 2]`.)

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/_tot_helpers.py tests/test_tot_helpers.py
git commit -m "fix(tot): order-independent thought merging via medoid labels (#42)"
```

---

### Task 4: MCDA `record_options` — cluster-then-label

**Files:**
- Modify: `consensus/methods/phases/_mcda_helpers.py` (import line 16;
  `record_options` at lines 198-224)
- Test: `tests/test_mcda_helpers.py` (`TestRecordOptions`)

**Interfaces:**
- Consumes: `cluster_text_contributions` from Task 1.
- Produces: `record_options(state, entity, texts) -> list[dict]` — appends to
  `state["options_raw"]`, rebuilds `state["options"]` (shape unchanged:
  `{id, text, entity_id, entity_name}`; `id` is referenced downstream as
  `O1..On`), returns clusters touched this turn.

- [ ] **Step 1: Write the failing test**

In `tests/test_mcda_helpers.py`, append this method to `class TestRecordOptions`
(keep the existing three methods):

```python
    def test_merging_is_order_independent_and_medoid_labelled(self, alice,
                                                              bob):
        forward: dict = {}
        record_options(forward, alice, ["Buy a commercial solution now"])
        record_options(forward, bob, ["Buy a commercial solution"])
        reverse: dict = {}
        record_options(reverse, bob, ["Buy a commercial solution"])
        record_options(reverse, alice, ["Buy a commercial solution now"])
        assert len(forward["options"]) == len(reverse["options"]) == 1
        assert (forward["options"][0]["text"]
                == reverse["options"][0]["text"]
                == "Buy a commercial solution now")  # medoid = longer phrasing
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest "tests/test_mcda_helpers.py::TestRecordOptions::test_merging_is_order_independent_and_medoid_labelled" -q`
Expected: FAIL — today `reverse` keeps Bob's shorter first-submitted phrasing,
so the two labels differ.

- [ ] **Step 3: Implement the refactor**

In `consensus/methods/phases/_mcda_helpers.py`, change the import on line 16 to
add the new helpers. **Keep `word_overlap_similar`** for now — `record_criteria`
still calls it until Task 5, so leaving it in keeps the module importable and
this task's tests green in isolation:

```python
from ..parsing import (
    canonical_index,
    cluster_by_similarity,
    cluster_text_contributions,
    extract_json_block,
    parse_numbered_list,
    word_overlap_similar,
)
```

Replace the whole `record_options` function (lines 198-224) with:

```python
def record_options(state: dict, entity: Entity,
                   texts: list[str]) -> list[dict]:
    """Append this turn's options as raw contributions, rebuild the
    order-independent clustered view, and return the clusters this
    turn's options landed in.

    Every submission is retained in ``state["options_raw"]``; the merged
    view ``state["options"]`` is derived by clustering the whole raw set
    and labelling each cluster with its medoid, so grouping and label are
    independent of submission order (issue #42).  Ids are referenced
    downstream as ``O1..On`` and are frozen once the scoring phase
    begins (no options are added there).  Shared by the free-text and
    structured-output paths (issue #23).
    """
    raw = state.setdefault("options_raw", [])
    since = len(raw)
    for text in texts:
        cleaned = str(text).strip().rstrip('.')
        if len(cleaned) < MIN_OPTION_LENGTH:
            continue
        raw.append({"entity_id": entity.id, "entity_name": entity.name,
                    "text": cleaned})
    view, touched = cluster_text_contributions(
        raw, since=since, threshold=SIMILARITY_THRESHOLD)
    state["options"] = view
    return touched
```

- [ ] **Step 4: Run the site's tests to verify they pass**

Run: `python -m pytest tests/test_mcda_helpers.py tests/test_phases_mcda.py tests/test_mcda_structured.py -q`
Expected: PASS. `record_criteria` is untouched in this task (still uses the
retained `word_overlap_similar` import), so the whole MCDA suite stays green —
`TestRecordOptions` now also covers order-independence.

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/_mcda_helpers.py tests/test_mcda_helpers.py
git commit -m "fix(mcda): order-independent option merging via medoid labels (#42)"
```

---

### Task 5: MCDA `record_criteria` — cluster-then-label with weight votes

**Files:**
- Modify: `consensus/methods/phases/_mcda_helpers.py` (`record_criteria` at
  lines 255-289)
- Test: `tests/test_mcda_helpers.py` (`TestRecordCriteria`)

**Interfaces:**
- Consumes: `cluster_by_similarity`, `canonical_index` from Task 1 (imported in
  Task 4).
- Produces: `record_criteria(state, entity, items) -> list[dict]` — appends to
  `state["criteria_raw"]`, rebuilds `state["criteria"]` (shape unchanged:
  `{id, name, weight_votes}`), returns clusters touched this turn.
  `criterion_weight` (unchanged) still reads `weight_votes`.

- [ ] **Step 1: Write the failing test**

In `tests/test_mcda_helpers.py`, append this method to `class TestRecordCriteria`
(keep all existing methods):

```python
    def test_weight_aggregation_is_order_independent(self, alice, bob):
        forward: dict = {}
        record_criteria(forward, alice,
                        [{"name": "Total cost of ownership", "weight": 4}])
        record_criteria(forward, bob,
                        [{"name": "Total cost of ownership now", "weight": 2}])
        reverse: dict = {}
        record_criteria(reverse, bob,
                        [{"name": "Total cost of ownership now", "weight": 2}])
        record_criteria(reverse, alice,
                        [{"name": "Total cost of ownership", "weight": 4}])
        assert len(forward["criteria"]) == len(reverse["criteria"]) == 1
        assert (forward["criteria"][0]["weight_votes"]
                == reverse["criteria"][0]["weight_votes"]
                == {"1": 4, "2": 2})
        # medoid label is order-independent (longer phrasing wins the tie)
        assert (forward["criteria"][0]["name"]
                == reverse["criteria"][0]["name"]
                == "Total cost of ownership now")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest "tests/test_mcda_helpers.py::TestRecordCriteria::test_weight_aggregation_is_order_independent" -q`
Expected: FAIL — today the first-submitted name (`"Total cost of ownership"`,
Alice in `forward`, Bob's `"...now"` in `reverse`) owns the label, so the two
runs disagree on `name`.

- [ ] **Step 3: Implement the refactor**

In `consensus/methods/phases/_mcda_helpers.py`, now that both MCDA sites are
converted, drop `word_overlap_similar` from the import (it is no longer used in
this module):

```python
from ..parsing import (
    canonical_index,
    cluster_by_similarity,
    cluster_text_contributions,
    extract_json_block,
    parse_numbered_list,
)
```

Replace the whole `record_criteria` function (lines 255-289) with:

```python
def record_criteria(state: dict, entity: Entity,
                    items: list[dict]) -> list[dict]:
    """Append this turn's weighted criteria as raw contributions, rebuild
    the order-independent clustered view, and return the criteria this
    turn touched.

    Every submission is retained in ``state["criteria_raw"]``; the merged
    view ``state["criteria"]`` is derived by clustering the whole raw set
    and labelling each cluster with its medoid name, so grouping and
    label are independent of submission order (issue #42).  Each entity's
    weight vote is its latest raw weight within the cluster (last-write-
    wins refinement).  Weights are clamped into
    ``[WEIGHT_MIN, WEIGHT_MAX]`` (the free-text path may carry arbitrary
    numbers).  Returns the criterion dicts this turn touched.
    """
    raw = state.setdefault("criteria_raw", [])
    since = len(raw)
    for item in items:
        name = str(item.get("name") or "").strip().rstrip('.')
        if len(name) < MIN_CRITERION_LENGTH:
            continue
        try:
            weight = int(item.get("weight", DEFAULT_WEIGHT))
        except (TypeError, ValueError):
            weight = DEFAULT_WEIGHT
        weight = min(max(weight, WEIGHT_MIN), WEIGHT_MAX)
        raw.append({"entity_id": entity.id, "entity_name": entity.name,
                    "name": name, "weight": weight})
    groups = cluster_by_similarity(
        list(range(len(raw))),
        text_of=lambda i: raw[i]["name"],
        threshold=SIMILARITY_THRESHOLD)
    view: list[dict] = []
    touched: list[dict] = []
    for cid, group in enumerate(groups, 1):
        canon = group[canonical_index(
            group, text_of=lambda i: raw[i]["name"])]
        votes: dict[str, int] = {}
        for i in group:  # ascending raw index -> last write wins per entity
            votes[str(raw[i]["entity_id"])] = raw[i]["weight"]
        item = {"id": cid, "name": raw[canon]["name"],
                "weight_votes": votes}
        view.append(item)
        if any(i >= since for i in group):
            touched.append(item)
    state["criteria"] = view
    return touched
```

- [ ] **Step 4: Run the full MCDA suite to verify it passes**

Run: `python -m pytest tests/test_mcda_helpers.py tests/test_phases_mcda.py tests/test_mcda_structured.py -q`
Expected: PASS. All existing `TestRecordCriteria` methods still hold
(sequential ids, similar-name merge adds a vote, resubmission replaces own
vote, one-submission dedup touches once, free-text weight clamped, mean weight,
default weight) plus the new order-independence test.

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/_mcda_helpers.py tests/test_mcda_helpers.py
git commit -m "fix(mcda): order-independent criteria merging with medoid labels (#42)"
```

---

### Task 6: Full-suite regression and documentation

**Files:**
- Modify: `HANDOVER.md`
- Modify: `docs/superpowers/plans/2026-07-15-order-independent-contribution-merging.md`
  (check off boxes as you go — optional)

**Interfaces:**
- Consumes: all prior tasks.
- Produces: a green full suite and an updated handover.

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS, with a count of at least 2295 (the #28 baseline) plus the new
tests added in Tasks 1-5. If anything unexpected fails, it will be a test that
asserted the old first-name-wins label; update it to expect the medoid, and
re-run.

- [ ] **Step 2: Update `HANDOVER.md`**

In the "What is done (all merged)" table, add a row:

```markdown
| Order-independent contribution merging | #42 | (this branch) |
```

In "Open work → Cross-cutting quality", delete the entire `#42 order-dependent
word-overlap merging` bullet.

In "Shared-helper dedup (low priority)", update the
`record_thoughts`/`record_ideas` duplication bullet to note both now delegate
to `parsing.cluster_text_contributions`, so only the give-up/validation blocks
remain near-duplicated.

Add a one-line note under the done row's context if useful: the fix lives in
`consensus/methods/parsing.py` (`word_overlap_ratio`, `cluster_by_similarity`,
`canonical_index`, `cluster_text_contributions`) and is adopted by
`record_ideas`, `record_thoughts`, `record_options`, `record_criteria`;
grouping is connected-components (transitive, order-independent) and labels are
the cluster medoid.

- [ ] **Step 3: Commit**

```bash
git add HANDOVER.md
git commit -m "docs: record order-independent contribution merging (#42)"
```

- [ ] **Step 4: Verify the branch is clean and summarize**

Run: `git status --short` (expect empty) and
`python -m pytest tests/ -q` one final time (expect green).

---

## Notes for the implementer

- The four `record_*` refactors are the same shape: append raw → rebuild view →
  return touched. `record_ideas`/`record_thoughts`/`record_options` delegate
  entirely to `cluster_text_contributions`; only `record_criteria` inlines the
  loop because it aggregates `weight_votes`.
- `state["<x>_raw"]` is a plain (non-`_`-prefixed) key, so it has the same
  method-switch survival lifecycle as today's `ideas`/`criteria`/`options`/
  `thoughts` — no change to the preserved set in
  `app_discussion_flow.switch_discussion_method`.
- Ids are stable across the collection→scoring boundary because each `record_*`
  runs in exactly one collection phase; no new raw items arrive during scoring.
- Do not add per-file magic numbers; every threshold/bound is an existing named
  constant at the call site.
