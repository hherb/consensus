# Double Crux pre-belief poll — design

_2026-07-17. Closes the HANDOVER tech-debt item: "Double Crux belief
shift is only measured for crux authors, and initial/final beliefs can
refer to different phrasings."_

## Problem

Double Crux's headline success metric is **belief shift on the shared
crux** — how far each party's probability on the pivotal claim moved
after evidence was focused on it. Today that metric is unreliable in
two ways, both rooted in how the "before" number is captured.

The "before" number (`initial_beliefs`) is snapshotted inside
`record_crux_selection` (`_crux_helpers.py`) from the **hunt-phase
per-crux beliefs** of only the cruxes the moderator selected as the
shared crux:

1. **Coverage gap.** A participant whose own crux was not among the
   moderator's selected `crux_ids` gets no initial belief, so the
   belief-shift table shows `? → final` for them
   (`format_belief_shifts`).
2. **Proposition mismatch.** The snapshotted initial belief is stated
   on the **crux author's own phrasing**, while the final `crux_belief`
   (collected in `resolve`) is stated on the **moderator's synthesized
   neutral claim**. When the moderator reframes or flips the polarity of
   the claim, initial and final measure two different propositions. A
   prompt asks the moderator to preserve polarity, but that is guidance,
   not a guarantee.

## Fix

Insert a new structured micro-turn phase, `poll_belief`, **after
`identify_crux` and before `test_crux`**, in which every disagreeing
party states their current probability that the **moderator's
synthesized shared claim** is true. That single, shared proposition
becomes the authoritative `initial_beliefs` for all participants,
fixing both problems at once:

- Every party is polled, so no participant shows `?` for their initial
  (barring a total parse failure — see Error handling).
- Initial and final are both measured on the same moderator claim, so a
  reframe can no longer silently corrupt the shift.

Polling **after identification** (rather than reusing the hunt beliefs)
is also the correct baseline for the metric's intent: the metric asks
whether *crux testing / evidence* moved a party, so the "before" should
be taken on the identified claim, immediately before testing.

### Phase sequence

```
positions → hunt_cruxes → identify_crux → poll_belief → test_crux → resolve
```

The poll runs on the **factual path only**, with **no changes to
identify's routing**. `IdentifyCruxHandler.next_phase` already returns:

- `LINEAR_NEXT` for `factual` → now lands on `poll_belief` (was
  `test_crux`);
- `"resolve"` for `values` and for exhausted `none` → jumps over
  `poll_belief` and `test_crux`;
- `"hunt_cruxes"` for `none` within budget → loop-back, unchanged.

So `values`/`none` discussions skip the poll automatically. `poll_belief`
itself always advances linearly to `test_crux`.

### Design decisions (owner-approved 2026-07-17)

- **Always-on, no config flag.** The poll is a permanent phase on the
  factual path. One structured micro-turn per party is cheap relative to
  the two-round `test_crux` phase, and it fixes the method's core
  metric; a config flag would add UI + a second code path to test for a
  correctness fix that should simply always apply (YAGNI).
- **Replace the hunt snapshot, do not seed from it.** The poll owns
  `initial_beliefs` entirely. Per-crux `belief` stays recorded in the
  hunt phase and shown in the crux list as provenance, but is no longer
  copied into `initial_beliefs`. If a party's poll fails after retries,
  their initial stays an honest `?` rather than a silently
  phrasing-mismatched number — mixing phrasings is exactly the defect
  being fixed, so a fallback that reintroduces it invisibly is worse
  than an explicit unknown.

## Components

### 1. `consensus/methods/phases/poll_belief.py` — `PollBeliefHandler`

A new `PhaseHandler`, structurally a hybrid of `IdentifyCruxHandler`
(structured output) and `ResolveCruxHandler` (per-participant straggler
completion):

- `phase = Phase(name="poll_belief", display_name="Belief Poll",
  rounds=1)`. No `track_evidence`.
- **Turn order:** default — does *not* override `get_turn_order`, so it
  runs for the full non-moderator roster exactly like `hunt`/`resolve`.
- **State:** `init_state → {"poll_beliefs": []}` — a list of
  `{entity_id, entity_name, belief, reasoning}`, replace-own on
  resubmission (mirrors `record_resolution`). The list key is plural to
  match the `resolutions` / `cruxes` convention and stay distinct from
  the `poll_belief` phase name.
- **Structured output** (`requires_structured_output = True`): tool
  `submit_crux_belief`, parameters `POLL_BELIEF_TOOL_PARAMETERS`
  (`belief`: number 0–1, **required**; `reasoning`: string,
  **required**). Per the structured-conversion convention, `reasoning`
  is required and rendered before the number so the turn reads as a real
  contribution.
- **Free-text fallback:** `extract_poll_belief` accepts a fenced JSON
  block containing a `belief` key. `process_response` records it when
  parseable; otherwise logs and leaves the party unpolled (retried
  within the round cap).
- **Prompt:** `get_system_prompt` shows the moderator's synthesized
  claim via `format_shared_crux` and asks for the participant's current
  probability (0–1) that the claim is true, with brief reasoning. It
  deliberately does **not** surface other parties' numbers — this is a
  clean "before" baseline and must not anchor.
- **Advancement:** `should_advance` returns `True` when every roster id
  has polled (`set(discussion.turn_order) ⊆ entities_with_poll(state)`)
  or `MAX_POLL_ROUNDS` is reached — the straggler pattern
  `ResolveCruxHandler.should_advance` already uses, with the same
  empty-roster fallback (`bool(state.get("poll_beliefs")) and
  phase_round > 1`).
