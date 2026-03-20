# Chapter 5: Discussion Methods

Discussion methods are structured analytical frameworks that guide how a discussion unfolds. Each method divides the discussion into **phases** with specific goals, prompts, and rules. The moderator automatically manages phase transitions.

## Choosing a Method

Consider what you want from the discussion:

| Goal | Recommended Methods |
|------|-------------------|
| Explore a topic openly | Open Discussion |
| Make a decision between options | Adversarial Collaboration, Voting |
| Forecast or estimate | Delphi Method, Belief State Diffusion |
| Identify risks and blind spots | Premortem Analysis, Red Team / Blue Team |
| Test whether reasoning is sound | Counterfactual Stress Testing, Recursive Self-Distillation |
| Evaluate competing explanations | Analysis of Competing Hypotheses |
| Challenge hidden assumptions | Key Assumptions Check |
| Break down a complex question | Recursive Decomposition |
| Not sure | Guided Triage (will recommend a method interactively) |

You can also use the **Suggest Method** button in the setup interface for LLM-powered recommendations.

---

## The Methods

### Open Discussion

The default method. Participants speak in round-robin order. The moderator summarises after each turn. If a Devil's Advocate is assigned, they receive a specialised prompt to challenge the group's reasoning.

**Phases:** Continuous discussion (no formal phase transitions)
**Best for:** Exploratory conversations, brainstorming, general analysis

---

### Analysis of Competing Hypotheses (ACH)

Developed by the CIA for intelligence analysis. Participants enumerate competing hypotheses, gather evidence, and rate each hypothesis against each piece of evidence. The focus is on **disconfirming** evidence — which hypotheses can we eliminate?

**Phases:**
1. **Hypothesize** — List all plausible hypotheses
2. **Evidence** — Gather evidence relevant to the hypotheses
3. **Evaluate** — Rate each hypothesis against each piece of evidence
4. **Analyse** — Identify diagnostic evidence and surviving hypotheses

**Best for:** Situations with multiple competing explanations and uncertain evidence

---

### Belief State Diffusion

Each participant maintains an explicit probability distribution over a set of hypotheses. After each round, participants see each other's distributions and update their own with justification. The method automatically detects convergence.

**Phases:**
1. **Frame** — Define the hypotheses to evaluate
2. **Prior** — Each participant states initial probabilities
3. **Diffuse** — Iterative rounds of belief updating
4. **Diagnose** — Analyse the final belief landscape

**Best for:** Quantitative estimation, tracking how opinions evolve, detecting false consensus

---

### Delphi Method

Classic forecasting technique. Participants provide independent estimates without seeing each other's responses. After each round, the moderator shares the statistical distribution and anonymised reasoning. Participants then revise their estimates.

**Phases:**
1. **Estimate** — Independent initial estimates
2. **Revise** — See distribution, revise with justification (multiple rounds until convergence)
3. **Synthesise** — Final aggregation

**Best for:** Numerical forecasting, expert elicitation, reducing anchoring bias

---

### Premortem Analysis

Assume the group has already reached a preliminary conclusion — and it turned out to be wrong. Each participant independently constructs a narrative explaining *why* it failed. This is psychologically easier than directly criticising a live idea.

**Phases:**
1. **Frame** — Establish the preliminary conclusion
2. **Premortem** — Each participant explains how and why it failed
3. **Consolidate** — Identify common failure modes and mitigation strategies

**Best for:** Risk assessment, stress-testing plans, surfacing concerns people might not voice directly

---

### Key Assumptions Check

Before diving into analysis, explicitly surface and challenge the assumptions underlying the question. Can function standalone or as a precursor to other methods.

**Phases:**
1. **Surface** — List all assumptions behind the question
2. **Challenge** — Critically examine each assumption
3. **Assess** — Determine which assumptions are well-supported and which are fragile

**Best for:** Complex questions where hidden assumptions could derail the analysis

---

