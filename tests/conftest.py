"""Shared fixtures for consensus tests."""

import os
import tempfile

import pytest

from consensus.database import Database
from consensus.models import (
    AIConfig, Discussion, Entity, EntityType, Message, MessageRole,
)


@pytest.fixture
def tmp_db(tmp_path):
    """Create a Database backed by a temporary SQLite file."""
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    yield db
    db.conn.close()


@pytest.fixture
def sample_provider(tmp_db):
    """Insert a provider and return its ID."""
    return tmp_db.add_provider("TestProvider", "http://localhost:11434/v1", "TEST_API_KEY")


@pytest.fixture
def sample_ai_entity(tmp_db, sample_provider):
    """Insert an AI entity and return its ID."""
    return tmp_db.add_entity(
        "Alice", "ai", "#ff0000", sample_provider,
        "test-model", 0.5, 512, "You are Alice.",
    )


@pytest.fixture
def sample_human_entity(tmp_db):
    """Insert a human entity and return its ID."""
    return tmp_db.add_entity("Bob", "human", "#00ff00")


@pytest.fixture
def discussion_with_entities(tmp_db, sample_ai_entity, sample_human_entity):
    """Create a Discussion object with two entities loaded from DB."""
    ai_row = tmp_db.get_entity(sample_ai_entity)
    human_row = tmp_db.get_entity(sample_human_entity)
    ai_entity = Entity.from_db_row(ai_row)
    human_entity = Entity.from_db_row(human_row)

    disc = Discussion(
        topic="Test topic",
        entities=[ai_entity, human_entity],
        moderator_id=ai_entity.id,
        turn_order=[ai_entity.id, human_entity.id],
        current_turn_index=0,
        turn_number=1,
        is_active=True,
        status="active",
    )
    return disc
