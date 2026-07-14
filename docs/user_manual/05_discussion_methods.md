# Chapter 5: Discussion Methods

Discussion methods are structured analytical frameworks that guide how a discussion unfolds. Each method divides the discussion into **phases** with specific goals, prompts, and rules. The moderator automatically manages phase transitions.

## Choosing a Method

Consider what you want from the discussion:

| Goal | Recommended Methods |
|------|-------------------|
| Explore a topic openly | Open Discussion |
| Generate and prioritise new ideas or options | Nominal Group Technique |
| Explore solution approaches broadly before committing | Tree of Thoughts |
| Make a decision between options | Weighted Decision Matrix (MCDA), Adversarial Collaboration, Voting |
| Resolve a genuine disagreement | Double Crux, Adversarial Collaboration |
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

### Nominal Group Technique (NGT)

Structured brainstorming — the catalog's generative method. Participants first propose ideas silently and independently (anonymised, so ideas are judged on content rather than authorship), the moderator merges duplicates into a candidate list, one clarification round ensures everyone understands each candidate, and then every participant distributes a fixed pool of points across the candidates. The result is a ranked shortlist.

**Phases:**
1. **Generate** — Silent, independent, anonymised idea generation
2. **Cluster** — The moderator merges duplicates into a candidate list
3. **Clarify** — One round of questions and refinement (no advocacy)
4. **Allocate** — Each participant distributes a fixed pool of points
5. **Rank** — The moderator presents the ranked shortlist

**Best for:** Open problem-solving, generating options, prioritising features or interventions, any question of the form "What should we do?" rather than "Is this right?"

---

### Weighted Decision Matrix (MCDA)

Multi-criteria decision analysis — the catalog's structured decision method. Participants enumerate the alternatives, jointly define the decision criteria with importance weights (1–5, averaged across participants), and score every option against every criterion (1–5). The weighted totals produce a ranked result, a deterministic sensitivity analysis shows whether the winner survives weight changes, and the moderator records a structured, machine-readable decision artifact (recommendation, rationale, ranking, divergence, caveats) that the storyboard or a follow-up discussion can consume.

**Phases:**
1. **Options** — Enumerate the decision alternatives
2. **Criteria & Weights** — Jointly define weighted decision criteria (locked before scoring)
3. **Score** — Every participant scores every option against every criterion
4. **Sensitivity** — The moderator presents the computed robustness analysis
5. **Decide** — The moderator records the structured decision

**Best for:** Making a concrete decision between identifiable options — "Should we do A, B, or C?" — where the trade-offs deserve explicit criteria rather than a straight vote.

---

### Double Crux

Disagreement resolution by crux-finding. Where adjudication formats (Court of Law, Red Team) sharpen positions, Double Crux searches for the underlying belief that actually drives the disagreement: each party names the factual claims that, if they were wrong about them, would change their mind (with a probability attached), the moderator identifies a shared crux — a claim multiple positions pivot on — and the discussion then focuses evidence on that crux alone. If no shared crux emerges, hunting repeats (up to a bounded number of rounds); if the disagreement turns out to rest on values rather than facts, that is reported as the finding. Each party finally restates their position and their probability on the crux, so belief shift is measured, and a machine-readable *crux map* records the outcome.

**Phases:**
1. **Positions** — Each party states their position and strongest reasons
2. **Crux Hunting** — "What claim, if you were wrong about it, would change your mind?"
3. **Crux Identification** — The moderator finds the shared crux (or loops back for more hunting)
4. **Crux Testing** — Evidence and reasoning focused on the crux alone (skipped for values differences)
5. **Resolution** — Final positions and belief restatement

**Best for:** Genuine two-sided disagreements where debate would only entrench positions — the outcome is either a resolution or a clean map: "the disagreement reduces to X" or "this is a values difference, not a factual one".

---

### Tree of Thoughts

Iterative parallel exploration for open problem-solving. Participants independently propose distinct solution approaches (anonymised so nobody anchors on anyone else), everyone scores every approach on feasibility, impact, and risk, and a deterministic *beam prune* keeps the strongest few. The survivors then get a deep-dive round — refinements and obstacles — and are re-scored in light of it; the score→prune→expand loop repeats until the ranking stabilises or a depth budget is spent. All composites and rankings are computed by the platform from the submitted scores, never by a model, and a machine-readable outcome artifact records the recommendation, the beam's trajectory across passes, and the obstacles raised.

**Phases:**
1. **Propose** — Anonymised independent generation of distinct solution approaches
2. **Score** — Everyone scores every surviving approach on feasibility, impact, and risk
3. **Prune** — The computed ranking cuts the field to the strongest few (the beam)
4. **Expand** — Deep-dive of the survivors: refinements and obstacles (then back to scoring)
5. **Synthesis** — The moderator presents the outcome and its trajectory

**Best for:** Open questions where the solution space should be explored broadly before committing — the generative counterpart to the evaluative methods, with an iterative refine-and-re-rank loop that NGT's single voting pass does not have.

---

## Method Transitions

Within a discussion, the moderator automatically manages phase transitions. You'll see phase announcements in the chat (e.g., "Moving to the Evidence phase") and in the turn badge at the top.

Methods cannot be changed mid-discussion — choose your method during setup.
