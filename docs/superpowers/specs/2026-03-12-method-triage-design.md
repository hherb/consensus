# Method Triage & Recommendation — Design Spec

## Overview

A two-tier system for recommending discussion methods based on the nature of the user's question. Tier 1 is a lightweight LLM-based recommender that runs at discussion setup time — the user provides a topic and answers one "what kind of answer?" question, and gets ranked method recommendations before the discussion starts. Tier 2 is a guided triage meta-method ("Guided Triage") for ambiguous or complex cases, where the moderator interviews participants, proposes a methodology, and the group confirms before proceeding.

This addresses a usability gap: as the method catalog grows (currently 11 methods), manual selection becomes harder. Users shouldn't need to understand every method's strengths to pick the right one.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Where does triage happen? | Both at setup (quick) and as a meta-method (guided) | Quick covers straightforward cases; guided handles ambiguity. Best of both worlds. |
| Intake friction | One question ("what kind of answer?") for quick mode | Single question gives high signal boost with minimal friction. Guided mode handles the rest. |
| Classification engine | LLM-based, not rule-based | Method catalog will grow; LLM prompt auto-includes new methods. Rules would need manual maintenance. |
| Who conducts guided triage? | Moderator interviews humans, proposes to group, group confirms | Mirrors clinical case discussion: attending presents, chair proposes framework, team can push back. |
| Method chaining | Single method only (v1) | Keeps scope manageable. Architecture doesn't preclude future chaining. |
| Triage recommendations | Advisory, never forced | User can always override via the existing dropdown. Trust is built through good recommendations, not enforcement. |

## Component 1: MethodRecommender

**Location:** `consensus/methods/recommender.py`

Stateless utility class. Shared by both the quick recommendation at setup and the guided triage method.

### Interface

```python
@dataclass
class MethodRecommendation:
    method_name: str        # registry key, e.g. "ach"
    display_name: str
    confidence: float       # 0.0-1.0
    reasoning: str          # 1-2 sentence explanation
    fit_factors: list[str]  # what about the problem matches this method

class MethodRecommender:
    async def recommend(
        self,
        topic: str,
        answer_type: str,
        method_catalog: list[dict],
        ai_client: AIClient,
        provider: dict,             # must contain "base_url", "api_key", "model"
        num_recommendations: int = 3,
        additional_context: str = "",   # intake responses for guided mode
    ) -> list[MethodRecommendation]: ...
```

### Classification Prompt

- System prompt: "You are a discussion methodology expert. Given a topic and problem characteristics, recommend the most suitable discussion methods."
- Includes the full method catalog (name, description, phases) dynamically built from `list_methods()`. Excludes `triage` (circular) and `open_discussion` (reserved as fallback).
- Includes a problem-type taxonomy mapping to method strengths:
  - Probabilistic / forecasting → Delphi, Belief Diffusion
  - Risk assessment → Premortem
  - Hypothesis testing / intelligence analysis → ACH
  - Testing claim robustness → Counterfactual Stress Test
  - Assumption examination → Key Assumptions Check
  - Resolving disagreements → Adversarial Collaboration
  - Stress-testing positions → Red Team / Blue Team
  - Complex multi-faceted questions → Recursive Decomposition
  - Decision-making with formal consensus → Voting
  - General exploration → Open Discussion (fallback only)
- User message provides topic, answer_type, and optional additional_context
- Requests structured JSON response with top N recommendations matching this schema:
  ```json
  { "recommendations": [
    { "method_name": "ach", "display_name": "Analysis of Competing Hypotheses",
      "confidence": 0.85, "reasoning": "...", "fit_factors": ["...", "..."] }
  ]}
  ```
- JSON parsing uses `methods/parsing.py` utilities (consistent with existing methods). On parse failure, falls back to `open_discussion`.

### Answer Type Options

The single intake question "What kind of answer are you looking for?" with these options:

