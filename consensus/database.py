"""SQLite persistence layer for entities, providers, discussions, and prompts."""

import os
import sqlite3
import threading
import time
from typing import Optional

from .migrator import run_migrations
from .models import (
    DEFAULT_BASE_URL, DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS, DEFAULT_AVATAR_COLOR,
    DEFAULT_EMBEDDING_BACKEND, DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_ENDPOINT,
)

MAX_DAYS_KEEP_DELETED = 7

_VALID_TABLES = frozenset({
    "providers", "entities", "prompts", "discussions",
})


class Database:
    """Thread-safe SQLite database for the consensus application.

    All write operations are serialized via a threading lock to prevent
    concurrent write errors when accessed from multiple threads (e.g.
    pywebview js_api calls).
    """

    def __init__(self, db_path: str) -> None:
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        # Load sqlite_vec extension if available (required for memory/embedding features)
        try:
            import sqlite_vec
            sqlite_vec.load(self.conn)
        except Exception:
            pass
        run_migrations(self.conn, self._lock, self.db_path)
        self._seed_default_prompts()
        self._seed_default_providers()
        self._migrate_providers()
        self._seed_default_memory_config()
        self._seed_devils_advocate_prompts()

    def _execute_write(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a single write statement under the lock and commit."""
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def _update_row(self, table: str, row_id: int,
                    allowed: set[str], extra_sets: Optional[dict] = None,
                    **kwargs: object) -> None:
        """Generic row update: filters kwargs to allowed fields, appends
        extra_sets (e.g. updated_at), and executes a single UPDATE."""
        if table not in _VALID_TABLES:
            raise ValueError(f"Invalid table: {table}")
        sets: list[str] = []
        vals: list[object] = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if extra_sets:
            for k, v in extra_sets.items():
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            vals.append(row_id)
            self._execute_write(
                f"UPDATE {table} SET {','.join(sets)} WHERE id=?", tuple(vals)
            )

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def _seed_default_prompts(self) -> None:
        """Insert default prompts only if none exist yet."""
        count = self.conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
        if count > 0:
            return

        now = time.time()
        defaults = [
            # AI moderator prompts
            {
                "name": "AI Moderator – System",
                "role": "moderator", "target": "ai", "task": "system",
                "content": (
                    "You are {entity_name}, the moderator of a structured discussion.\n"
                    "Topic: {topic}\n"
                    "Participants: {participants}\n\n"
                    "Your role is to:\n"
                    "1. Facilitate productive dialogue between participants\n"
                    "2. Ensure all voices are heard fairly\n"
                    "3. Identify areas of agreement and disagreement\n"
                    "4. Synthesize emerging consensus\n"
                    "5. Maintain a neutral, balanced perspective\n\n"
                    "You do NOT take sides. You acknowledge all perspectives "
                    "fairly and guide the discussion constructively.\n\n"
                    "If you have access to memory tools, use them actively:\n"
                    "- Use discussion_search to recall relevant points from past discussions on similar topics\n"
                    "- Use kg_query to check for established concept relationships\n"
                    "- Use memory_store to save important moderator observations for future reference\n"
                    "- Use kg_assert to record key relationships that emerge during the discussion"
                ),
            },
            {
                "name": "AI Moderator – Summarize",
                "role": "moderator", "target": "ai", "task": "summarize",
                "content": (
                    "Turn {turn_number} has just completed. {speaker_name} spoke.\n"
                    "The next speaker is {next_speaker_name}.\n\n"
                    "Provide a brief synthesis (2-3 sentences) of the key point(s) "
                    "made and how they relate to the overall discussion so far. "
                    "Note any agreements, disagreements, or new perspectives introduced.\n\n"
                    "When handing off, address {next_speaker_name} by name."
                ),
            },
            {
                "name": "AI Moderator – Mediate",
                "role": "moderator", "target": "ai", "task": "mediate",
                "content": (
                    "A disagreement has arisen in the discussion.\n"
                    "Context: {context}\n\n"
                    "Please:\n"
                    "1. Acknowledge both perspectives fairly\n"
                    "2. Identify any common ground\n"
                    "3. Suggest a constructive path forward\n\n"
                    "Be diplomatic and balanced."
                ),
            },
            {
                "name": "AI Moderator – Conclude",
                "role": "moderator", "target": "ai", "task": "conclude",
                "content": (
                    "The discussion on '{topic}' is concluding.\n\n"
                    "Before writing your synthesis, use discussion_search and kg_query "
                    "to review relevant prior discussions and established relationships.\n\n"
                    "Provide a final synthesis that:\n"
                    "1. Summarizes the main positions expressed\n"
                    "2. Identifies areas of consensus\n"
                    "3. Notes remaining points of disagreement\n"
                    "4. Offers a balanced conclusion or recommendation\n\n"
                    "After concluding, use kg_assert to record the key relationships "
                    "and conclusions that emerged, and memory_store to save a summary "
                    "of outcomes for future reference.\n\n"
                    "Be thorough but concise (3-5 paragraphs)."
                ),
            },
            {
                "name": "AI Moderator – Open",
                "role": "moderator", "target": "ai", "task": "open",
                "content": (
                    "Welcome to this discussion on: **{topic}**\n\n"
                    "Participants: {participants}\n\n"
                    "I will moderate this discussion, summarize key points "
                    "after each turn, and synthesize conclusions. Let's begin."
                ),
            },
            # AI participant prompts
            {
                "name": "AI Participant – System",
                "role": "participant", "target": "ai", "task": "system",
                "content": (
                    "You are {entity_name}, a participant in a moderated discussion.\n"
                    "Topic: {topic}\n"
                    "Other participants: {participants}\n\n"
                    "Contribute thoughtfully and constructively. Be concise but "
                    "substantive. Address points raised by other participants when "
                    "relevant. Present well-reasoned arguments and be open to "
                    "other perspectives.\n\n"
                    "If you have access to tools such as web search or page fetching, "
                    "use them actively whenever the topic involves current events, "
                    "recent data, specific facts, or claims worth verifying. "
                    "Do not just mention that a search could be done — perform it.\n\n"
                    "If you have access to memory tools, use them proactively:\n"
                    "- Before responding, use memory_recall to check whether you have "
                    "relevant memories from past discussions on this topic\n"
                    "- Use discussion_search to find prior arguments or evidence from "
                    "earlier discussions that are relevant to the current point\n"
                    "- Use memory_store to save your key positions, insights, or "
                    "observations so you can recall them in future discussions\n"
                    "- Use kg_assert to record important conceptual relationships you "
                    "identify (e.g. 'free will' contradicts 'hard determinism')\n"
                    "- Use kg_query to check what is already known about concepts "
                    "being discussed\n\n"
                    "If you have nothing meaningful to add at this stage — for example, "
                    "if your views have already been well represented or you agree with "
                    "what has been said — it is perfectly acceptable to pass. "
                    "To pass, respond with exactly: [PASS]"
                ),
            },
            {
                "name": "AI Participant – Turn",
                "role": "participant", "target": "ai", "task": "turn",
                "content": (
                    "It is your turn to speak as {entity_name}.\n"
                    "Before responding, consider using memory_recall and discussion_search "
                    "to check for relevant context from past discussions.\n"
                    "Provide your contribution to the discussion.\n"
                    "Be concise (2-4 paragraphs max). "
                    "Respond only with your contribution, no meta-commentary.\n"
                    "After contributing, use memory_store to save any key insights or "
                    "positions you want to remember for future discussions.\n\n"
                    "If you have nothing new or meaningful to contribute this round, "
                    "you may pass by responding with exactly: [PASS]"
                ),
            },
            # Human guidance prompts
            {
                "name": "Human Moderator – Guidance",
                "role": "moderator", "target": "human", "task": "guidance",
                "content": (
                    "As moderator of this discussion on \"{topic}\", please:\n"
                    "- Summarize key points after each participant speaks\n"
                    "- Identify areas of agreement and disagreement\n"
                    "- Mediate if conflicts arise\n"
                    "- Maintain neutrality and fairness\n"
                    "- Synthesize conclusions when the discussion wraps up"
                ),
            },
            {
                "name": "Human Participant – Guidance",
                "role": "participant", "target": "human", "task": "guidance",
                "content": (
                    "You are participating in a moderated discussion on \"{topic}\".\n\n"
                    "Please:\n"
                    "- Present your views clearly and concisely\n"
                    "- Engage with other participants' points\n"
                    "- Be constructive and respectful\n"
                    "- Support your arguments with reasoning"
                ),
            },
            # Devil's Advocate prompts
            {
                "name": "Devil's Advocate – System",
                "role": "participant", "target": "ai",
                "task": "system_devils_advocate",
                "content": (
                    "You are {entity_name}, serving as the Devil's Advocate in a "
                    "moderated discussion.\n"
                    "Topic: {topic}\n"
                    "Other participants: {participants}\n\n"
                    "Your role is to critically analyze all claims, suggestions, and "
                    "conclusions made by other participants. You are NOT hostile or "
                    "contrarian for its own sake. Your purpose is constructive: to "
                    "strengthen the discussion by identifying:\n"
                    "1. Factual errors or unsupported claims\n"
                    "2. Logical fallacies and flawed reasoning\n"
                    "3. Weak arguments that need stronger evidence\n"
                    "4. Unstated assumptions that may not hold\n"
                    "5. Missing perspectives or counterarguments\n"
                    "6. Overconfident conclusions drawn from insufficient evidence\n\n"
                    "You MUST actively use web search tools to fact-check specific "
                    "claims made by other participants. Do not merely assert something "
                    "is wrong — search for evidence and cite what you find.\n\n"
                    "Use memory tools to track your work:\n"
                    "- Use memory_store to record flaws, errors, and weak arguments "
                    "you have identified so you can reference them later\n"
                    "- Use memory_recall to check your previous critiques before "
                    "each new contribution\n"
                    "- Use discussion_search to find earlier claims that may "
                    "contradict current arguments\n"
                    "- Use kg_assert to record logical relationships and "
                    "contradictions you discover\n"
                    "- Use kg_query to check established concept relationships\n\n"
                    "Be respectful but unflinching. Your duty is to the truth and "
                    "the quality of reasoning, not to consensus or social harmony. "
                    "If a claim withstands your scrutiny, acknowledge its strength "
                    "explicitly.\n\n"
                    "You speak last each round, so you will have seen all "
                    "contributions before offering your critique.\n\n"
                    "If you have nothing to challenge this round, you may pass by "
                    "responding with exactly: [PASS]"
                ),
            },
            {
                "name": "Devil's Advocate – Turn",
                "role": "participant", "target": "ai",
                "task": "turn_devils_advocate",
                "content": (
                    "It is your turn to speak as {entity_name} (Devil's Advocate).\n\n"
                    "Review the recent contributions carefully and identify the weakest "
                    "points. Before responding:\n"
                    "- Use web_search to fact-check any specific claims made by others\n"
                    "- Use memory_recall to review your previous critiques\n"
                    "- Use discussion_search to find contradictions with earlier points\n\n"
                    "Structure your response:\n"
                    "1. Identify the claim or argument you are challenging\n"
                    "2. Explain why it is problematic (logical flaw, missing evidence, "
                    "etc.)\n"
                    "3. Provide evidence from your research where available\n"
                    "4. Suggest what would make the argument stronger\n\n"
                    "Be concise (2-4 paragraphs). After contributing, use memory_store "
                    "to record your key critiques for future rounds.\n\n"
                    "If all recent arguments are sound and well-supported, acknowledge "
                    "this and pass by responding with exactly: [PASS]"
                ),
            },
        ]

        with self._lock:
            for d in defaults:
                self.conn.execute(
                    "INSERT INTO prompts (name, role, target, task, content, "
                    "is_default, created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
                    (d["name"], d["role"], d["target"], d["task"],
                     d["content"], now, now),
                )
            self.conn.commit()

    def _seed_default_providers(self) -> None:
        """Insert default providers only if none exist yet."""
        count = self.conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
        if count > 0:
            return

        now = time.time()
        defaults = [
            {
                "name": "Ollama (Local)",
                "base_url": DEFAULT_BASE_URL,
                "api_key_env": "",
            },
            {
                "name": "Anthropic",
                "base_url": "https://api.anthropic.com/v1",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            {
                "name": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY",
            },
            {
                "name": "Mistral",
                "base_url": "https://api.mistral.ai/v1",
                "api_key_env": "MISTRAL_API_KEY",
            },
            {
                "name": "OpenAI",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
            },
        ]

        with self._lock:
            for d in defaults:
                self.conn.execute(
                    "INSERT INTO providers (name, base_url, api_key_env, "
                    "created_at) VALUES (?,?,?,?)",
                    (d["name"], d["base_url"], d["api_key_env"], now),
                )
            self.conn.commit()

    def _migrate_providers(self) -> None:
        """Apply provider data fixes for existing databases."""
        from .config import save_api_key

        with self._lock:
            # Fix DeepSeek base_url (was /v1, which breaks /models endpoint)
            self.conn.execute(
                "UPDATE providers SET base_url = ? WHERE base_url = ?",
                ("https://api.deepseek.com", "https://api.deepseek.com/v1"),
            )
            # Add Mistral if not already present
            has_mistral = self.conn.execute(
                "SELECT COUNT(*) FROM providers WHERE base_url LIKE '%api.mistral.ai%'"
            ).fetchone()[0]
            if not has_mistral:
                self.conn.execute(
                    "INSERT INTO providers (name, base_url, api_key_env, "
                    "created_at) VALUES (?,?,?,?)",
                    ("Mistral", "https://api.mistral.ai/v1",
                     "MISTRAL_API_KEY", time.time()),
                )

            # Migrate literal API keys out of api_key_env into ~/.consensus/.env
            rows = self.conn.execute(
                "SELECT id, name, api_key_env FROM providers WHERE api_key_env != ''"
            ).fetchall()
            for row in rows:
                value = row[2]  # api_key_env
                # Heuristic: env var names are UPPER_SNAKE_CASE and short.
                # Literal keys contain lowercase, dashes, dots, or are long.
                is_literal = (
                    any(c in value for c in "-.") or
                    value != value.upper() or
                    len(value) > 40
                )
                if is_literal:
                    # Derive env var name from provider name
                    env_var = (row[1].upper()
                               .replace(" ", "_")
                               .replace("(", "")
                               .replace(")", "") + "_API_KEY")
                    if env_var.endswith("_API_KEY_API_KEY"):
                        env_var = env_var[:-8]
                    save_api_key(env_var, value)
                    self.conn.execute(
                        "UPDATE providers SET api_key_env = ? WHERE id = ?",
                        (env_var, row[0]),
                    )

            self.conn.commit()

    def _seed_default_memory_config(self) -> None:
        """Seed default memory_config values if the table exists."""
        # Check if memory_config table exists (created by baseline migration)
        has_table = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_config'"
        ).fetchone()
        if not has_table:
            return
        defaults = [
            ("embedding_backend", DEFAULT_EMBEDDING_BACKEND),
            ("embedding_model", DEFAULT_EMBEDDING_MODEL),
            ("embedding_endpoint", DEFAULT_EMBEDDING_ENDPOINT),
        ]
        with self._lock:
            for key, value in defaults:
                self.conn.execute(
                    "INSERT OR IGNORE INTO memory_config (key, value) VALUES (?,?)",
                    (key, value),
                )
            self.conn.commit()

    def _seed_devils_advocate_prompts(self) -> None:
        """Add devil's advocate prompt templates if not already present."""
        existing = self.conn.execute(
            "SELECT COUNT(*) FROM prompts WHERE task='system_devils_advocate'"
        ).fetchone()[0]
        if existing > 0:
            return
        now = time.time()
        templates = [
            {
                "name": "Devil's Advocate – System",
                "role": "participant", "target": "ai",
                "task": "system_devils_advocate",
                "content": (
                    "You are {entity_name}, serving as the Devil's Advocate in a "
                    "moderated discussion.\n"
                    "Topic: {topic}\n"
                    "Other participants: {participants}\n\n"
                    "Your role is to critically analyze all claims, suggestions, and "
                    "conclusions made by other participants. You are NOT hostile or "
                    "contrarian for its own sake. Your purpose is constructive: to "
                    "strengthen the discussion by identifying:\n"
                    "1. Factual errors or unsupported claims\n"
                    "2. Logical fallacies and flawed reasoning\n"
                    "3. Weak arguments that need stronger evidence\n"
                    "4. Unstated assumptions that may not hold\n"
                    "5. Missing perspectives or counterarguments\n"
                    "6. Overconfident conclusions drawn from insufficient evidence\n\n"
                    "You MUST actively use web search tools to fact-check specific "
                    "claims made by other participants. Do not merely assert something "
                    "is wrong — search for evidence and cite what you find.\n\n"
                    "Use memory tools to track your work:\n"
                    "- Use memory_store to record flaws, errors, and weak arguments "
                    "you have identified so you can reference them later\n"
                    "- Use memory_recall to check your previous critiques before "
                    "each new contribution\n"
                    "- Use discussion_search to find earlier claims that may "
                    "contradict current arguments\n"
                    "- Use kg_assert to record logical relationships and "
                    "contradictions you discover\n"
                    "- Use kg_query to check established concept relationships\n\n"
                    "Be respectful but unflinching. Your duty is to the truth and "
                    "the quality of reasoning, not to consensus or social harmony. "
                    "If a claim withstands your scrutiny, acknowledge its strength "
                    "explicitly.\n\n"
                    "You speak last each round, so you will have seen all "
                    "contributions before offering your critique.\n\n"
                    "If you have nothing to challenge this round, you may pass by "
                    "responding with exactly: [PASS]"
                ),
            },
            {
                "name": "Devil's Advocate – Turn",
                "role": "participant", "target": "ai",
                "task": "turn_devils_advocate",
                "content": (
                    "It is your turn to speak as {entity_name} (Devil's Advocate).\n\n"
                    "Review the recent contributions carefully and identify the weakest "
                    "points. Before responding:\n"
                    "- Use web_search to fact-check any specific claims made by others\n"
                    "- Use memory_recall to review your previous critiques\n"
                    "- Use discussion_search to find contradictions with earlier points\n\n"
                    "Structure your response:\n"
                    "1. Identify the claim or argument you are challenging\n"
                    "2. Explain why it is problematic (logical flaw, missing evidence, "
                    "etc.)\n"
                    "3. Provide evidence from your research where available\n"
                    "4. Suggest what would make the argument stronger\n\n"
                    "Be concise (2-4 paragraphs). After contributing, use memory_store "
                    "to record your key critiques for future rounds.\n\n"
                    "If all recent arguments are sound and well-supported, acknowledge "
                    "this and pass by responding with exactly: [PASS]"
                ),
            },
        ]
        with self._lock:
            for d in templates:
                self.conn.execute(
                    "INSERT INTO prompts (name, role, target, task, content, "
                    "is_default, created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
                    (d["name"], d["role"], d["target"], d["task"],
                     d["content"], now, now),
                )
            self.conn.commit()

    def get_memory_config(self) -> dict:
        """Return all memory_config rows as a dict."""
        rows = self.conn.execute("SELECT key, value FROM memory_config").fetchall()
        return {row[0]: row[1] for row in rows}

    def set_memory_config(self, key: str, value: str) -> None:
        """Upsert a memory_config value."""
        self._execute_write(
            "INSERT OR REPLACE INTO memory_config (key, value) VALUES (?,?)",
            (key, value),
        )

    def add_entity_memory(self, memory_id: str, entity_id: int,
                          content: str, discussion_id: Optional[int] = None) -> None:
        """Insert a new entity memory record."""
        self._execute_write(
            "INSERT INTO entity_memories (id, entity_id, content, discussion_id, created_at) "
            "VALUES (?,?,?,?,?)",
            (memory_id, entity_id, content, discussion_id, time.time()),
        )

    def set_entity_memory_embedding(self, memory_id: str, embedding: bytes) -> None:
        """Store or replace an embedding for an entity memory."""
        self._execute_write(
            "INSERT OR REPLACE INTO entity_memory_embeddings (memory_id, embedding) VALUES (?,?)",
            (memory_id, embedding),
        )

    def delete_entity_memory(self, memory_id: str, entity_id: int) -> bool:
        """Delete a memory. Returns True if a row was deleted."""
        cur = self._execute_write(
            "DELETE FROM entity_memories WHERE id=? AND entity_id=?",
            (memory_id, entity_id),
        )
        return cur.rowcount > 0

    def get_entity_memories_with_embeddings(
        self, entity_id: int
    ) -> list[dict]:
        """Return all (id, content, embedding) rows for an entity that have embeddings."""
        rows = self.conn.execute(
            "SELECT m.id, m.content, e.embedding "
            "FROM entity_memories m "
            "JOIN entity_memory_embeddings e ON e.memory_id = m.id "
            "WHERE m.entity_id = ?",
            (entity_id,),
        ).fetchall()
        return [{"id": r[0], "content": r[1], "embedding": r[2]} for r in rows]

    def set_message_embedding(self, message_id: str, embedding: bytes) -> None:
        """Store or replace an embedding for a message."""
        self._execute_write(
            "INSERT OR REPLACE INTO message_embeddings (message_id, embedding, indexed_at) "
            "VALUES (?,?,?)",
            (message_id, embedding, time.time()),
        )

    def get_unindexed_message_ids(self, discussion_id: int) -> list[str]:
        """Return message IDs from a discussion that have no embedding yet."""
        rows = self.conn.execute(
            "SELECT m.id FROM messages m "
            "LEFT JOIN message_embeddings e ON e.message_id = m.id "
            "WHERE m.discussion_id = ? AND e.message_id IS NULL",
            (discussion_id,),
        ).fetchall()
        return [str(r[0]) for r in rows]

    def get_message_content(self, message_id: str) -> Optional[str]:
        """Return content of a single message."""
        row = self.conn.execute(
            "SELECT content FROM messages WHERE id=?", (message_id,)
        ).fetchone()
        return row[0] if row else None

    def get_messages_with_embeddings(
        self, topic_filter: Optional[str] = None
    ) -> list[dict]:
        """Return (message_id, content, embedding, discussion_topic) rows."""
        if topic_filter:
            rows = self.conn.execute(
                "SELECT m.id, m.content, e.embedding, d.topic "
                "FROM messages m "
                "JOIN message_embeddings e ON e.message_id = m.id "
                "JOIN discussions d ON d.id = m.discussion_id "
                "WHERE d.topic LIKE ?",
                (f"%{topic_filter}%",),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT m.id, m.content, e.embedding, d.topic "
                "FROM messages m "
                "JOIN message_embeddings e ON e.message_id = m.id "
                "JOIN discussions d ON d.id = m.discussion_id",
            ).fetchall()
        return [{"id": r[0], "content": r[1], "embedding": r[2], "topic": r[3]}
                for r in rows]

    def upsert_kg_node(self, node_id: str, label: str, node_type: str,
                       description: Optional[str] = None) -> None:
        """Insert or update a knowledge graph node."""
        self._execute_write(
            "INSERT INTO kg_nodes (id, label, node_type, description, created_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(label) DO UPDATE SET description=excluded.description",
            (node_id, label, node_type, description, time.time()),
        )

    def get_kg_node_by_label(self, label: str) -> Optional[dict]:
        """Return a kg_node row by label."""
        row = self.conn.execute(
            "SELECT id, label, node_type, description FROM kg_nodes WHERE label=?",
            (label,),
        ).fetchone()
        return {"id": row[0], "label": row[1], "node_type": row[2], "description": row[3]} \
            if row else None

    def set_kg_node_embedding(self, node_id: str, embedding: bytes) -> None:
        """Store or replace an embedding for a kg node."""
        self._execute_write(
            "INSERT OR REPLACE INTO kg_node_embeddings (node_id, embedding) VALUES (?,?)",
            (node_id, embedding),
        )

    def add_kg_edge(self, edge_id: str, source_id: str, target_id: str,
                    relation: str, discussion_id: Optional[int] = None,
                    weight: float = 1.0) -> None:
        """Insert a knowledge graph edge."""
        self._execute_write(
            "INSERT OR IGNORE INTO kg_edges "
            "(id, source_id, target_id, relation, weight, discussion_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (edge_id, source_id, target_id, relation, weight, discussion_id, time.time()),
        )

    def get_kg_nodes_with_embeddings(self) -> list[dict]:
        """Return all kg nodes that have embeddings."""
        rows = self.conn.execute(
            "SELECT n.id, n.label, n.description, e.embedding "
            "FROM kg_nodes n "
            "JOIN kg_node_embeddings e ON e.node_id = n.id",
        ).fetchall()
        return [{"id": r[0], "label": r[1], "description": r[2], "embedding": r[3]}
                for r in rows]

    def get_kg_neighbors(self, node_id: str) -> list[dict]:
        """Return neighboring nodes and their edge relations."""
        rows = self.conn.execute(
            "SELECT n.label, e.relation, e.weight, 'out' as direction "
            "FROM kg_edges e JOIN kg_nodes n ON n.id = e.target_id "
            "WHERE e.source_id = ? "
            "UNION "
            "SELECT n.label, e.relation, e.weight, 'in' as direction "
            "FROM kg_edges e JOIN kg_nodes n ON n.id = e.source_id "
            "WHERE e.target_id = ?",
            (node_id, node_id),
        ).fetchall()
        return [{"label": r[0], "relation": r[1], "weight": r[2], "direction": r[3]}
                for r in rows]

    # ------------------------------------------------------------------
    # Tool Providers
    # ------------------------------------------------------------------

    def add_tool_provider(self, name: str, provider_type: str,
                          config_json: str = "{}") -> int:
        """Register a tool provider. Returns the provider ID."""
        cur = self._execute_write(
            "INSERT OR IGNORE INTO tool_providers "
            "(name, type, config_json, created_at) VALUES (?,?,?,?)",
            (name, provider_type, config_json, time.time()),
        )
        if cur.lastrowid:
            return cur.lastrowid
        row = self.conn.execute(
            "SELECT id FROM tool_providers WHERE name=?", (name,)
        ).fetchone()
        return row[0] if row else 0

    def get_tool_providers(self) -> list[dict]:
        """Return all registered tool providers."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT * FROM tool_providers ORDER BY name"
                ).fetchall()]

    def delete_tool_provider(self, provider_id: int) -> None:
        """Delete a tool provider."""
        self._execute_write(
            "DELETE FROM tool_providers WHERE id=?", (provider_id,))

    # ------------------------------------------------------------------
    # Entity-Tool Assignments
    # ------------------------------------------------------------------

    def add_entity_tool(self, entity_id: int, tool_name: str,
                        access_mode: str = "private") -> None:
        """Assign a tool to an entity."""
        self._execute_write(
            "INSERT OR REPLACE INTO entity_tools "
            "(entity_id, tool_name, access_mode, enabled) VALUES (?,?,?,1)",
            (entity_id, tool_name, access_mode),
        )

    def remove_entity_tool(self, entity_id: int, tool_name: str) -> None:
        """Remove a tool assignment from an entity."""
        self._execute_write(
            "DELETE FROM entity_tools WHERE entity_id=? AND tool_name=?",
            (entity_id, tool_name),
        )

    def get_entity_tools(self, entity_id: int) -> list[dict]:
        """Return all tool assignments for an entity."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT * FROM entity_tools WHERE entity_id=? AND enabled=1",
                    (entity_id,),
                ).fetchall()]

    def get_entity_tool(self, entity_id: int, tool_name: str) -> Optional[dict]:
        """Get a specific tool assignment for an entity."""
        row = self.conn.execute(
            "SELECT * FROM entity_tools WHERE entity_id=? AND tool_name=?",
            (entity_id, tool_name),
        ).fetchone()
        return dict(row) if row else None

    def get_shared_tools_for_discussion(self, discussion_id: int) -> list[dict]:
        """Return all shared-mode tool assignments for entities in a discussion."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT et.* FROM entity_tools et "
                    "JOIN discussion_members dm ON et.entity_id = dm.entity_id "
                    "WHERE dm.discussion_id=? AND et.access_mode='shared' "
                    "AND et.enabled=1",
                    (discussion_id,),
                ).fetchall()]

    # ------------------------------------------------------------------
    # Discussion Tool Overrides
    # ------------------------------------------------------------------

    def set_discussion_tool_override(self, discussion_id: int, entity_id: int,
                                     tool_name: str, enabled: bool) -> None:
        """Set a per-discussion tool override."""
        self._execute_write(
            "INSERT OR REPLACE INTO discussion_tool_overrides "
            "(discussion_id, entity_id, tool_name, enabled) VALUES (?,?,?,?)",
            (discussion_id, entity_id, tool_name, int(enabled)),
        )

    def get_discussion_tool_overrides(self, discussion_id: int,
                                       entity_id: int) -> list[dict]:
        """Get tool overrides for an entity in a specific discussion."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT * FROM discussion_tool_overrides "
                    "WHERE discussion_id=? AND entity_id=?",
                    (discussion_id, entity_id),
                ).fetchall()]

    def get_prompts(self, role: str = "", target: str = "",
                    task: str = "") -> list[dict]:
        """Retrieve prompts, optionally filtered by role, target, and/or task."""
        sql = "SELECT * FROM prompts WHERE 1=1"
        params: list[str] = []
        if role:
            sql += " AND role=?"
            params.append(role)
        if target:
            sql += " AND target=?"
            params.append(target)
        if task:
            sql += " AND task=?"
            params.append(task)
        sql += " ORDER BY is_default DESC, name"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_prompt(self, prompt_id: int) -> Optional[dict]:
        """Retrieve a single prompt by ID."""
        row = self.conn.execute(
            "SELECT * FROM prompts WHERE id=?", (prompt_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_prompt_by_task(self, role: str, target: str,
                           task: str) -> Optional[dict]:
        """Get the first matching prompt for a role/target/task (prefers default)."""
        row = self.conn.execute(
            "SELECT * FROM prompts WHERE role=? AND target=? AND task=? "
            "ORDER BY is_default DESC LIMIT 1",
            (role, target, task),
        ).fetchone()
        return dict(row) if row else None

    def save_prompt(self, prompt_id: Optional[int], name: str, role: str,
                    target: str, task: str, content: str) -> int:
        """Create or update a prompt template. Returns the prompt ID."""
        now = time.time()
        if prompt_id:
            self._execute_write(
                "UPDATE prompts SET name=?, role=?, target=?, task=?, "
                "content=?, updated_at=? WHERE id=?",
                (name, role, target, task, content, now, prompt_id),
            )
        else:
            cur = self._execute_write(
                "INSERT INTO prompts (name,role,target,task,content,"
                "is_default,created_at,updated_at) VALUES (?,?,?,?,?,0,?,?)",
                (name, role, target, task, content, now, now),
            )
            prompt_id = cur.lastrowid
        return prompt_id

    def delete_prompt(self, prompt_id: int) -> None:
        """Delete a prompt by ID."""
        self._execute_write("DELETE FROM prompts WHERE id=?", (prompt_id,))

    # ------------------------------------------------------------------
    # Providers
    # ------------------------------------------------------------------

    def add_provider(self, name: str, base_url: str,
                     api_key_env: str = "") -> int:
        """Add a new API provider. Returns the new provider ID."""
        cur = self._execute_write(
            "INSERT INTO providers (name,base_url,api_key_env,created_at) "
            "VALUES (?,?,?,?)",
            (name, base_url, api_key_env, time.time()),
        )
        return cur.lastrowid

    def get_providers(self) -> list[dict]:
        """Return all providers ordered by name."""
        return [dict(r) for r in
                self.conn.execute("SELECT * FROM providers ORDER BY name")
                .fetchall()]

    def get_provider(self, provider_id: int) -> Optional[dict]:
        """Retrieve a single provider by ID."""
        row = self.conn.execute(
            "SELECT * FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_provider(self, provider_id: int, **kwargs: object) -> None:
        """Update a provider's mutable fields."""
        self._update_row(
            "providers", provider_id,
            allowed={"name", "base_url", "api_key_env"},
            **kwargs,
        )

    def delete_provider(self, provider_id: int) -> None:
        """Delete a provider by ID."""
        self._execute_write(
            "DELETE FROM providers WHERE id=?", (provider_id,))

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    def add_entity(self, name: str, entity_type: str,
                   avatar_color: str = DEFAULT_AVATAR_COLOR,
                   provider_id: int = 0, model: str = "",
                   temperature: float = DEFAULT_TEMPERATURE,
                   max_tokens: int = DEFAULT_MAX_TOKENS,
                   system_prompt: str = "") -> int:
        """Add a new entity profile. Returns the new entity ID."""
        now = time.time()
        prov_id = int(provider_id) if provider_id else None
        cur = self._execute_write(
            "INSERT INTO entities "
            "(name,entity_type,avatar_color,provider_id,model,"
            "temperature,max_tokens,system_prompt,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, entity_type, avatar_color,
             prov_id, model, temperature, max_tokens,
             system_prompt, now, now),
        )
        return cur.lastrowid

    def get_entities(self, entity_type: str = "",
                     include_inactive: bool = False) -> list[dict]:
        """Return entities with joined provider info, optionally filtered by type."""
        base = (
            "SELECT e.*, p.name AS provider_name, p.base_url, "
            "p.api_key_env FROM entities e "
            "LEFT JOIN providers p ON e.provider_id=p.id"
        )
        conditions = []
        params: list[object] = []
        if not include_inactive:
            conditions.append("e.active=1")
        if entity_type:
            conditions.append("e.entity_type=?")
            params.append(entity_type)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = self.conn.execute(
            f"{base}{where} ORDER BY e.name", tuple(params),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_inactive_entities(self) -> list[dict]:
        """Return only inactive (soft-deleted) entities."""
        rows = self.conn.execute(
            "SELECT e.*, p.name AS provider_name, p.base_url, "
            "p.api_key_env FROM entities e "
            "LEFT JOIN providers p ON e.provider_id=p.id "
            "WHERE e.active=0 ORDER BY e.name",
        ).fetchall()
        return [dict(r) for r in rows]

    def get_entity(self, entity_id: int) -> Optional[dict]:
        """Retrieve a single entity with joined provider info."""
        row = self.conn.execute(
            "SELECT e.*, p.name AS provider_name, p.base_url, "
            "p.api_key_env FROM entities e "
            "LEFT JOIN providers p ON e.provider_id=p.id "
            "WHERE e.id=?",
            (entity_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_entity(self, entity_id: int, **kwargs: object) -> None:
        """Update an entity's mutable fields."""
        self._update_row(
            "entities", entity_id,
            allowed={
                "name", "entity_type", "avatar_color", "provider_id",
                "model", "temperature", "max_tokens", "system_prompt",
            },
            extra_sets={"updated_at": time.time()},
            **kwargs,
        )

    def delete_entity(self, entity_id: int) -> dict:
        """Delete an entity by ID, or deactivate if referenced by past discussions.

        Returns {"deleted": True} or {"deactivated": True}.
        """
        try:
            self._execute_write(
                "DELETE FROM entities WHERE id=?", (entity_id,))
            return {"deleted": True}
        except sqlite3.IntegrityError:
            self._execute_write(
                "UPDATE entities SET active=0, updated_at=? WHERE id=?",
                (time.time(), entity_id))
            return {"deactivated": True}

    def reactivate_entity(self, entity_id: int) -> bool:
        """Reactivate a soft-deleted entity."""
        self._execute_write(
            "UPDATE entities SET active=1, updated_at=? WHERE id=?",
            (time.time(), entity_id))
        return True

    # ------------------------------------------------------------------
    # Discussions
    # ------------------------------------------------------------------

    def create_discussion(self, topic: str,
                          moderator_id: int = 0) -> int:
        """Create a new discussion record. Returns the discussion ID."""
        mod_id = int(moderator_id) if moderator_id else None
        cur = self._execute_write(
            "INSERT INTO discussions (topic,moderator_id,started_at,status) "
            "VALUES (?,?,?,?)",
            (topic, mod_id, time.time(), "setup"),
        )
        return cur.lastrowid

    def get_discussions(self) -> list[dict]:
        """Return non-deleted discussions ordered by start time (newest first)."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT * FROM discussions "
                    "WHERE deleted_at IS NULL "
                    "ORDER BY started_at DESC"
                ).fetchall()]

    def get_discussion(self, discussion_id: int) -> Optional[dict]:
        """Retrieve a single discussion by ID."""
        row = self.conn.execute(
            "SELECT * FROM discussions WHERE id=?", (discussion_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_discussion(self, discussion_id: int, **kwargs: object) -> None:
        """Update a discussion's mutable fields."""
        self._update_row(
            "discussions", discussion_id,
            allowed={"topic", "moderator_id", "status", "ended_at", "started_at",
                     "max_rounds"},
            **kwargs,
        )

    def soft_delete_discussions(self, discussion_ids: list[int]) -> int:
        """Soft-delete discussions by setting deleted_at. Returns count deleted."""
        if not discussion_ids:
            return 0
        placeholders = ",".join("?" * len(discussion_ids))
        with self._lock:
            cur = self.conn.execute(
                f"UPDATE discussions SET deleted_at = ? "
                f"WHERE id IN ({placeholders}) AND deleted_at IS NULL",
                (time.time(), *discussion_ids),
            )
            self.conn.commit()
        return cur.rowcount

    def restore_discussion(self, discussion_id: int) -> bool:
        """Restore a soft-deleted discussion."""
        with self._lock:
            cur = self.conn.execute(
                "UPDATE discussions SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
                (discussion_id,),
            )
            self.conn.commit()
        return cur.rowcount > 0

    def purge_deleted_discussions(self, max_days: int = MAX_DAYS_KEEP_DELETED) -> int:
        """Hard-delete discussions soft-deleted more than max_days ago.
        Cascades to messages, discussion_members, and storyboard_entries.
        Returns count of discussions purged.
        """
        cutoff = time.time() - (max_days * 86400)
        with self._lock:
            ids = [r[0] for r in self.conn.execute(
                "SELECT id FROM discussions WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                (cutoff,),
            ).fetchall()]
            if not ids:
                return 0
            placeholders = ",".join("?" * len(ids))
            self.conn.execute(f"DELETE FROM storyboard_entries WHERE discussion_id IN ({placeholders})", ids)
            self.conn.execute(f"DELETE FROM messages WHERE discussion_id IN ({placeholders})", ids)
            self.conn.execute(f"DELETE FROM discussion_members WHERE discussion_id IN ({placeholders})", ids)
            self.conn.execute(f"DELETE FROM discussions WHERE id IN ({placeholders})", ids)
            self.conn.commit()
        return len(ids)

    def add_discussion_member(self, discussion_id: int, entity_id: int,
                              is_moderator: bool = False,
                              also_participant: bool = False,
                              turn_position: Optional[int] = None,
                              participant_role: str = "standard") -> None:
        """Add or update a discussion member record."""
        self._execute_write(
            "INSERT OR REPLACE INTO discussion_members "
            "(discussion_id,entity_id,is_moderator,also_participant,"
            "turn_position,participant_role) VALUES (?,?,?,?,?,?)",
            (discussion_id, entity_id, int(is_moderator),
             int(also_participant), turn_position, participant_role),
        )

    def get_discussion_members(self, discussion_id: int) -> list[dict]:
        """Return all members of a discussion with joined entity and provider info."""
        rows = self.conn.execute(
            "SELECT dm.entity_id AS id, dm.*, e.name, e.entity_type, "
            "e.avatar_color, e.provider_id, e.model, e.temperature, "
            "e.max_tokens, e.system_prompt, p.base_url, p.api_key_env "
            "FROM discussion_members dm "
            "JOIN entities e ON dm.entity_id=e.id "
            "LEFT JOIN providers p ON e.provider_id=p.id "
            "WHERE dm.discussion_id=? "
            "ORDER BY dm.turn_position NULLS LAST, e.name",
            (discussion_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_discussion_member(self, discussion_id: int,
                              entity_id: int) -> Optional[dict]:
        """Return a single discussion member record, or None."""
        row = self.conn.execute(
            "SELECT * FROM discussion_members "
            "WHERE discussion_id=? AND entity_id=?",
            (discussion_id, entity_id),
        ).fetchone()
        return dict(row) if row else None

    def update_member_role(self, discussion_id: int, entity_id: int,
                           role: str) -> None:
        """Update the participant_role of a discussion member."""
        self._execute_write(
            "UPDATE discussion_members SET participant_role=? "
            "WHERE discussion_id=? AND entity_id=?",
            (role, discussion_id, entity_id),
        )

    def remove_discussion_member(self, discussion_id: int,
                                 entity_id: int) -> None:
        """Remove a member from a discussion."""
        self._execute_write(
            "DELETE FROM discussion_members "
            "WHERE discussion_id=? AND entity_id=?",
            (discussion_id, entity_id),
        )

    def get_max_turn_number(self, discussion_id: int) -> int:
        """Return the highest turn_number from messages for a discussion."""
        row = self.conn.execute(
            "SELECT MAX(turn_number) FROM messages WHERE discussion_id=?",
            (discussion_id,),
        ).fetchone()
        return row[0] if row and row[0] is not None else 0

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def add_message(self, discussion_id: int, entity_id: int,
                    content: str, role: str, turn_number: int = 0,
                    model_used: str = "", prompt_tokens: int = 0,
                    completion_tokens: int = 0, total_tokens: int = 0,
                    latency_ms: int = 0, temperature_used: float = 0,
                    prompt_id: int = 0,
                    tool_calls_json: str = "") -> int:
        """Store a message and return its generated ID."""
        cur = self._execute_write(
            "INSERT INTO messages "
            "(discussion_id,entity_id,content,role,turn_number,"
            "timestamp,model_used,prompt_tokens,completion_tokens,"
            "total_tokens,latency_ms,temperature_used,prompt_id,"
            "tool_calls_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (discussion_id, entity_id, content, role, turn_number,
             time.time(), model_used or None, prompt_tokens or None,
             completion_tokens or None, total_tokens or None,
             latency_ms or None, temperature_used or None,
             prompt_id or None, tool_calls_json or None),
        )
        return cur.lastrowid

    def get_messages(self, discussion_id: int) -> list[dict]:
        """Return all messages for a discussion with entity names, ordered by time."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT m.*, e.name AS entity_name, e.avatar_color "
                    "FROM messages m "
                    "JOIN entities e ON m.entity_id=e.id "
                    "WHERE m.discussion_id=? ORDER BY m.timestamp",
                    (discussion_id,),
                ).fetchall()]

    # ------------------------------------------------------------------
    # Storyboard
    # ------------------------------------------------------------------

    def add_storyboard_entry(self, discussion_id: int, turn_number: int,
                             summary: str,
                             speaker_entity_id: int = 0) -> int:
        """Add a storyboard entry and return its auto-generated row ID."""
        cur = self._execute_write(
            "INSERT INTO storyboard_entries "
            "(discussion_id,turn_number,summary,speaker_entity_id,timestamp) "
            "VALUES (?,?,?,?,?)",
            (discussion_id, turn_number, summary,
             speaker_entity_id or None, time.time()),
        )
        return cur.lastrowid or 0

    def get_storyboard(self, discussion_id: int) -> list[dict]:
        """Return all storyboard entries for a discussion, ordered by time."""
        return [dict(r) for r in
                self.conn.execute(
                    "SELECT se.*, e.name AS speaker_name "
                    "FROM storyboard_entries se "
                    "LEFT JOIN entities e ON se.speaker_entity_id=e.id "
                    "WHERE se.discussion_id=? ORDER BY se.timestamp",
                    (discussion_id,),
                ).fetchall()]

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
