# Consensus: A Platform for Collective Intelligence Through Structured Multi-Agent Deliberation

**[YOUR NAME]**$^{1}$
$^{1}$[YOUR AFFILIATION]
Correspondence: [YOUR EMAIL]

---

## Abstract

We present Consensus, an open-source platform for structured multi-party deliberation between human and artificial intelligence participants. Unlike conventional single-agent dialogue systems, Consensus orchestrates moderated discussions among multiple AI entities and human participants, incorporating persistent institutional memory, adversarial critique through a dedicated Devil's Advocate role, and extensible domain-specialist access via tool providers. The platform implements three interlocking memory subsystems — per-entity personal memory, semantic search over historical discussions, and a shared knowledge graph — enabling participants to build cumulative understanding across sessions. We describe the system architecture, detail the design rationale grounded in empirical findings from multi-agent debate, AI-assisted deliberation, and memory-augmented agent research, and discuss application scenarios in software engineering, medical decision support, and ethical reasoning. Consensus integrates individually validated techniques — multi-agent debate, structured adversarial roles, persistent memory, and tool-augmented generation — into a unified, user-facing platform for systematic cooperative inquiry.

**Keywords:** multi-agent systems, collective intelligence, deliberation, large language models, institutional memory, adversarial reasoning, human-AI collaboration

---

## 1. Introduction

The dominant paradigm for interacting with large language models (LLMs) remains a single user conversing with a single model in a stateless session. While effective for bounded tasks such as drafting, summarisation, and question answering, this paradigm is fundamentally inadequate for problems that demand rigour: questions that shape policy, guide clinical decisions, or determine engineering trade-offs require multiple perspectives, adversarial testing, accumulated knowledge, and access to verifiable evidence. They require *deliberation*, not mere generation.

A growing body of research supports multi-agent interaction as a means of improving LLM outputs. Du et al. [1] demonstrated that multi-agent debate significantly improves factuality and mathematical reasoning. Chiang et al. [2] showed that LLM-powered devil's advocates enhance group decision-making accuracy. Surveys on memory in multi-agent systems [3] highlight the importance of persistent knowledge for coherent long-term reasoning. However, these techniques have been studied largely in isolation, as inference-time improvements or research prototypes, rather than as components of an integrated deliberation platform.

In this paper, we present Consensus, an open-source platform that combines structured multi-party discussion, persistent institutional memory, adversarial critique, and extensible tool access into a unified framework for collective intelligence. Consensus supports both fully autonomous AI panels and hybrid configurations where human participants collaborate with AI entities under shared moderation. The platform is designed around a particular theory of how groups reason well: enforced turn-taking prevents dominant voices from drowning out others, moderation keeps discussions productive, persistent memory builds cumulative understanding, adversarial testing catches errors before they solidify, and domain-specialist access grounds deliberation in evidence.

The contributions of this paper are:

