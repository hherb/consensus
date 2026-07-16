"""Shared driver for the real-pipeline method-flow E2E tests.

Builds an all-human discussion through the production
``start_discussion`` and drives it turn by turn through
``submit_human_message`` + ``complete_turn`` (the human-moderator
summary path — no network, no stubs), tracing the ``(phase, speaker)``
of every turn until the method completes.

Spec: docs/superpowers/specs/2026-07-16-method-flow-e2e-tests-design.md
"""

from __future__ import annotations

import json
from typing import Callable

import pytest

from consensus.app_discussion_flow import complete_turn, submit_human_message
from consensus.app_discussion_setup import start_discussion
from consensus.database import Database
from consensus.models import Discussion, Entity
from consensus.moderator import Moderator
from consensus.pricing import PricingCache

#: Hard turn budget per E2E run — a flow regression must fail the test,
#: never hang the suite.  The longest scripted run (Double Crux) takes
#: 14 turns; 40 leaves headroom without masking a runaway loop.
MAX_E2E_TURNS = 40


def make_entity(db: Database, name: str) -> Entity:
    """Insert a human entity and return the loaded Entity."""
    eid = db.add_entity(name, "human", "#123456")
    return Entity.from_db_row(db.get_entity(eid))


def start_method_discussion(
    db: Database, method_name: str, n_participants: int, topic: str,
) -> tuple[Discussion, Moderator, PricingCache, Entity, list[Entity]]:
    """Start a real discussion: human moderator 'Mod' + P1..Pn.

    Everything (DB record, members, turn order, method init_state, the
    first phase's turn order) is set up by the production
    ``start_discussion`` — nothing is pre-seeded.
    """
    mod = make_entity(db, "Mod")
    parts = [make_entity(db, f"P{i + 1}") for i in range(n_participants)]
    disc = Discussion(
        topic=topic,
        entities=[mod] + parts,
        moderator_id=mod.id,
        discussion_method=method_name,
    )
    moderator = Moderator(disc, db)
    result = start_discussion(disc, db, moderator)
    assert result.get("started") is True, f"start_discussion failed: {result}"
    pricing = PricingCache(db.conn, db._lock)
    return disc, moderator, pricing, mod, parts


async def run_method(
    disc: Discussion, moderator: Moderator, db: Database,
    pricing: PricingCache,
    content_for: Callable[[Discussion, Entity], str],
) -> tuple[list[tuple[str, str]], dict]:
    """Drive turns until ``method_complete``; return (trace, final result).

    Each iteration submits the current speaker's scripted content (from
    ``content_for``, which reads the live ``method_state``) and completes
    the turn with a human-moderator summary.  Every step is asserted so
    a failure points at the exact turn; the ``MAX_E2E_TURNS`` budget
    turns a runaway loop into a failure with the full trace.
    """
    trace: list[tuple[str, str]] = []
    for _ in range(MAX_E2E_TURNS):
        speaker = disc.current_speaker
        assert speaker is not None, f"no current speaker; trace={trace}"
        phase = disc.method_state.get("current_phase", "")
        content = content_for(disc, speaker)
        submitted = submit_human_message(disc, db, speaker.id, content)
        assert "error" not in submitted, (
            f"submit failed in {phase!r} for {speaker.name}: {submitted}")
        result = await complete_turn(
            disc, moderator, db, pricing,
            get_state_fn=lambda: {},
            moderator_summary="Summary of the turn.",
        )
        assert "error" not in result, (
            f"complete_turn failed in {phase!r}: {result}")
        trace.append((phase, speaker.name))
        if result.get("method_complete"):
            return trace, result
    pytest.fail(
        f"method never completed within {MAX_E2E_TURNS} turns; "
        f"trace={trace}")


def db_method_state(db: Database, discussion_id: int) -> dict:
    """The persisted method_state, parsed from the discussion's DB row."""
    row = db.get_discussion(discussion_id)
    return json.loads(row.get("method_state") or "{}")
