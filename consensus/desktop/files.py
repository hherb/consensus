"""Bridge methods behind native file dialogs and attachments:
documents, images, export and ``save_file``."""

import os


class FilesBridgeMixin:
    """Documents, images, export and native file dialogs."""

    # -- Documents --
    def upload_document(self, discussion_id: int = 0) -> dict:
        """Open a file picker and upload a document."""
        import webview
        if not self._window:
            return {"error": "No window available"}
        if not self.app.documents_available:
            return {"error": "Document tools failed to load; see the application log for the cause"}

        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=(
                "Documents (*.pdf;*.html;*.htm;*.txt;*.md)",
                "PDF files (*.pdf)",
                "HTML files (*.html;*.htm)",
                "Text files (*.txt;*.md)",
                "All files (*.*)",
            ),
        )
        if not result:
            return {"cancelled": True}
        filepath = result if isinstance(result, str) else result[0]

        import mimetypes
        mime_type, _ = mimetypes.guess_type(filepath)
        if not mime_type:
            mime_type = "application/octet-stream"

        filename = os.path.basename(filepath)
        with open(filepath, "rb") as f:
            content = f.read()

        return self._run_async(self.app.add_document(
            filename=filename,
            content_bytes=content,
            mime_type=mime_type,
            discussion_id=discussion_id,
        ))

    def add_document_from_url(self, url: str, discussion_id: int = 0,
                               title: str = "") -> dict:
        """Add a document by fetching from a URL."""
        if not self.app.documents_available:
            return {"error": "Document tools failed to load; see the application log for the cause"}
        return self._run_async(self.app.add_document_from_url(
            url=url, discussion_id=discussion_id, title=title,
        ))

    def get_discussion_documents(self, discussion_id: int = 0) -> list:
        """Return documents attached to a discussion."""
        return self.app.get_discussion_documents(discussion_id)

    def remove_document(self, document_id: int, discussion_id: int = 0) -> dict:
        """Remove a document from a discussion."""
        return self.app.remove_document(document_id, discussion_id)

    def delete_document(self, document_id: int) -> dict:
        """Permanently delete a document."""
        return self.app.delete_document(document_id)

    # -- Images --

    def upload_image(self, discussion_id: int = 0) -> dict:
        """Open a file picker and upload an image."""
        import webview
        if not self._window:
            return {"error": "No window available"}

        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=(
                "Images (*.png;*.jpg;*.jpeg;*.gif;*.webp)",
                "PNG files (*.png)",
                "JPEG files (*.jpg;*.jpeg)",
                "GIF files (*.gif)",
                "WebP files (*.webp)",
                "All files (*.*)",
            ),
        )
        if not result:
            return {"cancelled": True}
        filepath = result if isinstance(result, str) else result[0]

        import mimetypes
        mime_type, _ = mimetypes.guess_type(filepath)
        if not mime_type:
            mime_type = "image/png"

        filename = os.path.basename(filepath)
        with open(filepath, "rb") as f:
            content = f.read()

        return self._run_async(self.app.add_image(
            filename=filename,
            content_bytes=content,
            mime_type=mime_type,
            discussion_id=discussion_id,
        ))

    def add_image_from_url(self, url: str, discussion_id: int = 0,
                            title: str = "") -> dict:
        """Add an image by fetching from a URL."""
        return self._run_async(self.app.add_image_from_url(
            url=url, discussion_id=discussion_id, title=title,
        ))

    def get_discussion_images(self, discussion_id: int = 0) -> list:
        """Return images attached to a discussion."""
        return self.app.get_discussion_images(discussion_id)

    def remove_image(self, image_id: int, discussion_id: int = 0) -> dict:
        """Remove an image from a discussion."""
        return self.app.remove_discussion_image(image_id, discussion_id)

    def delete_image(self, image_id: int) -> dict:
        """Permanently delete an image."""
        return self.app.delete_image(image_id)

    # -- Export --
    def get_export_data(self, discussion_id: int) -> dict:
        """Get discussion data for export without mutating current state."""
        return self.app.get_export_data(discussion_id)

    def save_file(self, content: str, filename: str, file_types: str = "") -> bool:
        """Show a native save dialog and write content to the chosen path."""
        import webview
        if not self._window:
            return False
        file_filter = file_types or "All files (*.*)"
        result = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=filename,
            file_types=(file_filter,),
        )
        if not result:
            return False
        path = result if isinstance(result, str) else result[0]
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
