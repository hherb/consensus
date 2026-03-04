# Roadmap

This document outlines planned features and long-term goals for Consensus, roughly ordered by priority and dependency.

## Discussion Continuity

- **Resume previous discussions** — reopen a past discussion and continue from where it left off, preserving full context, storyboard state, and participant configuration
- **Dynamic participation** — allow entities to join or leave an ongoing discussion mid-session without disrupting the flow

## Institutional Memory

- **Long-term memory for AI participants** — give participating AIs persistent memory across discussions so they can build on prior reasoning and positions
- **Semantic search over past discussions** — enable AI participants to retrieve relevant passages from the corpus of past (public) discussions using embedding-based search
- **Knowledge graph** — build and query a graph of concepts, positions, and relationships extracted from discussion history to support richer, more connected argumentation

## Research-Grade Argumentation

- **Web search and tool access** — equip participants with the ability to search the web and use external tools during discussions, producing properly referenced and evidence-backed arguments

## Democratic Moderation

- **Moderation challenges** — allow entities to formally challenge the moderator's summaries, conclusions, or procedural decisions, triggering a review by participant consensus
- **Moderator elections** — enable participants to vote for a new moderator or propose a change of moderation during a discussion

## Authentication & Identity

- **Registration and authentication system** — implement proper user accounts, identity verification, and session management for human participants

## Public Service

- **Security hardening** — audit and harden the platform for public-facing deployment (input validation, rate limiting, access control, abuse prevention)
- **Free hosted instance** — once sufficiently hardened, offer Consensus as a free web service (contingent on finding an affordable and reliable host)

## Training Data & Model Development

- **Open-source reasoning datasets** — harvest high-quality training data from meaningful discussions that reach helpful consensus, published as open-source datasets for reasoning AI research
- **Small moderator models** — use collected data specifically to train competent, lightweight moderator models capable of running on local consumer hardware