1. Explore a topic from multiple perspectives
2. Make a decision between options
3. Forecast or estimate something
4. Identify risks or failure modes
5. Test a hypothesis or claim
6. Resolve a disagreement
7. Something else / not sure

### Fallback Behavior

If the LLM call fails (network error, parsing failure, etc.), return:
```python
[MethodRecommendation(
    method_name="open_discussion",
    display_name="Open Discussion",
    confidence=0.5,
    reasoning="Could not reach AI for recommendation. Open Discussion is a safe default.",
    fit_factors=["fallback"],
)]
```

## Component 2: Setup UI Enhancement

**Location:** `consensus/static/setup.js` (existing file)

### UX Flow

1. User enters discussion topic (existing field, unchanged)
2. Below the topic: new single-select question — "What kind of answer are you looking for?" with the 7 options, styled as radio buttons or compact button group
3. When both topic and answer_type are filled, a "Suggest Method" button activates (or auto-fires after debounce)
4. System calls recommender, displays results inline:
   - Top recommendation highlighted with confidence and reasoning
   - 2 runners-up shown smaller
5. Clicking a recommendation selects it in the existing method dropdown
6. User can ignore suggestions and pick manually — recommendations are advisory

### API

**New endpoint:**
```
POST /api/recommend-method
Body: { "topic": "...", "answer_type": "..." }
Response: { "recommendations": [{ "method_name": "...", "display_name": "...", "confidence": 0.8, "reasoning": "...", "fit_factors": ["..."] }] }
```

**Desktop bridge:** `recommend_method(topic, answer_type)` — same interface, delegating to `ConsensusApp.recommend_method()`.

### Key Principle

The existing setup flow is unchanged for users who don't engage with the recommendation. The dropdown still works as before. Recommendations enhance but never replace manual selection.

## Component 3: TriageMethod — Guided Triage Meta-Method

**Location:** `consensus/methods/triage.py`

**Registry key:** `"triage"`, display name "Guided Triage"

For cases where the quick recommendation isn't enough — the topic is ambiguous, the user is unsure, or the group wants to collaboratively decide on methodology.

### Phase 1: INTAKE (1 round, humans only)

The moderator asks human participants 3-4 structured questions:

1. What type of question is this? (same taxonomy as the quick recommender)
2. What's the decision context? (academic exploration, real-world decision with stakes, risk assessment, policy evaluation, etc.)
3. What's the uncertainty structure? (known unknowns, disagreement between experts, quantifiable uncertainty, poorly defined problem space, etc.)
4. Is there a preliminary conclusion or position to examine? (optional — enables methods like Counterfactual Stress Test or Premortem)

Only human participants respond. AI entities are silent during this phase. This is enforced via `TriageIntakeHandler.get_turn_order()`, which filters `entity_ids` by cross-referencing `discussion.entities` to include only those with `entity_type == EntityType.HUMAN`. If no human participants exist (all-AI discussion), the phase is skipped and RECOMMEND proceeds using only the topic text.

**Handler:** `TriageIntakeHandler`

### Phase 2: RECOMMEND (1 round, moderator only)

This is a moderator-only phase (similar to `ExtractClaimsHandler` in Counterfactual Stress Test). `get_turn_order()` returns `[discussion.moderator_id]` only.

