"""Database mixin for prompt template CRUD and seeding."""

import time
from typing import Optional

# Shared prompt templates used by both _seed_default_prompts and
# _seed_devils_advocate_prompts to avoid content divergence.
_DEVILS_ADVOCATE_SYSTEM = (
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
)

_DEVILS_ADVOCATE_TURN = (
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
)

_DEVILS_ADVOCATE_TEMPLATES = [
    {
        "name": "Devil's Advocate – System",
        "role": "participant", "target": "ai",
        "task": "system_devils_advocate",
        "content": _DEVILS_ADVOCATE_SYSTEM,
    },
    {
        "name": "Devil's Advocate – Turn",
        "role": "participant", "target": "ai",
        "task": "turn_devils_advocate",
        "content": _DEVILS_ADVOCATE_TURN,
    },
]


class PromptsMixin:
    """Mixin providing prompt template database operations.

    Expects host class to provide:
        conn: sqlite3.Connection
        _lock: threading.Lock
        _execute_write(sql, params) -> sqlite3.Cursor
    """

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
            *_DEVILS_ADVOCATE_TEMPLATES,
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

    def _seed_devils_advocate_prompts(self) -> None:
        """Add devil's advocate prompt templates if not already present.

        This handles the migration path for databases created before the
        Devil's Advocate templates were added to _seed_default_prompts.
        """
        existing = self.conn.execute(
            "SELECT COUNT(*) FROM prompts WHERE task='system_devils_advocate'"
        ).fetchone()[0]
        if existing > 0:
            return
        now = time.time()
        with self._lock:
            for d in _DEVILS_ADVOCATE_TEMPLATES:
                self.conn.execute(
                    "INSERT INTO prompts (name, role, target, task, content, "
                    "is_default, created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
                    (d["name"], d["role"], d["target"], d["task"],
                     d["content"], now, now),
                )
            self.conn.commit()

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