### Adversarial Collaboration (Kahneman-style)

Inspired by Daniel Kahneman's approach to resolving expert disagreements. Parties who genuinely disagree first agree on the criteria and evidence that *would* settle the question — before presenting their cases. This prevents goalpost-shifting.

**Phases:**
1. **Positions** — Each side states their position clearly
2. **Criteria** — Jointly agree on what evidence would be decisive
3. **Evidence** — Present evidence against the agreed criteria
4. **Adjudicate** — Evaluate which position the evidence supports

**Best for:** Resolving genuine disagreements, structured debate, policy decisions

---

### Red Team / Blue Team

A rotating adversarial structure. The blue team constructs and refines a position. The red team's sole job is to attack it. The red team role rotates each round, so every participant takes a turn as attacker.

**Phases:**
1. **Construct** — Build the initial position (red team excluded)
2. **Attack** — Red team attempts to break the position
3. **Revise** — Blue team incorporates valid critiques
4. **Assess** — Final evaluation of the strengthened position

**Best for:** Security analysis, testing robustness of plans, finding weaknesses

---

### Participant Voting

Structured deliberation followed by formal voting. Participants propose motions using a specific format, then vote for, against, or abstain. Supports configurable thresholds.

**Phases:**
1. **Deliberate** — Discuss the topic and formulate motions
2. **Vote** — Cast votes on proposed motions
3. **Tally** — Count votes and announce results

**Voting thresholds:** Simple majority, supermajority, or unanimous (configurable)
**Best for:** Group decisions, prioritisation, democratic governance

---

### Counterfactual Stress Testing

For each key claim in a conclusion, systematically invert it and assess the impact. Claims are classified as:

- **LOAD-BEARING** — conclusion collapses without it
- **SUPPORTIVE** — weakens but doesn't break the conclusion
- **DECORATIVE** — conclusion survives without it

Each claim receives a 1–5 impact score.

**Phases:**
1. **Deliberate** — Initial discussion (optional, can also take a prior conclusion as input)
2. **Extract** — Identify key claims
3. **Stress Test** — Invert each claim and assess impact
4. **Synthesize** — Map which claims are truly essential

**Best for:** Evaluating the robustness of an argument, finding the weakest links in reasoning

---

### Recursive Decomposition

Breaks a complex question into independent sub-questions, analyses each separately, identifies cross-cutting patterns, and recomposes a unified answer.

**Phases:**
1. **Decompose** — Break the question into sub-questions
2. **Analyze** — Address each sub-question independently
3. **Integrate** — Find patterns and connections across sub-answers
4. **Recompose** — Build the final unified answer

**Best for:** Complex multi-faceted questions, systematic analysis, breaking down overwhelming problems

---

### Guided Triage

A meta-method that helps you choose the right method. The moderator interviews participants about the problem's characteristics, then recommends a method. The group confirms and the discussion switches to the recommended method.

**Phases:**
1. **Intake** — Moderator gathers information about the problem
2. **Recommend** — LLM recommends a method with reasoning
3. **Confirm** — Group approves or adjusts the recommendation

**Best for:** When you're not sure which method to use

---

### Recursive Self-Distillation

An LLM-native method designed to separate persuasive rhetoric from valid reasoning. Generates rich reasoning, strips it to a bare logical skeleton (premises, inferences, conclusion), then blind-evaluates only the skeleton.

**Phases:**
1. **Deliberate** — Generate rich, detailed reasoning
2. **Distill** — Strip to pure logical skeleton
3. **Blind Evaluate** — Assess only the logical structure
4. **Synthesize** — Compare rhetorical and logical versions

**Best for:** Testing whether an argument is genuinely sound or just well-written, separating style from substance

---

## Method Transitions

Within a discussion, the moderator automatically manages phase transitions. You'll see phase announcements in the chat (e.g., "Moving to the Evidence phase") and in the turn badge at the top.

Methods cannot be changed mid-discussion — choose your method during setup.
