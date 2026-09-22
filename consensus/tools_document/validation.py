"""Pure validation helpers for document tool arguments.

Character ranges arrive straight from the model, where an inverted or
negative range is a plausible mistake.  Validating in one place keeps
``doc_get_text`` and ``doc_summary`` consistent — only the latter guarded,
and the inconsistency looked accidental (issue #78 defect 9).
"""

# Documented sentinel meaning "to the end of the document".
TO_END = -1


def resolve_range(from_char: int, to_char: int, length: int) -> tuple[int, int]:
    """Resolve a model-supplied character range against a document length.

    Args:
        from_char: Inclusive start offset.
        to_char: Exclusive end offset, or ``TO_END`` for the end of the
            document.
        length: The document's character count.

    Returns:
        The resolved ``(from_char, to_char)``, with ``to_char`` clamped to
        *length*.

    Raises:
        ValueError: If either offset is negative other than the ``TO_END``
            sentinel, if the start is past the end of the document, or if
            the start is not before the end.  Python's negative slicing
            would otherwise turn ``to_char=-5`` into a silent truncation
            and ``markdown[500:100]`` into an empty success.
    """
    if from_char < 0:
        raise ValueError(
            f"from_char must not be negative (got {from_char})")
    if to_char < 0 and to_char != TO_END:
        raise ValueError(
            f"to_char must be {TO_END} (end of document) or a non-negative "
            f"offset (got {to_char})")
    if from_char > length:
        raise ValueError(
            f"from_char {from_char} is past the end of the document "
            f"({length} characters)")

    resolved_to = length if to_char == TO_END else min(to_char, length)
    if from_char >= resolved_to:
        raise ValueError(
            f"from_char {from_char} must be before to_char {resolved_to}")
    return from_char, resolved_to


def chapter_range(
    sections: list[dict], index: int, length: int,
) -> tuple[int, int, list[str]]:
    """Return the full extent of the chapter at *index*, with its
    subsections.

    ``extract_sections`` ends every section at the next header of *any*
    level, because that is what chunk boundaries need.  A chapter is a
    different question: ``## Methods`` includes its ``### Participants``,
    so the range runs to the next header at the same or a higher level
    (issue #78 defect 10).  The stored ``sections_json`` is untouched, so
    no document needs re-ingesting.

    Args:
        sections: The document's sections, in document order.
        index: Which section to treat as the chapter head.
        length: The document's character count, used when the chapter runs
            to the end.

    Returns:
        ``(from_char, to_char, subsection_headers)``.
    """
    head = sections[index]
    from_char = head["from_char"]
    subsections: list[str] = []

    for following in sections[index + 1:]:
        if following["level"] <= head["level"]:
            return from_char, following["from_char"], subsections
        subsections.append(following["header"])

    return from_char, length, subsections
