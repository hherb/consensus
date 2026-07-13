# 15. Discussion Methods

[Back to index](programmer-manual.md) | [Previous: MCP Server](14-mcp-server.md)

---

## Overview

Consensus supports twelve analytical discussion methods, each imposing a
different reasoning structure on the conversation. The method framework lives
in `consensus/methods/` and integrates with the moderator and discussion flow
without changing core engine call sites.

All structured methods are built from **composable `PhaseHandler` instances**
— self-contained units that encapsulate the full behaviour of a single phase.
Method classes are thin assemblies that declare an ordered tuple of handlers.

---

## Architecture

```
consensus/methods/
    __init__.py          — method registry (get_method, list_methods)
    base.py              — DiscussionMethod ABC, Phase, ProcessedResponse
    phase_handler.py     — PhaseHandler ABC
    parsing.py           — shared parsing utilities
    open_discussion.py   — OpenDiscussion (no handlers, wraps default behaviour)
    key_assumptions.py   — KeyAssumptionsCheck
    premortem.py         — PremortemAnalysis
    adversarial_collab.py — AdversarialCollaboration
    red_team.py          — RedTeamBlueTeam
    ach.py               — ACH (Analysis of Competing Hypotheses)
    delphi.py            — DelphiMethod
    belief_diffusion.py  — BeliefDiffusion
    voting.py            — VotingMethod
    counterfactual.py    — CounterfactualStressTest
    recursive_decomposition.py — RecursiveDecomposition
    triage.py            — TriageMethod (Guided Triage meta-method)
    nominal_group.py     — NominalGroupTechnique (structured brainstorming, NGT)
    recommender.py       — MethodRecommender (LLM-based method classification)
    phases/              — 53 PhaseHandler implementations + 8 helper modules
        __init__.py
        _belief_helpers.py
        _delphi_helpers.py
        _voting_helpers.py
        _counterfactual_helpers.py
        surface_assumptions.py, challenge_assumptions.py, assess_assumptions.py
        frame_premortem.py, premortem_imagine.py, consolidate_premortem.py
        state_positions.py, define_criteria.py, present_evidence.py, adjudicate.py
        construct.py, attack.py, revise_red_team.py, assess_red_team.py
        hypothesize.py, gather_evidence.py, evaluate_matrix.py, analyse_ach.py
        estimate.py, revise_delphi.py, synthesise_delphi.py
        frame_hypotheses.py, prior_beliefs.py, diffuse_beliefs.py, diagnose_beliefs.py
        deliberate.py, vote.py, tally.py
        counterfactual_deliberate.py, counterfactual_extract.py
        counterfactual_stress.py, counterfactual_synthesize.py
        decompose.py, analyze_subquestions.py, integrate_subquestions.py, recompose.py
        triage_intake.py, triage_recommend.py, triage_confirm.py
        _ngt_helpers.py
        generate_ideas.py, cluster_ideas.py, clarify_ideas.py
        allocate_points.py, rank_ideas.py
```

---

## Key Classes

### `DiscussionMethod` (ABC)

The base class for all methods. Subclasses set class-level metadata and
either override hooks directly (traditional) or set `phase_handlers` to
delegate automatically.

```python
class DiscussionMethod(ABC):
    name: str                        # machine name, e.g. "ach"
    display_name: str                # human-readable
    description: str                 # shown in UI method selector
    default_phases: tuple[Phase, ...]  # auto-derived from phase_handlers
    phase_handlers: tuple[PhaseHandler, ...] = ()
```

**Hook methods** (all delegate to the active handler if present):

| Hook | Called by | Purpose |
|------|-----------|---------|
| `get_system_prompt(entity, discussion)` | `moderator.py` | System prompt for the current phase |
| `get_turn_prompt(entity, discussion)` | `moderator.py` | Turn instruction for the current phase |
| `get_summary_prompt(discussion, speaker, next_speaker)` | `moderator.py` | Summary prompt override |
| `get_conclusion_prompt(discussion)` | `moderator.py` | Final conclusion prompt |
| `filter_context_message(name, content, role, discussion)` | `moderator.py` | Transform context messages (e.g. anonymise for Delphi) |
| `process_response(content, entity, discussion)` | `app_discussion_flow.py` | Extract structured data from responses |
| `should_advance_phase(discussion)` | `app_discussion_flow.py` | Check if current phase is complete |
| `advance_phase(discussion)` | `app_discussion_flow.py` | Move to next phase |
| `on_round_complete(discussion)` | `app_discussion_flow.py` | Update counters after all participants respond |
| `get_turn_order(entity_ids, discussion)` | `moderator.py` | Custom turn ordering per phase |
| `init_state(discussion)` | `app_discussion_setup.py` | Initial `method_state` dict |

