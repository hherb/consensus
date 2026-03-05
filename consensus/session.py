"""Session management for multi-user web deployments."""

import asyncio
import logging
import os
import shutil
import time
from typing import Optional

from .app import ConsensusApp
from .config import get_data_dir

logger = logging.getLogger(__name__)

# Session defaults
DEFAULT_SESSION_TTL = 86400  # 24 hours
DEFAULT_MAX_SESSIONS = 100
CLEANUP_INTERVAL = 300  # 5 minutes


class SessionManager:
    """Manages per-user ConsensusApp instances with TTL-based expiry.

    Each session gets its own SQLite database and ConsensusApp instance.
    Sessions are identified by a cookie-based session ID.
    """

    def __init__(self, session_ttl: int = DEFAULT_SESSION_TTL,
                 max_sessions: int = DEFAULT_MAX_SESSIONS) -> None:
        self.session_ttl = session_ttl
        self.max_sessions = max_sessions
        self._sessions: dict[str, dict] = {}  # sid -> {app, last_access, db_path}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._data_dir = os.path.join(get_data_dir(), "sessions")
        os.makedirs(self._data_dir, exist_ok=True)

    def start_cleanup_loop(self) -> None:
        """Start the periodic cleanup task."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        """Stop cleanup and close all sessions."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        async with self._lock:
            for info in self._sessions.values():
                try:
                    await info["app"].moderator.close()
                except Exception:
                    pass
            self._sessions.clear()

    async def get_app(self, session_id: str) -> Optional[ConsensusApp]:
        """Get or create a ConsensusApp for the given session ID.

        Returns None if max sessions reached and this is a new session.
        """
        async with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id]["last_access"] = time.time()
                return self._sessions[session_id]["app"]

            if len(self._sessions) >= self.max_sessions:
                # Try to evict expired sessions first
                self._evict_expired()
                if len(self._sessions) >= self.max_sessions:
                    return None

            db_path = os.path.join(self._data_dir, f"{session_id}.db")
            app = ConsensusApp(db_path=db_path)
            self._sessions[session_id] = {
                "app": app,
                "last_access": time.time(),
                "db_path": db_path,
            }
            return app

    @property
    def active_count(self) -> int:
        """Number of active sessions."""
        return len(self._sessions)

    def _evict_expired(self) -> None:
        """Remove expired sessions (call under lock)."""
        now = time.time()
        expired = [
            sid for sid, info in self._sessions.items()
            if now - info["last_access"] > self.session_ttl
        ]
        for sid in expired:
            info = self._sessions.pop(sid)
            try:
                info["app"].db.conn.close()
            except Exception:
                pass
            # Clean up session database
            try:
                db_path = info["db_path"]
                for suffix in ("", "-wal", "-shm"):
                    p = db_path + suffix
                    if os.path.exists(p):
                        os.remove(p)
            except OSError:
                logger.debug("Failed to clean session DB %s", sid)
        if expired:
            logger.info("Evicted %d expired sessions", len(expired))

    async def _cleanup_loop(self) -> None:
        """Periodically evict expired sessions."""
        try:
            while True:
                await asyncio.sleep(CLEANUP_INTERVAL)
                async with self._lock:
                    self._evict_expired()
        except asyncio.CancelledError:
            pass
