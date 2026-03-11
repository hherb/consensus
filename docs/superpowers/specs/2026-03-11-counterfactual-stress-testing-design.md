# Counterfactual Stress Testing — Design Spec

## Overview

A discussion method that systematically tests which beliefs are load-bearing vs. decorative in a developing consensus. For each key claim, participants are forced to argue from the premise that it is false, then assess how much damage this does to the overall conclusion. Produces a ranked classification of claims by structural importance.

## Motivation

Existing methods build consensus (Delphi, Open Discussion), challenge it adversarially (Red Team, Premortem, Key Assumptions), or evaluate evidence (ACH, Belief Diffusion). None answer the question: **which specific claims does the conclusion actually depend on?** Counterfactual Stress Testing fills this gap by producing actionable output — a ranked list of load-bearing vs. decorative beliefs with impact scores.

## Design Decisions

- **Self-contained or post-hoc:** Supports both. An optional deliberation phase runs if no prior conclusion is provided; skips straight to claim extraction if one is supplied via `method_state["prior_conclusion"]`.
- **One claim at a time:** Each round inverts a single claim and all participants assess its impact. Depth over speed.
- **Pure stress test:** Participants must argue from the counterfactual premise. No "rescuing" claims during inversion rounds. The synthesis phase can note alternative reasoning paths.
- **Impact scoring:** Each participant rates impact 1-5 per claim. Average score determines classification.
- **Dependency graph deferred:** Inter-claim dependency mapping is out of scope. Will be added as a future extension (noted in ROADMAP.md).

## Phases

### Phase 1: Deliberate (optional, 2 rounds)

Open discussion to establish a preliminary conclusion. Skipped if `prior_conclusion` is already set in `method_state` at discussion start.

- **System prompt:** Instructs participants to discuss openly and work toward a preliminary conclusion.
- **Turn prompt:** Ask participants to share perspective, build on others' contributions.
- **Summary:** Standard moderator summary. Final-round summary captured as `method_state["preliminary_conclusion"]`.
- **Advancement:** After 2 rounds, or immediately if `prior_conclusion` exists.
- **Skip mechanism:** When `prior_conclusion` is set, `CounterfactualStressTest.init_state()` sets `current_phase` directly to `"extract"` (bypassing the deliberate phase entirely), and copies `prior_conclusion` into `preliminary_conclusion`. This avoids the framework limitation where `should_advance` only fires after a turn is taken.

### Phase 2: Extract (1 round, moderator-driven)

Moderator extracts 3-7 key falsifiable claims from the deliberation or prior conclusion.

- **Turn order:** `get_turn_order` returns only the moderator entity ID (first entity), so participants do not speak in this phase.
- **Turn prompt:** Instructs moderator to review discussion and list 3-7 specific, falsifiable claims as a numbered list.
- **`process_response`:** Uses `parse_numbered_list()` to extract claims. Stores in `method_state["claims"]` as `[{id, text}, ...]`. Initializes `method_state["claim_results"]` with one entry per claim.
- **Zero-claims guard:** If `parse_numbered_list` returns 0 items (malformed moderator output), `process_response` sets `method_state["extraction_failed"] = True` and `claims` remains empty. `should_advance` checks this flag and if set, does NOT advance — instead the phase stays active for another round (retry). The turn prompt on retry explicitly tells the moderator the extraction failed and to try again with a numbered list. After 3 failed attempts, advance anyway and `get_conclusion_prompt` produces an error message explaining no claims could be extracted.
- **Advancement:** After 1 round with at least 1 claim extracted.

### Phase 3: Stress Test (condition-based, one iteration per claim)

For each claim, all participants argue from the premise that it is false and score the impact.