1. **System design:** We describe the architecture of a platform that integrates multi-agent debate, institutional memory (personal memory, semantic corpus search, and knowledge graph), adversarial critique (Devil's Advocate role), and tool-augmented generation within a moderated discussion framework.

2. **Design rationale:** We ground each architectural decision in empirical findings from the multi-agent reasoning, AI-assisted deliberation, and memory-augmented agent literatures, demonstrating that Consensus synthesises individually validated techniques.

3. **Application scenarios:** We present concrete use cases in software engineering, medical decision support, and ethical reasoning that illustrate the combinatorial benefits of the integrated approach.

4. **Open-source implementation:** We release the platform as open-source software under the GNU AGPL-3.0 license, enabling reproducibility and community extension.

[TODO: Add a brief paragraph summarising any empirical evaluation results, if you plan to include a formal evaluation section before submission.]

The remainder of this paper is organised as follows. Section 2 reviews related work. Section 3 describes the system architecture. Section 4 details the institutional memory system. Section 5 presents the Devil's Advocate mechanism. Section 6 discusses the specialist plugin framework. Section 7 illustrates application scenarios. [TODO: Section 8 presents evaluation results, if applicable.] Section 8 [or 9] discusses limitations and future work. Section 9 [or 10] concludes.


## 2. Related Work

### 2.1 Multi-Agent Debate Frameworks

The use of multi-agent debate to improve LLM reasoning has been explored extensively. Liang et al. [4] introduced the Multi-Agents Debate (MAD) framework, one of the first to explore structured argumentation between LLM agents. Du et al. [1] provided rigorous empirical evidence at ICML 2024 that debate among multiple agents improves both factuality and reasoning over single-agent baselines. Zhang et al. [5] extended this to competitive debate with dynamic agent coordination, achieving human-level performance (Agent4Debate, ICASSP 2026). Kim et al. [6] validated that the approach generalises to open-source models (LLM-Agora).

Recent work has refined the understanding of when and why multi-agent debate works. Li et al. [7] identified the "lazy agent" problem — where one agent dominates while others contribute minimally — motivating Consensus's enforced turn-taking. Controlled experiments by [8] found that diverse reasoning paths and explicit role assignments are critical success factors, both core features of Consensus. A hashgraph-inspired consensus mechanism [9] has explored formal distributed-systems protocols for multi-model agreement, suggesting paths toward theoretical guarantees.

### 2.2 Adversarial Reasoning and Devil's Advocacy

The Devil's Advocate has a long history as a deliberation technique [TODO: cite historical/organisational behaviour references, e.g. Janis (1972) on groupthink, Schweiger et al. (1986) on dialectical inquiry]. Recent work has applied the concept to AI-mediated group decision-making. Chiang et al. [2] demonstrated that groups with an LLM-powered devil's advocate achieved significantly higher accuracy on decision-making tasks, circumventing the self-censorship that undermines human devil's advocacy under social pressure. Park et al. [10] extended this to equity-focused applications, using AI-mediated devil's advocacy to amplify marginalised perspectives. The RedDebate framework [11] demonstrated that adversarial debate can serve as a safety mechanism, with agents red-teaming each other to identify unsafe behaviours. Estornell et al. [12] formalised the pattern through D3 (Debate, Deliberate, Decide), defining role-specialised agents — advocates, judges, and juries — that map onto Consensus's existing architecture.

### 2.3 Memory Systems for AI Agents

Persistent memory is essential for coherent multi-session reasoning. Packer et al. [13] (Letta/MemGPT) introduced an OS-inspired memory hierarchy where agents actively manage core, conversational, and archival memory tiers. Chhikara et al. [14] (Mem0) provided a lightweight memory engine with graph-based storage. The MemOS preprint [15] proposed a full memory operating system with conflict detection, deduplication, versioning, and forgetting policies. A comprehensive survey [3] catalogues memory mechanisms across multi-agent systems.

These systems primarily address single-agent persistence. Consensus extends the paradigm to *multi-party deliberation*, where personal memory, a shared discussion corpus, and a collective knowledge graph serve as complementary layers of institutional knowledge.

### 2.4 Structured Human Deliberation

Structured deliberation has a rich tradition in human-centred platforms. Kialo [16] provides hierarchical argument trees for mapping the structure of debates. Polis [17], the open-source civic deliberation platform used in Taiwanese legislative processes, clusters opinions from large groups to surface consensus. Small et al. [18] have explored integrating LLMs with Polis for AI-assisted moderation and summarisation.

These platforms serve human-only deliberation. Consensus bridges the gap by supporting hybrid human-AI panels within a shared deliberative framework.

### 2.5 Multi-Agent Medical Decision Systems

Medical decision-making has emerged as a compelling application for multi-agent AI. The Multi-Agent Conversation (MAC) framework [19], published in *npj Digital Medicine*, achieved improved diagnostic accuracy using supervisor and doctor agents inspired by clinical multi-disciplinary teams. MDAgents [20] introduced adaptive complexity routing, escalating complex cases to multi-disciplinary panels. The Multi-Agent Medical Decision Consensus Matrix [21] achieved 89.3% consensus rates with measurably improved accuracy through structured opinion aggregation. TeamMedAgents [22] demonstrated 2–10 percentage point improvements over single-agent baselines. Multi-agent systems have also been shown to mitigate clinical decision biases, improving accuracy from 0% to 76% on bias-containing complex cases [23].

### 2.6 General-Purpose Agent Frameworks

General-purpose multi-agent frameworks such as AutoGen [24] and CrewAI [25] provide conversation-driven and role-based agent orchestration, respectively. These serve as infrastructure for multi-agent interaction but do not encode deliberation-specific features — adversarial roles, institutional memory, knowledge graphs, or moderated turn-taking — that support structured inquiry. Consensus is more opinionated by design, implementing a particular theory of productive group reasoning.


## 3. System Architecture

### 3.1 Overview

Consensus is a dual-mode application supporting both desktop (via pywebview with a JavaScript bridge) and web (via aiohttp REST API) interfaces, both routing through a shared orchestrator (`ConsensusApp`). The architecture comprises four core components:

1. **ConsensusApp** — the central orchestrator managing discussion state, participant coordination, and callback dispatch.
2. **Moderator** — responsible for discussion flow, turn-taking, AI response generation, and synthesis.
3. **AIClient** — an asynchronous HTTP client targeting any OpenAI-compatible API endpoint, enabling provider diversity.
4. **Database** — thread-safe SQLite persistence for entities, discussions, messages, prompts, and tool configuration.

```
Frontend (static HTML/CSS/JS)
    |  pywebview bridge OR aiohttp REST API
ConsensusApp (orchestrator, state management)
    |-- Moderator (turn flow, AI generation, summaries)
    |-- AIClient (async OpenAI-compatible HTTP client)
    |-- Database (thread-safe SQLite persistence)
    |-- ToolRegistry (tool providers, access control)
```

### 3.2 Discussion Lifecycle

A discussion proceeds as follows:

1. **Setup.** The user selects a topic, configures participants (each with an assigned AI provider, model, and role), and optionally designates a Devil's Advocate.
2. **Turn order determination.** The moderator establishes participant ordering, with the Devil's Advocate placed last to ensure it responds to the strongest formulations of each position.
3. **Turn execution.** For each participant, the system: (a) builds a context comprising the system prompt, discussion history, and available tool schemas; (b) sends the context to the participant's LLM; (c) iteratively executes any tool calls (up to 5 iterations with a 30-second timeout); (d) records the final text response and tool call history.
4. **Interim synthesis.** After each turn, the moderator generates a summary of the discussion state.
5. **Conclusion.** When the discussion concludes, the moderator generates a final synthesis, and results are stored to memory and the knowledge graph.

### 3.3 Provider Abstraction

The `AIClient` targets any OpenAI-compatible API endpoint. A provider registry allows users to configure multiple backends (e.g., OpenAI, Anthropic via proxy, local models via Ollama or vLLM). This design ensures provider diversity — participants in a single discussion can use different models, reducing the risk of correlated failure modes inherent in single-model multi-agent setups.

### 3.4 Tool System

The tool architecture is built on a `ToolProvider` abstraction supporting both local Python implementations and external Model Context Protocol (MCP) [26] servers. The `ToolRegistry` manages access control with three modes: *private* (accessible only to the assigned entity), *shared* (accessible to all participants), and *moderator_only*. Tool assignments can be configured per-entity with per-discussion overrides. The `AIClient` implements `complete_with_tools()` using native OpenAI function calling for tool invocation.

### 3.5 Multi-User Deployment

For web deployment, Consensus supports a multi-user mode where each browser session receives an isolated `ConsensusApp` instance and SQLite database, managed by a `SessionManager` with TTL-based expiry. Authentication supports email/password registration (PBKDF2-SHA256, 600k iterations) and OAuth via GitHub, Google, LinkedIn, and Apple. API keys follow a Bring Your Own Key (BYOK) model: user-provided keys are transmitted per-request and never persisted server-side.


## 4. Institutional Memory

A central design principle of Consensus is that deliberation should be *cumulative* — each discussion should build on the knowledge accumulated in prior sessions, rather than starting from scratch. To this end, Consensus implements three interlocking memory subsystems.

### 4.1 Personal Memory

Each AI entity maintains a personal memory store of observations, positions, and insights that persist across discussions. Entries are embedded as 768-dimensional vectors (via Ollama using `nomic-embed-text`) and retrieved by cosine similarity. An entity that has debated the ethics of algorithmic sentencing can recall its prior positions when the topic resurfaces, notice contradictions with new evidence, and evolve its thinking. Default system prompts instruct participants to search personal memory before responding and to store key insights after contributing.

### 4.2 Semantic Discussion Search

The full corpus of past discussions is indexed using the same embedding pipeline. Any participant can perform semantic search across all historical messages, finding relevant passages even when vocabulary differs — a discussion of drug interactions can surface insights from an earlier conversation about metabolic pathways. Indexing is lazy: unindexed messages are embedded in the background on first query, ensuring the system remains responsive.

### 4.3 Knowledge Graph

A shared knowledge graph captures structured relationships between concepts as subject-predicate-object triples (e.g., *free will* --[contradicts]--> *hard determinism*). Nodes are embedded for semantic search; edges encode typed relationships. Participants can assert new relationships and query existing ones, building a navigable map of how ideas connect across discussions. Over time, this graph becomes a cumulative representation of the group's collective understanding.

### 4.4 Design Rationale

The three memory layers serve complementary functions: personal memory tracks individual intellectual trajectories, discussion search surfaces evidence and arguments from the historical corpus, and the knowledge graph captures structured relationships that transcend individual conversations. This layered design parallels proposals in the memory systems literature — Letta's [13] tiered hierarchy, Mem0's [14] graph-based storage, and MemOS's [15] full memory operating system — while extending them from single-agent to multi-party contexts.


## 5. Adversarial Critique: The Devil's Advocate Role

### 5.1 Design

Groupthink — the tendency of deliberating groups to converge prematurely on comfortable conclusions — is among the most well-documented failures in collective decision-making [TODO: cite Janis 1972]. Consensus addresses this structurally through the Devil's Advocate role, which modifies participant behaviour in four ways:

1. **Specialised prompts.** Standard participant instructions are replaced with a mandate for constructive critical analysis, directing the entity to identify factual errors, logical fallacies, unsupported claims, unstated assumptions, and missing perspectives.
2. **Automatic tool assignment.** The Devil's Advocate receives immediate access to web search and the full memory toolkit, with explicit instructions to fact-check claims and search for contradicting evidence.
3. **Turn order placement.** The Devil's Advocate speaks last in each round, ensuring access to the full picture of arguments before responding.
4. **Single-advocate enforcement.** Only one participant holds the role at any time, preventing discussion degradation into a chorus of criticism.

### 5.2 Rationale

This design mirrors best practices from academic peer review, security red-teaming, and moot court. Empirical support comes from Chiang et al. [2], who found that LLM-powered devil's advocates bypass the self-censorship that undermines human devil's advocacy; Park et al. [10], who demonstrated the approach can amplify marginalised perspectives; and the D3 framework [12], which formalises role-specialised deliberation agents. The RedDebate framework [11] further suggests that the same adversarial pattern could serve as a safety mechanism through mutual red-teaming.


## 6. Specialist Plugins and Tool Integration

### 6.1 Architecture

Consensus's tool system enables domain-specialist access during discussions. The `ToolProvider` abstraction supports local Python implementations (e.g., the built-in `WebSearchProvider` using Brave Search API with DuckDuckGo fallback) and is designed to support external MCP servers [26]. The tool registry handles access control, assignment, and execution within the discussion flow.

### 6.2 Design and Rationale

This design parallels how expert consultation works in practice: a clinical panel calls in a radiologist; an architecture review engages a security specialist. In Consensus, these consultations occur within the discussion flow, with results visible to all participants and subject to the same critical scrutiny as any other contribution.

The Model Context Protocol, now an open standard under the Linux Foundation with over 10,000 public servers, provides the interoperability layer for specialist plugins. MCP servers already exist for PubMed, legal databases, code repositories, and numerous domain-specific knowledge sources, enabling rapid specialist integration.

### 6.3 Current Status

The tool infrastructure is operational with built-in web search. The MCP integration interface (`MCPToolProvider`) is designed but not yet implemented. [TODO: Update this section if MCP integration is completed before submission.]


## 7. Application Scenarios

The features described above are individually useful, but their power is combinatorial. We illustrate this through three application scenarios.

### 7.1 Software Engineering: Technology Evaluation

A team evaluating a database migration could convene a discussion with participants representing performance, operational complexity, data integrity, and migration risk perspectives. The Devil's Advocate challenges optimistic assumptions using web search to find post-mortems from similar migrations. Memory tools recall conclusions from prior architecture discussions. A database specialist plugin queries benchmark databases and compatibility matrices. The moderator synthesises the discussion into a structured decision document preserving dissenting views and supporting evidence.

### 7.2 Medical Decision Support

A complex patient case could involve AI participants with different clinical perspectives — generalist, organ-system specialist, and pharmacologist. The Devil's Advocate fact-checks claims against current evidence via web search and PubMed. Memory tools recall previously discussed cases. The knowledge graph captures relationships between conditions, treatments, and outcomes. The discussion produces a structured analysis of options, evidence quality, and areas of genuine uncertainty — aligning with the multi-disciplinary team approach validated by MAC [19], MDAgents [20], and TeamMedAgents [22].

### 7.3 Ethical Reasoning

A discussion on the ethics of predictive policing could include participants representing utilitarian, deontological, and virtue ethics frameworks. The Devil's Advocate challenges each framework's blind spots. The knowledge graph accumulates conceptual relationships across sessions, building an increasingly nuanced map of the ethical landscape.

### 7.4 Flexible Human-AI Collaboration

A defining feature of Consensus is that humans and AI participants coexist as equals within the deliberative framework. A human moderator can guide AI specialists; a human expert can contribute alongside AI participants handling literature search; or a fully autonomous AI panel can deliberate while a human observer intervenes as needed. The optimal level of human involvement varies by context, and the platform does not prescribe a fixed balance.


## 8. Evaluation

[TODO: This section should present empirical evaluation of the platform. Consider the following approaches, selecting those feasible before submission:]

[TODO: **Task-based evaluation.** Compare discussion outputs (e.g., decision quality, factual accuracy, argument completeness) between Consensus and single-agent baselines on structured tasks. Potential benchmarks include medical case vignettes, ethical dilemmas with expert-annotated solutions, or software architecture decision scenarios.]

[TODO: **Ablation study.** Measure the contribution of individual components (memory, Devil's Advocate, tool access) by selectively disabling them and comparing output quality.]

[TODO: **User study.** Collect qualitative and quantitative feedback from users engaging with the platform in realistic scenarios. Report on perceived utility, discussion quality, and trust calibration.]

[TODO: **Comparison with existing systems.** Benchmark against single-agent chat, AutoGen, or other multi-agent frameworks on shared tasks.]

[TODO: At minimum, report the experimental setup, metrics, results, and statistical significance. Even a small-scale pilot evaluation strengthens the paper substantially.]


## 9. Limitations and Future Work

**Evaluation.** [TODO: Adjust based on what evaluation is included.] The current work presents the system design and rationale but [lacks/includes limited] empirical evaluation. Formal benchmarking against single-agent baselines and existing multi-agent frameworks is needed to quantify the benefits of the integrated approach.

**Scalability.** The current implementation uses SQLite for persistence and local Ollama for embeddings, appropriate for single-user and small-group deployments but not for large-scale concurrent use. Migration to a client-server database and hosted embedding services would be required for production deployment at scale.

**Knowledge graph maintenance.** As the knowledge graph grows, mechanisms for conflict resolution, deduplication, and obsolescence management become necessary. The MemOS proposal [15] offers relevant strategies that could be adapted.

**MCP integration.** The planned MCPToolProvider interface would connect Consensus to the growing ecosystem of domain-specialist servers but is not yet implemented.

**Evaluation of emergent dynamics.** The interaction between memory, adversarial critique, and tool access may produce emergent deliberation dynamics — both positive and negative — that require systematic study.

**Bias and safety.** Multi-agent systems can amplify biases present in underlying models. While the Devil's Advocate role provides some mitigation, systematic analysis of bias propagation and mitigation in multi-party deliberation is an important direction.

**Consensus mechanisms.** The platform currently relies on moderator synthesis for reaching conclusions. Formal consensus mechanisms — voting, weighted agreement, or distributed-systems-inspired protocols [9] — could provide more structured convergence.


## 10. Conclusion

We have presented Consensus, an open-source platform for structured multi-party deliberation that integrates moderated discussion, persistent institutional memory, adversarial critique, and tool-augmented generation. The platform synthesises techniques that have been individually validated in the multi-agent debate, memory systems, and AI-assisted deliberation literatures, combining them into a unified framework for systematic cooperative inquiry. By supporting flexible human-AI collaboration — from fully autonomous AI panels to hybrid configurations — Consensus provides a foundation for investigating how groups of human and artificial agents can reason carefully together.

The platform is available as open-source software under the GNU AGPL-3.0 license at [TODO: INSERT REPOSITORY URL].


## Acknowledgements

[TODO: Acknowledge contributors, funding sources, computational resources, etc.]


## References

[1] Y. Du, S. Li, A. Torralba, J. B. Tenenbaum, and I. Mordatch, "Improving Factuality and Reasoning in Language Models through Multiagent Debate," in *Proc. ICML*, 2024.

[2] C.-W. Chiang, Z. Lu, Z. Li, and M. Yin, "Enhancing AI-Assisted Group Decision Making through LLM-Powered Devil's Advocate," in *Proc. ACM IUI*, 2024.

[3] "Memory in LLM-based Multi-agent Systems: Mechanisms, Challenges, and Collective," *TechRxiv*, 2025.

[4] T. Liang et al., "Encouraging Divergent Thinking in Large Language Models through Multi-Agent Debate," arXiv preprint arXiv:2305.19118, 2023.

[5] Y. Zhang et al., "Agent4Debate: Dynamic Multi-Agent Framework for Competitive Debate," in *Proc. ICASSP*, 2026.

[6] S. Kim et al., "LLM-Agora: Debating between Open-Source LLMs," arXiv preprint, 2023.

[7] Z. Li et al., "Unlocking the Power of Multi-Agent LLM for Reasoning: From Lazy Agents to Deliberation," arXiv preprint arXiv:2511.02303, 2025.

[8] "Can LLM Agents Really Debate?" arXiv preprint arXiv:2511.07784, 2025.

[9] "A Hashgraph-Inspired Consensus Mechanism for Reliable Multi-Model Reasoning," arXiv preprint arXiv:2505.03553, 2025.

[10] J. Park et al., "Amplifying Minority Voices: AI-Mediated Devil's Advocate System for Inclusive Group Decision-Making," arXiv preprint arXiv:2502.06251, 2025.

[11] Y. Chen et al., "RedDebate: Safer Responses through Multi-Agent Red Teaming Debates," arXiv preprint arXiv:2506.11083, 2025.

[12] A. Estornell et al., "D3: Debate, Deliberate, Decide — A Cost-Aware Adversarial Framework," arXiv preprint arXiv:2410.04663, 2024.

[13] C. Packer et al., "MemGPT: Towards LLMs as Operating Systems," arXiv preprint arXiv:2310.08560, 2023.

[14] P. Chhikara et al., "Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory," arXiv preprint arXiv:2504.19413, 2025.

[15] MemTensor, "MemOS: A Memory OS for AI Systems," Preprint, 2025.

[16] "Kialo," https://www.kialo.com.

[17] "Polis: Open-Source Platform for Civic Deliberation," https://compdemocracy.org/polis/.

[18] C. Small et al., "Opportunities and Risks of LLMs for Scalable Deliberation with Polis," arXiv preprint arXiv:2306.11932, 2023.

[19] Z. Lin et al., "Enhancing Diagnostic Capability with Multi-Agent Conversational LLMs," *npj Digital Medicine*, 2025.

[20] J. Kim et al., "MDAgents: An Adaptive Collaboration of LLMs for Medical Decision-Making," arXiv preprint arXiv:2404.15155, 2024.

[21] "Multi-Agent Medical Decision Consensus Matrix," arXiv preprint arXiv:2512.14321, 2025.

[22] "TeamMedAgents: Enhancing Medical Decision-Making," arXiv preprint arXiv:2508.08115, 2025.

[23] "A Survey of LLM-based Multi-agent Systems in Medicine," *TechRxiv*, 2025.

[24] Microsoft, "AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation," arXiv preprint arXiv:2308.08155, 2023.

[25] CrewAI, https://www.crewai.com.

[26] Anthropic, "Introducing the Model Context Protocol," 2024. https://modelcontextprotocol.io.

[TODO: Complete all reference entries with full author lists, titles, volume/page numbers, and DOIs where available. Verify arXiv IDs are correct. Add any missing references cited in the text (e.g., Janis 1972, Schweiger et al. 1986).]
