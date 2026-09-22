"""RAG-driven ``doc_*`` handlers: ``doc_ask`` and ``doc_summary``.

Split out of ``handlers.py`` when that module crossed the ~500 line
guideline (golden rule 8). These two handlers are the retrieval-augmented
generation pipeline proper — embed a question, rank chunks, call the
interpretation LLM — as opposed to the metadata/navigation handlers
(``doc_add``, ``doc_list``, ``doc_get_*``) that remain in ``handlers.py``.

``_reindex_message`` lives here too, relocated from ``embedding.py``: it is
a user-facing string formatter, not computation, and its only consumers are
handlers (``doc_ask`` here, plus ``_doc_list_handler`` in ``handlers.py``,
which imports it from this module). ``embedding.py`` stays pure embedding
maths and the background embedding pass.
"""

import json
import logging

from ..tools import ToolContext, ToolResult
from .constants import (
    MIN_SIMILARITY_THRESHOLD, PASSAGE_PREVIEW_CHARS,
    RAG_TOP_K, SUMMARY_CHUNK_LIMIT,
)
from .embedding import (
    DocKey, IndexingFailure, RankingResult, _embedding_docs,
    _rank_by_similarity, _spawn_embedding_pass, doc_key, get_indexing_failure,
)
from .errors import DocumentInterpretationError
from .llm import _call_interpretation_llm
from .validation import resolve_range

logger = logging.getLogger(__name__)

# Documents whose indexing failure has already been announced in the
# transcript, keyed by ``(db_path, doc_id)``. The AI retries doc_ask up to
# MAX_TOOL_ITERATIONS times per turn, and one notice per failure streak is
# information while several would just be noise. The key is scoped to the
# database because document ids restart at 1 in every ``--multi-user``
# session's own SQLite file (issue #78 whole-branch review).
_notified_index_failures: set[DocKey] = set()


def _reindex_message(ranking: RankingResult) -> str:
    """Explain a dimension mismatch in terms a user can act on.

    Args:
        ranking: A :class:`RankingResult` whose ``skipped_dim_mismatch`` is
            non-zero — the caller is expected to check that first.

    Returns:
        A message naming the dimensions involved and the required remedy.
    """
    return (
        f"{ranking.skipped_dim_mismatch} chunk(s) were indexed with a "
        f"different embedding model (dimension "
        f"{', '.join(str(d) for d in ranking.row_dims)} vs "
        f"{ranking.query_dim} now). The documents must be re-indexed "
        "before they can be searched."
    )


def _start_embedding_pass(db, doc_id: int, embed_client) -> bool:
    """Schedule a background embedding pass unless one is already running.

    Shared by both indexing branches of :func:`_doc_ask_handler` so the
    in-flight marker is claimed the same way in each: without the guard,
    every retry within a turn would spawn another pass over the same
    chunks.

    Args:
        db: The database holding the document; also scopes the marker.
        doc_id: Id of the document to index.
        embed_client: The embedding client, or a falsy value when the
            caller has none — in which case nothing is scheduled.

    Returns:
        True if a pass was scheduled by this call.
    """
    if not embed_client:
        return False
    key = doc_key(db, doc_id)
    if key in _embedding_docs:
        return False
    _embedding_docs.add(key)
    _spawn_embedding_pass(doc_id, db, embed_client)
    return True


def _retry_indexing_if_due(
    db, doc_id: int, embed_client, failure: IndexingFailure,
) -> bool:
    """Re-kick a failed document's embedding pass once the wait has elapsed.

    A recorded failure used to be terminal: ``doc_ask`` returned the error
    and never scheduled another pass, so a thirty-second embedder outage
    during ingestion killed the document for the rest of the process
    lifetime, even after the service came back. Retrying on every call
    instead would hammer a service that is still down, so the retry is
    gated on ``INDEXING_RETRY_INTERVAL`` since ``failure.last_attempt``
    (issue #78 whole-branch review).

    Args:
        db: The database holding the document.
        doc_id: Id of the document whose indexing failed.
        embed_client: The embedding client to retry with, if any.
        failure: The recorded failure, whose ``last_attempt`` sets the gate.

    Returns:
        True if a fresh pass was scheduled by this call.
    """
    if not failure.should_retry():
        return False
    return _start_embedding_pass(db, doc_id, embed_client)