The flow:
1. The moderator AI generates a turn that synthesizes intake answers into a problem characterization
2. `TriageRecommendHandler.process_response()` programmatically calls `MethodRecommender.recommend()` with the full intake context as `additional_context`, passing the moderator's provider/model
3. The recommendations are stored in `method_state["recommendations"]` (list of dicts) and `method_state["recommended_method"]` (top pick's `method_name`)
4. The handler's `process_response()` appends the recommendation summary to the display content, presenting top 2-3 methods with reasoning

**Handler:** `TriageRecommendHandler`

### Phase 3: CONFIRM (1 round, all participants)

All participants (including AI entities) get one round to:
- Agree with the recommendation
- Object with reasoning
- Suggest an alternative

The moderator evaluates responses and makes the final selection. If a human participant explicitly requests a different method, the moderator honors that.

`TriageConfirmHandler.process_response()` parses the moderator's final summary to extract the chosen method name and stores it in `method_state["chosen_method"]`. This key is read by the transition mechanism. If parsing fails, falls back to `method_state["recommended_method"]` (set during RECOMMEND phase).

**Handler:** `TriageConfirmHandler`

### Method Transition

After CONFIRM completes, the discussion flow detects that the triage method is complete (via `advance_phase()` returning `None`) and checks `method_state["chosen_method"]`. If present, `app_discussion_flow.py` calls `switch_discussion_method()` instead of ending the discussion. This is analogous to how `method_complete` is already detected after phase advancement.

1. Calls `switch_discussion_method(discussion, method_state["chosen_method"])`
2. This function:
   - Sets `discussion.discussion_method` to the chosen method
   - Calls `new_method.init_state(discussion)` for fresh state
   - Persists to DB
   - Emits system message: "Discussion method set to {display_name}. Beginning {first_phase.display_name} phase."
3. All triage messages are preserved in history (useful context for the chosen method)

## Component 4: Method Transition Mechanism

**Location:** `consensus/app_discussion_flow.py`

### Function

```python
async def switch_discussion_method(
    self, discussion_id: int, method_name: str
) -> dict:
```

This is the only place method switching happens. It:

1. Validates the target method exists and isn't "triage"
2. Gets the method instance via `get_method(method_name)`
3. Updates `discussion.discussion_method`
4. Initializes fresh `method_state` via `method.init_state(discussion)`
5. Persists both to DB
6. Adds a system message announcing the transition
7. Returns the new method's `to_dict()` metadata

### Constraints

- Only callable during an active discussion (status = "active")
- Only the triage method should call this (not exposed as a general API endpoint)
- Preserves all existing messages — no history rewriting

## Integration Points

### Method Registry (`consensus/methods/__init__.py`)
- Add `"triage": TriageMethod` to `_METHODS`
- `list_methods()` includes Triage in the UI catalog
- `MethodRecommender` excludes "triage" and "open_discussion" from its recommendation candidates

### ConsensusApp
- New method in `app_discussion_setup.py`: `recommend_method(topic, answer_type)` — calls `MethodRecommender.recommend()` using the current moderator's provider/model
- New method in `app_discussion_flow.py`: `switch_discussion_method(discussion_id, method_name)` — the transition mechanism
- `set_discussion_method()` — unchanged

### Server / Desktop Bridge
- New route: `POST /api/recommend-method` in `server.py`
- New bridge method: `recommend_method(topic, answer_type)` in `desktop.py`
- Both delegate to `ConsensusApp.recommend_method()`

## Edge Cases

| Scenario | Handling |
|----------|----------|
| Recommender API call fails | Graceful fallback to open_discussion with explanation |
| User starts discussion before recommendation returns | Fine — dropdown has a default. Recommendation is advisory. |
| Triage with no human participants | INTAKE phase skips to RECOMMEND using topic text only |
| User overrides triage recommendation | CONFIRM phase respects explicit human override |
| Method transition preserves messages | All triage messages stay in history as context |
| Triage recommends itself | Prevented — "triage" excluded from recommendation candidates |
| Fallback to open_discussion | Means no structured phases — discussion proceeds as standard moderated round-robin. This is intentional. |
| All-AI participants (no humans, human moderator) | Same as "no human participants" — INTAKE phase is skipped, RECOMMEND uses topic text only |
| Desktop bridge return type | `recommend_method()` returns list of dicts (serialized `MethodRecommendation`), JSON-serializable for pywebview |

## Out of Scope (Future Work)

- Method chaining / pipelines (e.g., "Key Assumptions Check → ACH")
- Learning from past discussions ("similar topic used ACH successfully")
- Per-user method preferences / defaults
- Participant-count-aware recommendations (e.g., "Red Team needs 3+ participants")
- Automatic re-triage mid-discussion if the chosen method proves unsuitable