**`__init_subclass__` magic:** When a subclass sets `phase_handlers` without
explicitly defining `default_phases`, the base class auto-derives
`default_phases` from `handler.phase` for each handler. This eliminates
duplication between handler phase metadata and the method's phase list.

### `PhaseHandler` (ABC)

The building block for composable phases. Each handler is a self-contained
unit with:

```python
class PhaseHandler(ABC):
    phase: ClassVar[Phase]           # metadata (name, display_name, rounds, ...)

    # Required
    def get_system_prompt(entity, discussion) -> str
    def get_turn_prompt(entity, discussion) -> str

    # Optional (have defaults)
    def get_summary_prompt(...) -> str
    def filter_context_message(...) -> str
    def process_response(content, entity, discussion) -> ProcessedResponse
    def init_state(discussion) -> dict
    def should_advance(discussion) -> bool
    def get_transition_message(discussion) -> str
    def get_turn_order(entity_ids, discussion) -> list[int]
```

### `Phase` (dataclass)

```python
@dataclass
class Phase:
    name: str            # machine name, e.g. "hypothesize"
    display_name: str    # human-readable
    description: str     # shown to participants on phase transition
    rounds: int = 1      # full rounds in this phase (0 = until condition)
    allow_tools: bool = True
```

### `ProcessedResponse` (dataclass)

```python
@dataclass
class ProcessedResponse:
    display_content: str     # stored as message content
```