- **Routing:** `next_phase` calls `apply_poll_beliefs(state)` (fold into
  `shared_crux["initial_beliefs"]`) then returns `LINEAR_NEXT`. Single
  deterministic write point, mirroring how `ResolveCruxHandler.next_phase`
  builds `crux_map`.
- `get_transition_message` announces the poll; `get_summary_prompt`
  gives the moderator a brief between-speaker note.

### 2. `_crux_helpers.py` additions

Pure helpers + constants + schema, alongside the existing crux helpers:

- `MAX_POLL_ROUNDS: int` — give-up cap for the condition-based phase (no
  magic numbers, per golden rules).
- `POLL_BELIEF_TOOL_PARAMETERS: dict` — JSON Schema for
  `submit_crux_belief`.
- `validate_poll_belief_payload(payload) -> str` — `""` or a
  human-readable error; reuses `_belief_error` for the `belief` field
  and requires non-empty `reasoning`.
- `record_poll_belief(state, entity, payload) -> None` — append/replace
  the entity's entry in `state["poll_beliefs"]` (shared by free-text and
  structured paths).
- `entities_with_poll(state) -> set[int]` — polled entity ids (parallel
  to `entities_with_resolutions`).
- `extract_poll_belief(content) -> dict | None` — fenced JSON block with
  a `belief` key (fallback path).
- `apply_poll_beliefs(state) -> None` — fold `state["poll_beliefs"]`
  into `state["shared_crux"]["initial_beliefs"]` as `name → float(belief)`.

### 3. `record_crux_selection` change

The `VERDICT_FACTUAL` branch stops populating `initial_beliefs` from the
crux snapshot: it sets `"initial_beliefs": {}` (the poll fills it). The
`crux_ids` loop and the `by_id` lookup that built the snapshot are
removed. `source_crux_ids`, `claim`, and `description` are unchanged.
`build_crux_map` and `format_belief_shifts` need **no change** — they
already read `shared_crux["initial_beliefs"]`.

### 4. `DoubleCrux.phase_handlers`

Insert `PollBeliefHandler()` between `IdentifyCruxHandler()` and
`TestCruxHandler()`. `__init_subclass__` re-derives `default_phases`
from the handler tuple automatically.

### 5. Wording fix in `IdentifyCruxHandler.get_system_prompt`

The current text tells the moderator that "each participant's stated
belief is carried over as their initial belief on the shared claim, so a
reversed or reframed claim would make those numbers meaningless." With
the poll, the initial is re-collected on the moderator's claim, so this
is no longer true. Reword: keep the polarity guidance as good practice
(a reversed claim still confuses human readers), but state that each
participant will be **re-polled on this exact claim**, and drop the
"carried over / meaningless" clause.

## Data flow

```
hunt_cruxes:   each crux recorded with per-crux `belief` (provenance only)
identify_crux: moderator picks factual crux → shared_crux.claim set,
               initial_beliefs = {}
poll_belief:   each party states belief on shared_crux.claim
               → state["poll_beliefs"] = [{entity_id, name, belief, ...}]
               next_phase: apply_poll_beliefs →
               shared_crux["initial_beliefs"] = {name: belief}
test_crux:     evidence focused on the crux (unchanged)
resolve:       each party restates crux_belief (the "final")
               next_phase: build_crux_map reads initial_beliefs + finals
               → belief_shifts fully populated for all parties
```

## Error handling

- Poll parse failures are retried within `MAX_POLL_ROUNDS` (the phase is
  condition-based; stragglers get further rounds up to the cap).
- Total poll failure for a party degrades to an honest `?` in the shift
  table — never a fabricated or phrasing-mismatched number. This is the
  same graceful degradation the resolve phase already exhibits for a
  missing final.
- No new network calls: the poll uses the existing per-turn generation
  path (structured tool call or free-text), which already retries with
  exponential backoff.

## Testing

- **Unit** (`tests/test_crux_helpers.py`, extended): the six new helpers
  — validate (good/bad `belief`, missing `reasoning`), record
  (append + replace-own), `entities_with_poll`, `extract_poll_belief`
  (JSON block / absent), `apply_poll_beliefs` (fold, empty).
- **Structured output** (`tests/test_double_crux_structured.py`,
  extended): the poll turn via a stubbed `complete_with_tools` — payload
  recorded, `initial_beliefs` folded, display renders reasoning before
  the number.
- **Phase behavior** (`tests/test_phases_double_crux.py`, extended):
  `should_advance` straggler logic (partial roster keeps waiting; cap
  forces advance); `next_phase` folds and returns `LINEAR_NEXT`;
  `record_crux_selection` factual branch now yields empty
  `initial_beliefs`.
- **Real-pipeline E2E** (`tests/test_method_flow_e2e.py`, extended): the
  all-human Double Crux run now passes through `poll_belief`; assert
  `initial_beliefs` is populated for **every** party and `belief_shifts`
  is fully computed (no `?`) end to end.
- **Regression sweep:** update any test asserting snapshot-sourced
  `initial_beliefs` from `record_crux_selection`, and any test asserting
  the `double_crux` phase list / count (now includes `poll_belief`).

## Non-goals

- No config flag or per-discussion toggle (always-on).
- No polling on the `values` / `none` paths (there is no factual claim
  to poll).
- No change to the crux-testing, resolution, or conclusion logic beyond
  `initial_beliefs` now being poll-sourced.
- No new UI beyond the standard phase rendering (the existing
  "Attach evidence" affordance is unrelated).