def _post_indexing_notice(app, db, doc_id: int, detail: str) -> None:
    """Announce an indexing failure in the discussion transcript.

    Golden rule 6 requires a caught error to reach the UI, and the
    tool-call row carrying this result is collapsed by default in the
    frontend — so a human who never expands it would otherwise never learn
    the document is unusable, and would just see a discussion that
    silently stops using it.

    Fully guarded: this function exists to *report* a failure, so it must
    never raise one of its own out of the tool call (the lesson of issue
    #74). A missing discussion, a missing moderator entity, or a
    ``post_notice`` call that itself raises are all swallowed and logged
    rather than propagated.

    Args:
        app: The orchestrator instance. Expected to carry ``.discussion``,
            but it may be absent (e.g. a standalone tool call outside an
            active discussion), in which case no notice is posted.
        db: The database holding the document, used both to resolve the
            moderator entity and to scope the already-notified marker.
        doc_id: Id of the document whose indexing failed.
        detail: The embedder's own error message, already known to the
            caller via ``IndexingFailure.last_error``.
    """
    key = doc_key(db, doc_id)
    if key in _notified_index_failures:
        return
    try:
        from ..app_discussion_flow.helpers import post_notice
        from ..models import Entity

        discussion = getattr(app, "discussion", None)
        if discussion is None:
            return
        if db is None:
            return
        moderator_row = db.get_entity(discussion.moderator_id)
        if not moderator_row:
            return
        post_notice(
            discussion, db, Entity.from_db_row(moderator_row),
            f"Document {doc_id} could not be indexed: {detail}. "
            "Participants cannot search or ask questions about it until "
            "the embedding service is working and the document is "
            "re-indexed.",
        )
        _notified_index_failures.add(key)
    except Exception:
        logger.exception(
            "Could not post the indexing-failure notice for document %d",
            doc_id,
        )


