"""ConsensusApp mixin: Image upload, URL ingestion, serving and deletion.

Moved from ``consensus/app.py`` (issue #61); the error paths of
``add_image_from_url`` and ``get_image_data`` have since gained logging.
The host class is ``ConsensusApp``, which owns all state.
"""

import logging
from typing import Optional

from ..http_fetch import FetchError, redact_url


logger = logging.getLogger(__name__)


class ImagesMixin:
    """Image upload, URL ingestion, serving and deletion.

    Expects the host class to provide: db, discussion.
    """

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    async def add_image(self, filename: str, content_bytes: bytes,
                        mime_type: str, discussion_id: int = 0,
                        source_url: str = "",
                        title: str = "") -> dict:
        """Add an image via file upload."""
        from ..tools_image import ingest_image

        disc_id = discussion_id or (self.discussion.id if self.discussion else 0)
        result = await ingest_image(
            db=self.db,
            content_bytes=content_bytes,
            filename=filename,
            mime_type=mime_type,
            discussion_id=disc_id,
            source_url=source_url or None,
            title=title or None,
            source_type="url" if source_url else "upload",
        )
        # Track images added before discussion has a DB ID
        if not disc_id and "image_id" in result and self.discussion:
            self.discussion.pending_image_ids.append(result["image_id"])
        return result

    async def add_image_from_url(self, url: str, discussion_id: int = 0,
                                  title: str = "") -> dict:
        """Add an image by fetching from a URL."""
        from ..tools_image import fetch_image_from_url
        try:
            content_bytes, filename, mime_type = await fetch_image_from_url(url)
        except FetchError as e:
            # Expected: rejected URL, oversize or non-image body, HTTP fault.
            logger.warning("Image fetch failed for %s: %s", redact_url(url), e)
            return {"error": f"Failed to fetch image: {e}"}
        except Exception as e:
            # A bug in the fetch path, not a bad URL: keep the traceback.
            logger.exception("Image fetch failed unexpectedly for %s",
                             redact_url(url))
            return {"error": f"Failed to fetch image: {e}"}

        return await self.add_image(
            filename=filename, content_bytes=content_bytes,
            mime_type=mime_type, discussion_id=discussion_id,
            source_url=url, title=title,
        )

    def get_discussion_images(self, discussion_id: int = 0) -> list[dict]:
        """Return images attached to a discussion."""
        disc_id = discussion_id or (self.discussion.id if self.discussion else 0)
        if disc_id:
            return self.db.get_discussion_images(disc_id)
        # During setup (no discussion ID yet), return pending images
        if self.discussion and self.discussion.pending_image_ids:
            return [
                img for img_id in self.discussion.pending_image_ids
                if (img := self.db.get_image(img_id))
            ]
        return []

    def remove_discussion_image(self, image_id: int,
                                 discussion_id: int = 0) -> dict:
        """Remove an image from a discussion (keeps in library)."""
        disc_id = discussion_id or (self.discussion.id if self.discussion else 0)
        if disc_id:
            self.db.remove_discussion_image(disc_id, image_id)
        elif self.discussion and image_id in self.discussion.pending_image_ids:
            self.discussion.pending_image_ids.remove(image_id)
        return {"success": True}

    def get_image_data(self, image_id: int) -> Optional[tuple[bytes, str, str]]:
        """Return (bytes, mime_type, filename) for serving an image."""
        image = self.db.get_image(image_id)
        if not image:
            return None
        from ..tools_image import load_image_file
        try:
            data = load_image_file(image["storage_path"])
            return data, image["mime_type"], image["original_filename"]
        except FileNotFoundError:
            # The row survives but its file is gone: a 404 to the browser,
            # and a storage inconsistency worth a log line.
            logger.warning("File for image %d missing from storage: %s",
                           image_id, image["storage_path"])
            return None

    def delete_image(self, image_id: int) -> dict:
        """Permanently delete an image and its file."""
        image = self.db.get_image(image_id)
        if image:
            from ..tools_image import delete_image_file
            delete_image_file(image["storage_path"])
        if not self.db.delete_image(image_id):
            return {"success": False, "error": "Image not found"}
        return {"success": True}
