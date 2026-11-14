"""Shared fixtures for consensus tests."""

import os
import tempfile

import pytest

# Helper modules don't get pytest's assertion rewriting by default;
# register them so their asserts keep full failure introspection.
pytest.register_assert_rewrite("tests.flow_e2e_helpers")

from consensus.database import Database
from consensus.models import (
    AIConfig, Discussion, Entity, EntityType, Message, MessageRole,
)


@pytest.fixture(autouse=True)
def offline_pricing(monkeypatch):
    """Keep the suite off the network: ``PricingCache.refresh`` downloads
    OpenRouter's catalogue on every app start and for unknown models.
    Tests of the refresh itself patch the instance, which still wins."""
    from consensus.pricing import PricingCache
    monkeypatch.setattr(PricingCache, "refresh", lambda self: False)


@pytest.fixture
def tmp_db(tmp_path):
    """Create a Database backed by a temporary SQLite file."""
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    yield db
    db.conn.close()


@pytest.fixture
def app(tmp_path):
    """A ConsensusApp backed by a temporary database.

    Modules that define their own ``app`` fixture override this one.
    """
    from consensus.app import ConsensusApp
    return ConsensusApp(db_path=str(tmp_path / "app.db"))


@pytest.fixture
def images_dir(tmp_path, monkeypatch):
    """Redirect image storage away from the real user data directory."""
    path = tmp_path / "images"
    path.mkdir()
    monkeypatch.setattr("consensus.tools_image.storage.get_images_dir", lambda: str(path))
    return path


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