- **Iteration:** Uses `method_state["current_claim_index"]` (0-indexed) to track which claim is under test.
- **Per-claim banner:** The current claim under test is embedded directly in `get_system_prompt` and `get_turn_prompt` (both read `current_claim_index` each time they're called). The `get_transition_message` fires once on phase entry for the first claim. Subsequent claim transitions are announced via a banner in the turn prompt: `"--- Counterfactual Test #K of N --- Assume the following claim is FALSE: '[claim text]'"`. This avoids needing a framework hook for mid-phase transitions.
- **System prompt:** Forces counterfactual premise — participants MUST argue as if the claim is false, even if they believe it. Includes the current claim text.
- **Turn prompt:** Asks participants to assess damage to the conclusion and rate impact with `[IMPACT: N]` tag (1=unaffected, 5=collapses). Includes the current claim text.
- **`process_response`:** Extracts `[IMPACT: N]` via regex. Only stores scores for non-moderator entities (moderator summaries are excluded from scoring). Stores score in `method_state["claim_results"][current_claim_index]["scores"][entity_name]`.
- **Round completion:** `CounterfactualStressTest` overrides `on_round_complete` (method-level, same pattern as `BeliefDiffusion`). After all participants speak for a claim: compute `avg_score` from non-moderator scores, apply `classify_claim()`, increment `current_claim_index`, and increment `phase_round`.
- **Advancement:** `should_advance` returns `True` when `current_claim_index >= len(claims)` (all claims tested).

### Phase 4: Synthesize (1 round, moderator conclusion)

Moderator produces final analysis from accumulated results.

- **Conclusion prompt:** Receives full `claim_results` table. Classifies each claim, ranks by impact, identifies critical dependencies, and states overall conclusion robustness.
- **Classification thresholds:**
  - LOAD-BEARING: avg_score >= 4.0
  - SUPPORTIVE: avg_score >= 2.0
  - DECORATIVE: avg_score < 2.0

## Method State

```python
method_state = {
    # Base (auto-managed)
    "current_phase": str,
    "phase_round": int,

    # Deliberate phase
    "preliminary_conclusion": str,       # moderator summary, or user-provided

    # Optional: provided at setup to skip deliberation
    "prior_conclusion": str | None,

    # Extract phase
    "claims": [
        {"id": 1, "text": "..."},        # 3-7 falsifiable claims
    ],
    "extraction_failed": False,           # set True if parse fails
    "extraction_attempts": 0,             # retry counter (max 3)

    # Stress test tracking
    "current_claim_index": 0,             # 0-indexed, which claim is under test
    "claim_results": [
        {
            "claim_id": 1,
            "claim_text": "...",
            "scores": {"entity_name": 4}, # impact score 1-5 per participant
            "avg_score": None,            # computed after all score
            "classification": None,       # LOAD-BEARING / SUPPORTIVE / DECORATIVE
        },
    ],
}
```

## File Structure

| File | Contents |
|------|----------|
| `consensus/methods/counterfactual.py` | `CounterfactualStressTest` method class, registration |
| `consensus/methods/phases/counterfactual_deliberate.py` | `CounterfactualDeliberateHandler` |
| `consensus/methods/phases/counterfactual_extract.py` | `ExtractClaimsHandler` |
| `consensus/methods/phases/counterfactual_stress.py` | `StressTestHandler` |
| `consensus/methods/phases/counterfactual_synthesize.py` | `SynthesizeHandler` |
| `consensus/methods/phases/_counterfactual_helpers.py` | `extract_impact_score()`, `classify_claim()`, `format_results_table()` |

## Handler Details

### CounterfactualDeliberateHandler
- Named `CounterfactualDeliberateHandler` to avoid collision with the existing `DeliberateHandler` in the Voting method.
- `phase = Phase(name="cf_deliberate", display_name="Deliberation", description="Open discussion to establish preliminary conclusion", rounds=2, allow_tools=True)`
- Phase name is `cf_deliberate` (not `deliberate`) to avoid phase-name collision with Voting's deliberate phase.
- `init_state`: `{"preliminary_conclusion": None, "prior_conclusion": None}`
- `should_advance`: Default round-based (skip is handled in `CounterfactualStressTest.init_state()`, not here).
- After phase completes, moderator summary is stored as `preliminary_conclusion`.

### ExtractClaimsHandler
- `phase = Phase(name="extract", display_name="Claim Extraction", description="Moderator extracts key falsifiable claims", rounds=0, allow_tools=False)`
- `rounds=0` (condition-based advancement only — the retry mechanism controls when to advance).
- `init_state`: `{"claims": [], "claim_results": [], "current_claim_index": 0, "extraction_failed": False, "extraction_attempts": 0}`
- `get_turn_order`: Returns only the moderator entity ID (first entity). Participants do not speak in this phase.
- `process_response`: `parse_numbered_list(content)` → build claims list → initialize `claim_results`. If 0 claims extracted, sets `extraction_failed = True` and increments `extraction_attempts`.
- `should_advance`: Returns `True` if `len(claims) > 0`. Returns `True` if `extraction_attempts >= 3` (give up). Returns `False` otherwise (retry). The `rounds` field is irrelevant — advancement is entirely condition-based.
- `get_turn_prompt`: On retry (`extraction_failed` and `extraction_attempts > 0`), explicitly tells the moderator the extraction failed and to try again with a numbered list format.
- Prompt references `preliminary_conclusion` or `prior_conclusion`.

### StressTestHandler
- `phase = Phase(name="stress_test", display_name="Counterfactual Stress Test", description="Systematically invert each claim and assess impact", rounds=0, allow_tools=True)`
- `rounds=0` (condition-based advancement only).
- `get_transition_message`: Announces which claim is being inverted.
- `process_response`: Extracts `[IMPACT: N]` via `extract_impact_score()`. Stores in `claim_results`.
- `should_advance`: `current_claim_index >= len(claims)`.
- Round completion logic: After all participants speak for current claim, compute `avg_score` for that claim, apply `classify_claim()`, increment `current_claim_index`.

### SynthesizeHandler
- `phase = Phase(name="synthesize", display_name="Synthesis", description="Final classification and robustness assessment", rounds=1, allow_tools=False)`
- `get_turn_order`: Returns only the moderator entity ID (moderator-only phase). No participants speak.
- This handler provides prompts for the synthesis round. After the round completes and the phase advances to `None` (no next phase), the framework triggers `get_conclusion_prompt`. The actual conclusion is driven by `CounterfactualStressTest.get_conclusion_prompt()` (method-level override, same pattern as `BeliefDiffusion` and `PremortemAnalysis`), which injects `format_results_table(claim_results)` into the prompt.

## Method-Level Overrides (`CounterfactualStressTest`)

The method class overrides the following `DiscussionMethod` methods:

- **`init_state(discussion)`:** Calls `super().init_state()` to merge handler states. If `prior_conclusion` is set (e.g., passed in from discussion setup), sets `current_phase = "extract"` and copies `prior_conclusion` into `preliminary_conclusion`, bypassing the deliberate phase entirely.
- **`on_round_complete(discussion)`:** Calls `super().on_round_complete()` which increments `phase_round` — no additional `phase_round` increment in the method body. If the current phase is `stress_test`, additionally computes `avg_score` for the current claim from non-moderator scores, applies `classify_claim()`, and increments `current_claim_index`. Same delegation pattern as `BeliefDiffusion.on_round_complete`.
- **`get_conclusion_prompt(discussion)`:** Builds the final synthesis prompt by injecting `format_results_table(claim_results)` and the `preliminary_conclusion`. Asks the moderator to rank claims, classify each, and assess overall robustness.

## Helpers Module (`_counterfactual_helpers.py`)

```python
def extract_impact_score(content: str) -> int | None:
    """Extract [IMPACT: N] tag from response. Returns 1-5 or None."""

def classify_claim(avg_score: float) -> str:
    """LOAD-BEARING (>=4.0), SUPPORTIVE (>=2.0), DECORATIVE (<2.0)."""

def format_results_table(claim_results: list[dict]) -> str:
    """Markdown table: Claim | Avg Impact | Classification."""
```

## Prompt Design

### Deliberate
- **System:** "You are participating in a preliminary discussion to establish a position on the topic. Discuss openly and work toward a preliminary conclusion."
- **Turn:** "Share your perspective on this topic. Build on others' contributions where possible."

### Extract
- **Turn:** "Review the discussion above. Extract 3-7 key claims that the conclusion depends on. Each claim should be a specific, falsifiable assertion — not a value judgment or vague statement. List them as a numbered list."

### Stress Test
- **System:** "You are participating in a counterfactual stress test. You MUST argue from the premise that the specified claim is FALSE — even if you believe it is true. Your job is to honestly assess how much damage this does to the overall conclusion."
- **Turn:** "COUNTERFACTUAL: Assume '[claim text]' is FALSE. Given this, how does the overall conclusion change? What breaks? What still holds? Rate the impact on a scale of 1-5 at the end of your response using this exact format: [IMPACT: N] where 1 = conclusion completely unaffected, 5 = conclusion collapses entirely."
- **Transition:** "--- Counterfactual Test #K of N ---\nAssume the following claim is FALSE: '[claim text]'\nAll participants must argue from this premise."

### Synthesize
- **Conclusion:** Receives full claim_results table. Asks moderator to rank claims by structural importance, classify each, identify which the conclusion critically depends on, and state overall robustness.

## Testing Strategy

- Unit tests for helpers: `extract_impact_score`, `classify_claim`, `format_results_table`
- Unit tests for each handler: `process_response`, `should_advance`, `init_state`, prompt generation
- Integration test: full method flow with mock discussion (deliberate → extract → stress test all claims → synthesize)
- Edge cases: prior_conclusion skip, missing impact tags (fallback), single-claim extraction, all claims load-bearing, all decorative

## ROADMAP Addition

Add to Counterfactual Stress Testing entry:
- Mark as Done
- Add planned extension: "Dependency graph visualization — map inter-claim dependencies to show which claims support other claims, not just the final conclusion"