Handlers that extract structured data (beliefs, ratings, votes, ...)
must write it into `discussion.method_state` inside `process_response`;
the flow layer consumes only `display_content` (issue #21).

---

## Shared Parsing Utilities (`parsing.py`)

Three pure functions used across multiple handlers:

| Function | Purpose |
|----------|---------|
| `extract_json_block(content)` | Extract the first JSON object/array from a fenced code block |
| `parse_numbered_list(content, min_length)` | Extract items from numbered, prefixed, or bulleted lists |
| `word_overlap_similar(a, b, threshold)` | Jaccard-like word overlap check for deduplication |

---

## Method Registry

`consensus/methods/__init__.py` provides:

- `get_method(name: str) -> DiscussionMethod` — instantiate a method by name
- `list_methods() -> list[dict]` — return metadata for all registered methods

Methods register by being imported in `__init__.py`. The registry maps
`method.name` to the class.

---

## How Methods Integrate with the Engine

1. **Setup:** `app_discussion_setup.py` calls `method.init_state(discussion)`
   and stores the result in `discussion.method_state`.

2. **Turn generation:** `moderator.py` calls `method.get_system_prompt()` and
   `method.get_turn_prompt()` to build phase-specific prompts. If these return
   empty strings, the standard DB-template prompts are used as fallback.

3. **Response processing:** `app_discussion_flow.py` calls
   `method.process_response()` after each AI turn. The handler extracts
   structured data (beliefs, ratings, evidence) and updates
   `discussion.method_state`. The `display_content` becomes the stored message.

4. **Phase advancement:** After each turn, `app_discussion_flow.py` checks
   `method.should_advance_phase()`. If true, `method.advance_phase()` moves to
   the next phase and posts a transition message. When all phases are
   exhausted, the discussion auto-concludes.

5. **Round tracking:** `method.on_round_complete()` is called when all
   participants have spoken in a round. The base implementation increments
   `phase_round`; methods can override for custom logic (e.g. Belief Diffusion
   tracks diffusion rounds separately).

6. **Conclusion:** `method.get_conclusion_prompt()` provides method-specific
   conclusion instructions that reference accumulated state (hypotheses,
   belief trajectories, assumption assessments, etc.).

**Zero coupling to engine code:** The moderator and discussion flow modules
call method hooks through the `DiscussionMethod` interface. They have no
knowledge of `PhaseHandler` or any specific method implementation.

---

## Creating a New Method

1. **Write phase handlers** in `consensus/methods/phases/`. Each handler
   subclasses `PhaseHandler`, sets a `phase` class attribute, and implements
   `get_system_prompt()` and `get_turn_prompt()`. Override `process_response()`
   if you need to extract structured data.

2. **Create the method class** in `consensus/methods/`. Subclass
   `DiscussionMethod`, set `name`, `display_name`, `description`, and
   `phase_handlers`:

   ```python
   from .base import DiscussionMethod
   from .phases import MyPhaseAHandler, MyPhaseBHandler

   class MyMethod(DiscussionMethod):
       name = "my_method"
       display_name = "My Method"
       description = "..."
       phase_handlers = (
           MyPhaseAHandler(),
           MyPhaseBHandler(),
       )

       def get_conclusion_prompt(self, discussion):
           # reference accumulated method_state
           return "..."
   ```

3. **Register** by importing the class in `consensus/methods/__init__.py` and
   adding it to the `METHODS` dict.

4. **Write tests** in `tests/` covering handler parsing, phase transitions,
   and integration with the method framework.

**Reusing existing handlers:** Handlers are stateless and can be shared across
methods. For example, the `SurfaceAssumptionsHandler` could serve as a first
phase in a custom analytical method.

---

## Available Methods

| Method | Handlers | Phases |
|--------|----------|--------|
| Open Discussion | *(none — uses standard moderation)* | *(no phases)* |
| Key Assumptions Check | 3 | Surface → Challenge → Assess |
| Premortem Analysis | 3 | Frame → Premortem → Consolidate |
| Adversarial Collaboration | 4 | Positions → Criteria → Evidence → Adjudicate |
| Red Team / Blue Team | 4 | Construct → Attack → Revise → Assess |
| ACH | 4 | Hypothesise → Gather Evidence → Evaluate → Analyse |
| Delphi Method | 3 | Estimate → Revise → Synthesise |
| Belief State Diffusion | 4 | Frame → Prior → Diffuse → Diagnose |
| Participant Voting | 3 | Deliberate → Vote → Tally |
| Counterfactual Stress Testing | 4 | Deliberate → Extract Claims → Stress Test → Synthesize |
| Recursive Decomposition | 4 | Decompose → Analyze Sub-questions → Integrate → Recompose |
| Guided Triage | 3 | Intake → Recommend → Confirm |
| Nominal Group Technique | 5 | Generate → Cluster → Clarify → Allocate → Rank |

---

## Method Triage & Recommendation

Consensus includes a two-tier method recommendation system:

### Tier 1: Quick Recommender (`MethodRecommender`)

A stateless LLM-based classification engine in `consensus/methods/recommender.py`.
At discussion setup time, the user provides a topic and selects an answer type
(explore, decide, forecast, identify risks, test hypothesis, resolve disagreement,
or other). The recommender sends this to an LLM along with the method catalog and
a problem-type taxonomy, then returns ranked `MethodRecommendation` objects.

**Key types:**
- `MethodRecommendation` — dataclass with `method_name`, `display_name`,
  `confidence` (0–1), `reasoning`, and `fit_factors`
- `MethodRecommender` — stateless class with `_filter_catalog()`,
  `_build_system_prompt()`, `_build_user_prompt()`, `_parse_response()`,
  and `async recommend()`
- `ANSWER_TYPES` — list of answer type strings presented to users
- `_EXCLUDED_METHODS` — methods excluded from recommendations (`triage`,
  `open_discussion`)

**Integration:** `ConsensusApp.recommend_method(topic, answer_type)` creates an
`AIClient` using the moderator's provider config, calls `recommend()`, and returns
the results. Exposed via the `recommend_method` API route and desktop bridge.

**Frontend:** The setup tab has radio buttons for answer type selection and a
"Suggest Method" button. Clicking a recommendation selects it in the method
dropdown.

### Tier 2: Guided Triage (`TriageMethod`)

A three-phase meta-method for ambiguous cases where the best method isn't obvious.

| Phase | Handler | Turn Order | Purpose |
|-------|---------|------------|---------|
| Intake | `TriageIntakeHandler` | Human participants only | Structured questions about problem type, decision context, uncertainty |
| Recommend | `TriageRecommendHandler` | Moderator only | Synthesizes intake, triggers async `MethodRecommender` |
| Confirm | `TriageConfirmHandler` | All participants | Reviews recommendations, moderator makes final selection |

**Method transition:** When triage completes, `complete_turn()` detects
`method_state["chosen_method"]` and calls `switch_discussion_method()` in
`app_discussion_flow.py`. This reinitializes method state for the chosen method,
posts a system message, and returns `{"method_switched": True}` instead of
`{"method_complete": True}`.

**Async recommender wiring:** The `TriageRecommendHandler.process_response()` is
sync, so it stores `moderator_characterization` in method state. After
`process_response()` runs, `generate_ai_turn()` detects the triage recommend
phase and calls `_run_triage_recommender()` to make the async `MethodRecommender`
call. Results are stored in `method_state["recommendations"]` and
`method_state["recommended_method"]`.

---

*This chapter reflects the codebase as of March 2026.*
