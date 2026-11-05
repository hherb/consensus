"""ConsensusApp mixin: Document upload, URL ingestion and discussion attachment.

Moved verbatim from ``consensus/app.py`` (issue #61). The host class
is ``ConsensusApp``, which owns all state.
"""

import logging

from ..models import EntityType
from ..tools import ToolContext


logger = logging.getLogger(__name__)


class DocumentsMixin:
    """Document upload, URL ingestion and discussion attachment.

    Expects the host class to provide: db, discussion, documents_available.
    """

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    def _interpretation_entity_id(self, discussion_id: int) -> int:
        """Pick an AI entity whose provider config can summarise a document.

        ``ingest_document`` generates the summary through
        ``_call_interpretation_llm``, which resolves an entity row and uses
        its provider, model and key. A human upload has no "caller", so this
        path used to pass ``caller_entity_id=0``. SQLite rowids start at 1,
        so ``get_entity(0)`` never resolved and *every* uploaded document's
        summary failed — silently, because the failure is caught and the
        upload still succeeds (issue #78 whole-branch review).

        Args:
            discussion_id: The discussion the document is being added to.

        Returns:
            The moderator's entity id when the moderator is an AI, else the
            first AI participant's id, else ``0`` when the discussion holds
            no AI entity to borrow a provider from.
        """
        disc = self.discussion
        if not disc or (discussion_id and disc.id != discussion_id):
            return 0

        mod = disc.moderator
        if mod and mod.entity_type is EntityType.AI:
            return mod.id
        for entity in disc.entities:
            if entity.entity_type is EntityType.AI:
                return entity.id
        return 0

    async def add_document(self, filename: str, content_bytes: bytes,
                           mime_type: str, discussion_id: int = 0,
                           source_url: str = "",
                           title: str = "") -> dict:
        """Add a document via file upload or URL fetch.

        Args:
            filename: Name of the uploaded file, used to pick a parser.
            content_bytes: The raw document bytes.
            mime_type: The declared content type.
            discussion_id: Discussion to attach the document to; 0 means
                the current one.
            source_url: Where the bytes came from, for a URL add.
            title: Overrides the auto-detected title when given.

        Returns:
            The document metadata dict from ``ingest_document``, or
            ``{"error": ...}`` when the bytes cannot be turned into a
            document — the same shape ``add_document_from_url`` already
            returns and the frontend already renders. Parsing raises a
            typed ``DocumentError`` as of issue #78, and without this the
            human upload path turned a scanned PDF or a .docx into an
            HTTP 500 and "Upload failed (500)" in the UI, losing the hint
            the error carries (golden rule 6).
        """
        if not self.documents_available:
            return {"error": "Document tools unavailable on this server (they failed to load; see the server log)"}

        from ..tools_document import ingest_document
        from ..tools_document.errors import DocumentError
        from ..tools_memory import EmbeddingClient

        embed_client = EmbeddingClient(self.db)

        disc_id = discussion_id or (self.discussion.id if self.discussion else 0)

        # Summary generation borrows an AI entity's provider config. Passing
        # no context is honest when there is none to borrow: ingest_document
        # then records summary_status='pending' rather than 'failed'.
        entity_id = self._interpretation_entity_id(disc_id)
        context = ToolContext(
            caller_entity_id=entity_id, discussion_id=disc_id,
        ) if entity_id else None

        try:
            result = await ingest_document(
                app=self, db=self.db, embed_client=embed_client,
                content_bytes=content_bytes,
                filename=filename,
                mime_type=mime_type,
                discussion_id=disc_id,
                source_url=source_url or None,
                title=title or None,
                source_type="url" if source_url else "upload",
                context=context,
            )
        except DocumentError as e:
            # str() on a DocumentError includes its hint, which is the
            # actionable half of the message ("OCR the file before adding
            # it", "only PDF, HTML, plain text and markdown can be
            # ingested").
            logger.warning("Document ingestion failed for %s: %s", filename, e)
            return {"error": str(e)}
        return result

    async def add_document_from_url(self, url: str, discussion_id: int = 0,
                                     title: str = "") -> dict:
        """Add a document by fetching from a URL.

        Args:
            url: The URL to fetch the document bytes from.
            discussion_id: Discussion to attach the document to; 0 means
                the current one.
            title: Overrides the auto-detected title when given.

        Returns:
            The document metadata dict from ``add_document``, or
            ``{"error": ...}`` when the URL cannot be fetched — the shape
            the frontend renders.
        """
        if not self.documents_available:
            return {"error": "Document tools unavailable on this server (they failed to load; see the server log)"}

        from ..tools_document import fetch_url_content
        from ..tools_document.errors import DocumentError
        try:
            content_bytes, filename, mime_type = await fetch_url_content(url)
        except DocumentError as e:
            # str() carries the hint; an expected fault needs no traceback.
            logger.warning("URL fetch failed for %s: %s", url, e)
            return {"error": f"Failed to fetch URL: {e}"}
        except Exception as e:
            # A bug in the fetch path, not a bad URL — golden rule 6 wants
            # this logged with a traceback, not just shown.
            logger.exception("URL fetch failed unexpectedly for %s", url)
            return {"error": f"Failed to fetch URL: {e}"}

        return await self.add_document(
            filename=filename, content_bytes=content_bytes,
            mime_type=mime_type, discussion_id=discussion_id,
            source_url=url, title=title,
        )

    def get_discussion_documents(self, discussion_id: int = 0) -> list[dict]:
        """Return documents attached to a discussion."""
        disc_id = discussion_id or (self.discussion.id if self.discussion else 0)
        return self.db.get_discussion_documents(disc_id)

    def remove_document(self, document_id: int, discussion_id: int = 0) -> dict:
        """Remove a document from a discussion."""
        disc_id = discussion_id or (self.discussion.id if self.discussion else 0)
        self.db.remove_discussion_document(disc_id, document_id)
        return {"success": True}

    def delete_document(self, document_id: int) -> dict:
        """Permanently delete a document and all its data."""
        deleted = self.db.delete_document(document_id)
        return {"success": deleted}
