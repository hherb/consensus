# Composable Phase Library — Design Spec

## Problem

Consensus has 9 discussion methods, each implementing phase logic as monolithic `if phase.name == "X"` blocks inside a single class. This creates three problems:

1. **Code duplication** — JSON extraction, numbered-list parsing, and word-overlap similarity are copy-pasted across 5+ methods.
2. **No reusability** — phases like "Surface Assumptions" (Key Assumptions Check) and "Hypothesize" (ACH) share structural patterns but can't be composed or reused.
3. **No composability path** — methods are atomic units. There is no way to chain methods (e.g. Key Assumptions Check -> ACH -> Premortem) or share phases between methods.

## Solution: Phase Handler Objects

Introduce a `PhaseHandler` ABC that makes phases first-class objects with their own behavior. Methods become ordered sequences of phase handler instances. The `DiscussionMethod` base class delegates its existing hooks to the active phase handler.

### Approach

Two-step implementation:

- **Step A (groundwork):** Extract shared parsing utilities, remove dead code (`ProcessedResponse.phase_complete` — unused by engine).
- **Step B (core):** Introduce `PhaseHandler` ABC, refactor `DiscussionMethod` to delegate to handlers, refactor all 8 structured methods to use phase handlers.

## Architecture

### PhaseHandler ABC

New file: `consensus/methods/phase_handler.py`

```python
class PhaseHandler(ABC):
    phase: Phase  # metadata (name, display_name, description, rounds, allow_tools)

    # Prompt hooks (mirror DiscussionMethod, scoped to this phase)
    @abstractmethod
    def get_system_prompt(self, entity, discussion) -> str: ...
    @abstractmethod
    def get_turn_prompt(self, entity, discussion) -> str: ...
    def get_summary_prompt(self, discussion, speaker_name, next_speaker_name) -> str:
        return ""
    def filter_context_message(self, entity_name, content, role, discussion) -> str:
        return content

    # Response processing
    def process_response(self, content, entity, discussion) -> ProcessedResponse:
        return ProcessedResponse(display_content=content)

    # Phase lifecycle
    def init_state(self, discussion) -> dict:
        return {}
    def should_advance(self, discussion) -> bool:
        if self.phase.rounds == 0:
            return False
        return discussion.method_state.get("phase_round", 1) > self.phase.rounds
    def get_transition_message(self, discussion) -> str:
        return (f"**Phase transition:** Moving to *{self.phase.display_name}*.\n\n"
                f"{self.phase.description}")

    # Turn order (optional)
    def get_turn_order(self, entity_ids, discussion) -> list[int]:
        return entity_ids
```

### DiscussionMethod Delegation

`DiscussionMethod` gains `phase_handlers: tuple[PhaseHandler, ...]` and a private `_active_handler(discussion)` method. Its existing hooks become thin dispatchers:

- `get_system_prompt()` -> `handler.get_system_prompt()`
- `get_turn_prompt()` -> `handler.get_turn_prompt()`
- `process_response()` -> `handler.process_response()`
- `should_advance_phase()` -> `handler.should_advance()`
- `get_summary_prompt()` -> `handler.get_summary_prompt()`
- `filter_context_message()` -> `handler.filter_context_message()`
- `get_turn_order()` -> `handler.get_turn_order()`
- `get_phase_transition_message()` -> find handler for new phase, call `get_transition_message()`

`default_phases` is auto-derived from `phase_handlers` — `tuple(h.phase for h in self.phase_handlers)`. Methods no longer declare phases separately.

`init_state()` merges all handlers' initial state into the base `{current_phase, phase_round}` dict.

`get_system_prompt` and `get_turn_prompt` are **no longer abstract** — they have default dispatcher implementations. Methods with handlers don't need to implement them. Methods without handlers (OpenDiscussion) override directly.

Cross-phase hooks (`get_conclusion_prompt`) stay on the method class.

### Shared Parsing Utilities

New file: `consensus/methods/parsing.py`

```python
def extract_json_block(content: str) -> Optional[dict]: ...
def parse_numbered_list(content: str, min_length: int = 10) -> list[str]: ...
def word_overlap_similar(a: str, b: str, threshold: float = 0.7) -> bool: ...
```

Pure functions extracted from duplicated code across BeliefDiffusion, ACH, Delphi, Voting, KeyAssumptions, and AdversarialCollab.

### File Structure

```
consensus/methods/
    __init__.py          # registry (unchanged public interface)
    base.py              # DiscussionMethod + Phase + ProcessedResponse (modified)
    phase_handler.py     # NEW — PhaseHandler ABC
    parsing.py           # NEW — shared parsing utilities
    phases/              # NEW — phase handler implementations
        __init__.py
        # Key Assumptions Check phases
        surface_assumptions.py
        challenge_assumptions.py
        assess_assumptions.py
        # ACH phases
        hypothesize.py
        gather_evidence.py
        evaluate_matrix.py
        analyse_ach.py
        # Belief Diffusion phases
        frame_hypotheses.py
        prior_beliefs.py
        diffuse_beliefs.py
        diagnose_beliefs.py
        # Premortem phases
        frame_premortem.py
        premortem.py
        consolidate_premortem.py
        # Adversarial Collaboration phases
        state_positions.py
        define_criteria.py
        present_evidence.py
        adjudicate.py
        # Red Team phases
        construct.py
        attack.py
        revise.py
        assess_red_team.py
        # Delphi phases
        estimate.py
        revise_delphi.py
        synthesise_delphi.py
        # Voting phases
        deliberate.py
        vote.py
        tally.py
    open_discussion.py   # unchanged (no phases)
    key_assumptions.py   # refactored to handler assembly
    ach.py               # refactored
    belief_diffusion.py  # refactored
    premortem.py         # refactored
    adversarial_collab.py # refactored
    red_team.py          # refactored
    delphi.py            # refactored
    voting.py            # refactored
```

## What Does NOT Change

- **Engine call sites** — `moderator.py`, `app_discussion_flow.py`, `app_discussion_setup.py` call the same `DiscussionMethod` methods. Zero changes needed.
- **`discussion.method_state`** — same JSON dict, same DB column, same serialization format.
- **Frontend** — same API responses, same method selector dropdown.
- **`OpenDiscussion`** — no phases, stays as-is. Delegation short-circuits (empty handlers tuple).
- **Registry** — same `_METHODS` dict, same `get_method()`, same `list_methods()`.
- **Saved discussions** — `method_state` format unchanged, existing discussions load and resume.

## Testing Strategy

- Existing tests must pass unchanged (engine interface preserved).
- New unit tests for each phase handler in isolation.
- New unit tests for `parsing.py` functions.
- Integration tests verifying that refactored methods produce identical behavior to the originals (same prompts, same state transitions for the same inputs).

## Future Extensions (not in scope)

- **Runtime composition** — assembling methods from phase handlers at discussion setup time.
- **Problem Intake / Method Recommender** — triage flow guiding users to methods.
- **Platform Telemetry** — logging method effectiveness metrics.
- **Phase progress UI** — frontend showing current phase name and progress.

These all build naturally on this foundation but are separate work items.