async def _doc_ask_handler(
    arguments: dict, context: ToolContext,
    db, embed_client, app,
) -> ToolResult:
    """RAG pipeline: embed question, retrieve top-k chunks, call LLM."""
    doc_id = arguments.get("document_id")
    question = arguments.get("question", "").strip()

    if doc_id is None:
        return ToolResult(content="document_id is required.", is_error=True)
    if not question:
        return ToolResult(content="question is required.", is_error=True)

    doc_id = int(doc_id)
    doc = db.get_document(doc_id)
    if not doc:
        return ToolResult(content=f"Document {doc_id} not found.", is_error=True)

    # Check whether embeddings are ready. A failed pass and a first pass
    # still in flight used to be reported identically, so a dead embedder
    # claimed to be "still indexing" forever (issue #78 defect 3).
    unembedded = db.count_unembedded_chunks(doc_id)
    if unembedded > 0:
        total_chunks = len(db.get_document_chunks(doc_id))
        embedded = total_chunks - unembedded
        failure = get_indexing_failure(db, doc_id)

        if failure is not None:
            detail = failure.last_error
            _post_indexing_notice(app, db, doc_id, detail)
            # Report the failure honestly *and* schedule another pass when
            # enough time has passed, so an embedder outage that has since
            # ended repairs itself instead of condemning the document for
            # the rest of the process lifetime.
            retried = _retry_indexing_if_due(db, doc_id, embed_client, failure)
            retry_note = (
                " A fresh indexing attempt has just been started."
                if retried else ""
            )
            return ToolResult(
                content=(
                    f"Indexing failed: {detail}. "
                    f"{embedded}/{total_chunks} chunks embedded after "
                    f"{failure.consecutive_failures} failed pass(es). "
                    "The embedding service must be working before this "
                    f"document can be queried.{retry_note}"
                ),
                is_error=True,
            )

        # No failure recorded: this is a first pass genuinely still in
        # flight, not a stuck one — the document is currently healthy.
        # Forget any earlier notice so a *later* failure is treated as a
        # new streak and announced again, rather than "one notice per
        # document per failure streak" silently degrading into "one
        # notice per document ever" (issue #78 task 9 follow-up).
        _notified_index_failures.discard(doc_key(db, doc_id))

        # Re-kick the background embedding pass if it is not already running,
        # so a previously failed/interrupted chunk is retried instead of
        # leaving the document permanently stuck as "still being indexed".
        _start_embedding_pass(db, doc_id, embed_client)
        return ToolResult(
            content=(
                f"Document is still being indexed "
                f"({embedded}/{total_chunks} chunks embedded). "
                "Please try again shortly."
            ),
        )

    # unembedded == 0: the document is fully embedded and healthy. Same
    # reasoning as above — a document that failed, recovered, and later
    # fails again must get a fresh notice for the new streak.
    _notified_index_failures.discard(doc_key(db, doc_id))

    # Embed the question
    try:
        query_vec = await embed_client.embed(question)
    except Exception as e:
        # Logged as well as shown (golden rule 6); the traceback matters
        # because this catch would report a client-side bug as an outage.
        logger.exception(
            "doc_ask could not embed the question for document %d", doc_id,
        )
        return ToolResult(
            content=f"Embedding service unavailable: {e}", is_error=True,
        )

    # Retrieve and rank chunks
    rows = db.get_chunks_with_embeddings(doc_id)
    if not rows:
        return ToolResult(content="No embedded chunks found for this document.")

    ranking = _rank_by_similarity(
        query_vec, rows, RAG_TOP_K, threshold=MIN_SIMILARITY_THRESHOLD,
    )
    if not ranking.ranked:
        # Two very different situations used to look identical, because
        # the default threshold of 0.0 let every row through (issue #78).
        if ranking.skipped_dim_mismatch:
            return ToolResult(content=_reindex_message(ranking), is_error=True)
        return ToolResult(
            content=(
                f"No passage in '{doc['title']}' is relevant to that "
                "question (nothing scored above the relevance threshold)."
            ),
        )
    scored = ranking.ranked

    # Build context for LLM
    passages = []
    for i, (score, row) in enumerate(scored, 1):
        passages.append({
            "index": i,
            "text": row["content"],
            "from_char": row["from_char"],
            "to_char": row["to_char"],
            "score": round(score, 3),
        })

    passages_text = "\n\n".join(
        f"[Passage {p['index']}] (chars {p['from_char']}-{p['to_char']}, "
        f"relevance: {p['score']}):\n{p['text']}"
        for p in passages
    )

    try:
        answer = await _call_interpretation_llm(
            app, context,
            system_prompt=(
                "You are a document analyst. Answer the question based ONLY on the "
                "provided passages from the document. Cite passage numbers in your answer. "
                "If the answer is not in the passages, say so clearly."
            ),
            user_prompt=(
                f"DOCUMENT: {doc['title']}\n\n"
                f"PASSAGES:\n{passages_text}\n\n"
                f"QUESTION: {question}"
            ),
        )
    except DocumentInterpretationError as e:
        # Caught explicitly, rather than left to ToolRegistry's generic
        # `except Exception`, so the failure names the model and provider
        # that failed instead of a bare "Tool error: ..." — and so an
        # expected provider failure logs a warning, not a full traceback.
        entity = db.get_entity(context.caller_entity_id) or {}
        model = entity.get("model") or "unknown model"
        provider = entity.get("provider_name") or "unknown provider"
        logger.warning(
            "doc_ask interpretation failed for document %d "
            "(model=%s, provider=%s): %s",
            doc_id, model, provider, e,
        )
        return ToolResult(
            content=(
                f"Could not answer from document {doc_id} ('{doc['title']}'): "
                f"the interpretation model '{model}' (provider '{provider}') "
                f"failed: {e}"
            ),
            is_error=True,
        )

    result = {
        "answer": answer,
        "relevant_passages": [
            {
                "text": p["text"][:PASSAGE_PREVIEW_CHARS],
                "from_char": p["from_char"], "to_char": p["to_char"],
            }
            for p in passages
        ],
    }
    if ranking.skipped_dim_mismatch:
        # A *partial* mismatch still answers, from whichever chunks carry
        # the current dimension — but the answer is drawn from part of the
        # document, and saying nothing would make that indistinguishable
        # from a complete one (issue #78 whole-branch review).
        result["incomplete_retrieval"] = _reindex_message(ranking)
    return ToolResult(
        content=json.dumps(result, indent=2),
        metadata=result,
    )


