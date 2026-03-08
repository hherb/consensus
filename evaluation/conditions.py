"""Evaluation conditions (ablation configurations).

Each condition defines how a Consensus discussion is configured:
- Which participant roles to create
- Whether to enable the Devil's Advocate
- Whether to enable memory tools
- Whether to enable web search tools
"""

from dataclasses import dataclass, field


@dataclass
class ParticipantSpec:
    """Specification for creating an AI participant."""
    name: str
    system_prompt: str
    role: str = "standard"  # "standard" or "devils_advocate"


@dataclass
class Condition:
    """An evaluation condition (one row in the ablation table)."""
    name: str
    description: str
    participants: list[ParticipantSpec] = field(default_factory=list)
    enable_da: bool = False
    enable_memory: bool = False
    enable_tools: bool = False
    num_rounds: int = 2


# ---------------------------------------------------------------------------
# Participant templates
# ---------------------------------------------------------------------------

GENERALIST = ParticipantSpec(
    name="Dr. Generalist",
    system_prompt=(
        "You are an experienced general internist. Approach clinical cases "
        "systematically: take a thorough history, identify key findings, "
        "generate a broad differential diagnosis, and reason through each "
        "possibility. Be explicit about your reasoning and what findings "
        "support or argue against each diagnosis."
    ),
)

SPECIALIST = ParticipantSpec(
    name="Dr. Specialist",
    system_prompt=(
        "You are an experienced specialist physician with deep expertise "
        "across subspecialties (rheumatology, endocrinology, haematology, "
        "infectious disease, etc.). Focus on pattern recognition: identify "
        "the key discriminating features that narrow the differential. "
        "Highlight pathognomonic findings when present. Be precise about "
        "which investigations confirm or exclude specific diagnoses."
    ),
)

PHARMACOLOGIST = ParticipantSpec(
    name="Dr. Pharmacologist",
    system_prompt=(
        "You are a clinical pharmacologist. Evaluate cases with attention to "
        "drug-related causes, metabolic and biochemical derangements, "
        "laboratory result interpretation, and treatment implications. "
        "Consider iatrogenic causes, drug interactions, and pharmacological "
        "mechanisms. When a diagnosis is established, comment on treatment "
        "approach and monitoring."
    ),
)

DEVILS_ADVOCATE = ParticipantSpec(
    name="Dr. Critic",
    system_prompt=(
        "You are a diagnostic critic. Your role is to challenge the "
        "diagnoses proposed by other participants. For each proposed "
        "diagnosis, identify: (1) findings that don't fit, (2) alternative "
        "explanations, (3) missing investigations that would confirm or "
        "exclude, (4) cognitive biases that may be influencing reasoning "
        "(anchoring, premature closure, availability bias). Be constructive "
        "but rigorous."
    ),
    role="devils_advocate",
)


# ---------------------------------------------------------------------------
# Ablation conditions
# ---------------------------------------------------------------------------

CONDITIONS: dict[str, Condition] = {
    "baseline": Condition(
        name="baseline",
        description="Single agent, single response (no discussion)",
        participants=[GENERALIST],
        num_rounds=1,
    ),
    "multi_agent": Condition(
        name="multi_agent",
        description="3 agents discuss without DA, memory, or tools",
        participants=[GENERALIST, SPECIALIST, PHARMACOLOGIST],
        num_rounds=2,
    ),
    "multi_agent_da": Condition(
        name="multi_agent_da",
        description="3 agents + Devil's Advocate, no memory or tools",
        participants=[GENERALIST, SPECIALIST, PHARMACOLOGIST, DEVILS_ADVOCATE],
        enable_da=True,
        num_rounds=2,
    ),
    # Memory condition — requires Ollama + sqlite-vec; skip if unavailable
    "multi_agent_da_memory": Condition(
        name="multi_agent_da_memory",
        description="3 agents + DA + memory tools (no web search)",
        participants=[GENERALIST, SPECIALIST, PHARMACOLOGIST, DEVILS_ADVOCATE],
        enable_da=True,
        enable_memory=True,
        num_rounds=2,
    ),
    # Full condition — all features enabled
    "full": Condition(
        name="full",
        description="3 agents + DA + memory + web search tools",
        participants=[GENERALIST, SPECIALIST, PHARMACOLOGIST, DEVILS_ADVOCATE],
        enable_da=True,
        enable_memory=True,
        enable_tools=True,
        num_rounds=2,
    ),
}
