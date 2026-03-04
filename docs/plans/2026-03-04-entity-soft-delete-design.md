# Entity Soft-Delete with Reactivation

**Date:** 2026-03-04
**Status:** Approved

## Problem

Deleting an entity that has participated in past discussions causes a foreign key violation (entities are referenced by `discussion_members`, `messages`, `storyboard_entries`, and `discussions.moderator_id`). Users need a way to retire entities without breaking historical data.

## Solution

Add an `active` column to the `entities` table. "Deleting" a referenced entity sets `active=0` instead. Unreferenced entities are still hard-deleted. A reactivation section in the Profiles tab allows restoring inactive entities.

## Database

- **Migration:** Add `active INTEGER NOT NULL DEFAULT 1` to `entities`. Check column existence in `Database.__init__`.
- **`delete_entity`:** Try hard DELETE first. On `IntegrityError`, catch and SET `active=0`. Return status dict (`{"deleted": true}` or `{"deactivated": true}`).
- **`reactivate_entity(entity_id)`:** New method — `UPDATE entities SET active=1 WHERE id=?`.
- **`get_entities(include_inactive=False)`:** Default filters `WHERE active=1`. Pass `True` for reactivation UI.

## App Layer

- `delete_entity` returns dict instead of bool.
- New `reactivate_entity(entity_id)` and `get_inactive_entities()` methods.
- `get_state()` continues returning only active entities.

## Server/Bridge

- Web: `/api/reactivate_entity`, `/api/get_inactive_entities` routes.
- Desktop: `reactivate_entity`, `get_inactive_entities` bridge methods.

## Frontend

- **Profiles tab:** Collapsible "Inactive Profiles" section at bottom with Reactivate buttons. Only shown when inactive entities exist.
- **Delete flow:** Confirmation dialog. If deactivated, show explanatory toast message.
- **New Discussion / History:** No changes. State already filters to active entities only; history shows names normally.

## Unchanged

- `Entity` dataclass — `active` is a DB/query concern, not exposed on the model.
- Discussion flow, messages, storyboard — untouched.