async def _doc_summary_handler(
    arguments: dict, context: ToolContext,
    db, embed_client, app,
) -> ToolResult:
    """Summarize a document or a range of it."""
    doc_id = arguments.get("document_id")
    from_char = int(arguments.get("from_char", 0))
    to_char = int(arguments.get("to_char", -1))

    if doc_id is None:
        return ToolResult(content="document_id is required.", is_error=True)

    markdown = db.get_document_markdown(int(doc_id))
    if markdown is None:
        return ToolResult(content=f"Document {doc_id} not found.", is_error=True)

    try:
        from_char, to_char = resolve_range(from_char, to_char, len(markdown))
    except ValueError as e:
        return ToolResult(content=f"Invalid range: {e}", is_error=True)
    text = markdown[from_char:to_char]

    # resolve_range guarantees from_char < to_char, but a range that is
    # entirely whitespace is still a valid selection — not covered by
    # resolve_range, so this check stays.
    if not text.strip():
        return ToolResult(content="Selected range is empty.")

    try:
        if len(text) <= SUMMARY_CHUNK_LIMIT:
            # Direct summarization
            summary = await _call_interpretation_llm(
                app, context,
                system_prompt=(
                    "You are a document analyst. Provide a clear, comprehensive summary "
                    "of the following text. Include key findings, methods, and conclusions."
                ),
                user_prompt=text,
            )
        else:
            # Map-reduce: summarize chunks, then summarize summaries
            chunk_summaries = []
            for i in range(0, len(text), SUMMARY_CHUNK_LIMIT):
                chunk = text[i:i + SUMMARY_CHUNK_LIMIT]
                chunk_summary = await _call_interpretation_llm(
                    app, context,
                    system_prompt=(
                        "Provide a concise summary of this text excerpt. "
                        "Focus on key points and findings."
                    ),
                    user_prompt=chunk,
                )
                chunk_summaries.append(chunk_summary)

            # Combine
            combined = "\n\n---\n\n".join(
                f"Section {i+1}:\n{s}" for i, s in enumerate(chunk_summaries)
            )
            summary = await _call_interpretation_llm(
                app, context,
                system_prompt=(
                    "You are a document analyst. Synthesize these section summaries "
                    "into a single coherent summary. Include all key findings, methods, "
                    "and conclusions."
                ),
                user_prompt=combined,
            )
    except DocumentInterpretationError as e:
        # Caught explicitly, rather than left to ToolRegistry's generic
        # `except Exception`, so the failure names the model and provider
        # that failed instead of a bare "Tool error: ..." — the same
        # treatment _doc_ask_handler already received. Covers all three
        # _call_interpretation_llm call sites above: the direct path and
        # both map-reduce calls.
        entity = db.get_entity(context.caller_entity_id) or {}
        model = entity.get("model") or "unknown model"
        provider = entity.get("provider_name") or "unknown provider"
        logger.warning(
            "doc_summary interpretation failed for document %d "
            "(model=%s, provider=%s): %s",
            doc_id, model, provider, e,
        )
        return ToolResult(
            content=(
                f"Could not summarize document {doc_id}: "
                f"the interpretation model '{model}' (provider '{provider}') "
                f"failed: {e}"
            ),
            is_error=True,
        )

    return ToolResult(
        content=json.dumps({"summary": summary}),
        metadata={"summary": summary, "from_char": from_char, "to_char": to_char},
    )
