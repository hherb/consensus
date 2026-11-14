"""Field checks for the evaluation UI's JSON request bodies (#109).

Pure functions: each checker takes a field's value and returns a problem
description, or ``None`` when the value is acceptable. ``body_problem``
applies a field table to a body and returns the first problem as a
user-facing message naming the field. The routes in ``eval_routes.py``
turn that message into a 400, so a hand-made request with a ``null``, a
number or an unknown enum value is refused before it reaches the database.
"""

from collections.abc import Callable, Collection, Mapping

from consensus.evaluation.constants import (
    CASE_DIFFICULTIES,
    PARTICIPANT_ROLES,
    SQLITE_MAX_INTEGER,
)

#: A field checker: the value's problem, or ``None`` if it is acceptable.
Checker = Callable[[object], str | None]


def text(value: object) -> str | None:
    """A string (possibly empty)."""
    return None if isinstance(value, str) else "must be a string"


def text_list(value: object) -> str | None:
    """A list of strings (possibly empty)."""
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return None
    return "must be a list of strings"


def flag(value: object) -> str | None:
    """A JSON boolean — not a truthy string or number."""
    return None if isinstance(value, bool) else "must be true or false"


def _storable_int(value: object) -> bool:
    """An ``int`` (not ``bool``) of 1 to ``SQLITE_MAX_INTEGER``."""
    return (isinstance(value, int) and not isinstance(value, bool)
            and 1 <= value <= SQLITE_MAX_INTEGER)


def positive_int(value: object) -> str | None:
    """A whole number of at least 1 that an SQLite INTEGER can hold."""
    if _storable_int(value):
        return None
    return "must be a whole number of at least 1"


def id_list(value: object) -> str | None:
    """A list of row ids (whole numbers an SQLite INTEGER can hold)."""
    if isinstance(value, list) and all(_storable_int(v) for v in value):
        return None
    return "must be a list of whole-number ids"


def one_of(choices: Collection[str]) -> Checker:
    """A checker accepting only the given strings."""
    def check(value: object) -> str | None:
        # ``isinstance`` first: a list or dict is unhashable in a set lookup.
        if isinstance(value, str) and value in choices:
            return None
        return "must be one of " + ", ".join(sorted(choices))
    return check


#: Fields of one participant in a condition (``name`` is required).
PARTICIPANT_FIELDS: Mapping[str, Checker] = {
    "name": text,
    "system_prompt": text,
    "role": one_of(PARTICIPANT_ROLES),
    "provider_url": text,
    "model": text,
}


def participants(value: object) -> str | None:
    """A list of participant objects, each with a non-blank ``name``."""
    if not isinstance(value, list):
        return "must be a list of participants"
    for i, p in enumerate(value, start=1):
        if not isinstance(p, dict):
            return f"entry {i} must be an object"
        problem = body_problem(p, PARTICIPANT_FIELDS, required=("name",))
        if problem:
            return f"entry {i}: {problem}"
    return None


CASE_FIELDS: Mapping[str, Checker] = {
    "case_key": text,
    "title": text,
    "presentation": text,
    "gold_diagnosis": text,
    "difficulty": one_of(CASE_DIFFICULTIES),
    "source": text,
    "aliases": text_list,
    "findings": text_list,
    "differentials": text_list,
}
CASE_REQUIRED = ("case_key", "title", "presentation", "gold_diagnosis")

CONDITION_FIELDS: Mapping[str, Checker] = {
    "name": text,
    "description": text,
    "enable_da": flag,
    "enable_memory": flag,
    "enable_tools": flag,
    "num_rounds": positive_int,
    "participants": participants,
}
CONDITION_REQUIRED = ("name",)

BATCH_FIELDS: Mapping[str, Checker] = {
    "case_ids": id_list,
    "condition_ids": id_list,
    "provider_url": text,
    "model": text,
    "api_key": text,
}


#: Phrases in a ``sqlite3.IntegrityError`` that are the caller's conflict
#: with stored data, mapped to the message the caller sees.
CONFLICT_MESSAGES: Mapping[str, str] = {
    "UNIQUE constraint failed: cases.case_key":
        "A case with that case_key already exists",
    "UNIQUE constraint failed: conditions.name":
        "A condition with that name already exists",
    # Only a delete can hit a foreign key: start_batch checks ids first.
    "FOREIGN KEY constraint failed":
        "This case or condition is used by evaluation runs, so it cannot "
        "be deleted",
}


def conflict_message(error: str) -> str | None:
    """The user-facing message for a known conflict, else ``None``.

    ``None`` means the constraint failure is not one a well-formed request
    can cause, i.e. a server bug, which must stay a 500.
    """
    for phrase, message in CONFLICT_MESSAGES.items():
        if phrase in error:
            return message
    return None


def body_problem(data: Mapping[str, object], fields: Mapping[str, Checker],
                 required: Collection[str] = (),
                 partial: bool = False) -> str | None:
    """The first problem with ``data`` against ``fields``, or ``None``.

    Every field present in ``data`` must pass its checker (``null`` is
    never accepted). A ``required`` field must also be non-blank, and
    present unless ``partial`` (an update, which only sends what changes).
    Fields not in ``fields`` are ignored, as the routes ignore them.
    """
    for name in required:
        if name not in data:
            if partial:
                continue
            return f"Missing required field: {name}"
        value = data[name]
        if isinstance(value, str) and not value.strip():
            return f"Missing required field: {name}"
    for name, check in fields.items():
        if name in data:
            problem = check(data[name])
            if problem:
                return f"{name} {problem}"
    return None
