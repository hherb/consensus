"""Entity profile management — CRUD operations.

Pure functions that operate on a Database instance. No dependency
on ConsensusApp.
"""

from typing import Optional

from .database import Database


def save_entity(
    db: Database,
    name: str,
    entity_type: str,
    avatar_color: str = "#3b82f6",
    provider_id: int = 0,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 1024,
    system_prompt: str = "",
    entity_id: int = 0,
) -> Optional[dict]:
    """Create or update a persistent entity profile.

    Args:
        db: Database instance.
        name: Display name for the entity.
        entity_type: One of "human", "ai", or "expert".
        avatar_color: Hex color for the entity's avatar.
        provider_id: ID of the AI provider (0 for humans).
        model: Model name (empty for humans).
        temperature: Sampling temperature for AI entities.
        max_tokens: Maximum tokens for AI responses.
        system_prompt: Custom system prompt for AI entities.
        entity_id: If nonzero, update this entity instead of creating.

    Returns:
        The entity dict from the database, or None on failure.
    """
    if entity_id:
        db.update_entity(
            entity_id, name=name, entity_type=entity_type,
            avatar_color=avatar_color, provider_id=provider_id,
            model=model, temperature=temperature,
            max_tokens=max_tokens, system_prompt=system_prompt,
        )
    else:
        entity_id = db.add_entity(
            name, entity_type, avatar_color, provider_id,
            model, temperature, max_tokens, system_prompt,
        )
    return db.get_entity(entity_id)


def delete_entity(db: Database, entity_id: int) -> dict:
    """Delete or deactivate an entity profile by ID.

    Args:
        db: Database instance.
        entity_id: ID of the entity to delete.

    Returns:
        Result dict from the database operation.
    """
    return db.delete_entity(entity_id)


def reactivate_entity(db: Database, entity_id: int) -> bool:
    """Reactivate a previously deactivated entity profile.

    Args:
        db: Database instance.
        entity_id: ID of the entity to reactivate.

    Returns:
        True if the entity was reactivated, False otherwise.
    """
    return db.reactivate_entity(entity_id)


def get_entities(db: Database) -> list[dict]:
    """Return all saved active entity profiles.

    Args:
        db: Database instance.

    Returns:
        List of entity dicts.
    """
    return db.get_entities()


def get_inactive_entities(db: Database) -> list[dict]:
    """Return all inactive (soft-deleted) entity profiles.

    Args:
        db: Database instance.

    Returns:
        List of inactive entity dicts.
    """
    return db.get_inactive_entities()
