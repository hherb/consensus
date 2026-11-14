"""Tool handlers that change data: memories, knowledge triples, and
``run_discussion``, which runs a whole AI discussion."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from ..models import MessageRole
from .constants import (
    AGENT_ENTITY_NAME, AUTO_SELECT_ENTITIES, DEFAULT_COST_LIMIT_USD,
    DEFAULT_MAX_ROUNDS, RUN_DISCUSSION_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


class WriteHandlersMixin:
    """Handlers that store memories, assert knowledge and run discussions."""

    # -----------------------------------------------------------------------
    # Tool handlers — write
    # -----------------------------------------------------------------------

    async def _handle_store_memory(self, args: dict) -> str:
        content = args.get("content", "").strip()
        if not content:
            return "Error: No content provided."
        entity_id = self._resolve_entity_id(int(args.get("entity_id", 0)))
        memory_id = str(uuid.uuid4())
        self._db.add_entity_memory(
            memory_id=memory_id, entity_id=entity_id,
            content=content, discussion_id=None,
        )
        # Embed in background
        try:
            from ..tools_memory import _pack_embedding
            embed = self._get_embed_client()
            vec = await embed.embed(content)
            blob = _pack_embedding(vec)
            self._db.set_entity_memory_embedding(memory_id, blob)
        except Exception as e:
            logger.warning("Could not embed memory %s: %s", memory_id, e)
        return f"Memory stored (id: {memory_id})."

    async def _handle_delete_memory(self, args: dict) -> str:
        memory_id = args.get("memory_id", "").strip()
        if not memory_id:
            return "Error: No memory_id provided."
        # HARD RULE: can only delete own agent's memories
        agent_id = self._get_agent_entity_id()
        deleted = self._db.delete_entity_memory(memory_id, agent_id)
        if deleted:
            return f"Memory {memory_id} deleted."
        return "Memory not found or does not belong to this agent."

    async def _handle_assert_knowledge(self, args: dict) -> str:
        subject = args.get("subject", "").strip()
        relation = args.get("relation", "").strip()
        obj = args.get("object", "").strip()
        description = args.get("description", "").strip() or None
        if not subject or not relation or not obj:
            return "Error: subject, relation, and object are all required."
        # Upsert subject node
        subj_row = self._db.get_kg_node_by_label(subject)
        if not subj_row:
            subj_id = str(uuid.uuid4())
            self._db.upsert_kg_node(subj_id, subject, "concept", description)
        else:
            subj_id = subj_row["id"]
        # Upsert object node
        obj_row = self._db.get_kg_node_by_label(obj)
        if not obj_row:
            obj_id = str(uuid.uuid4())
            self._db.upsert_kg_node(obj_id, obj, "concept", None)
        else:
            obj_id = obj_row["id"]
        edge_id = str(uuid.uuid4())
        self._db.add_kg_edge(
            edge_id=edge_id, source_id=subj_id, target_id=obj_id,
            relation=relation, discussion_id=None,
        )
        # Embed nodes in background
        try:
            from ..tools_memory import _pack_embedding
            embed = self._get_embed_client()
            for node_id, label in [(subj_id, subject), (obj_id, obj)]:
                vec = await embed.embed(label)
                blob = _pack_embedding(vec)
                self._db.set_kg_node_embedding(node_id, blob)
        except Exception as e:
            logger.warning("Could not embed KG nodes: %s", e)
        return f"Asserted: {subject} --[{relation}]--> {obj}"

    # -----------------------------------------------------------------------
    # Tool handler — run_discussion
    # -----------------------------------------------------------------------

    async def _handle_run_discussion(self, args: dict) -> str:
        from ..app import ConsensusApp

        topic = args.get("topic", "").strip()
        if not topic:
            return "Error: No topic provided."
        entity_ids = args.get("entity_ids")
        max_rounds = int(args.get("max_rounds", DEFAULT_MAX_ROUNDS))
        moderator_id = args.get("moderator_id")
        cost_limit = float(args.get("cost_limit", DEFAULT_COST_LIMIT_USD))

        # Resolve entities
        if not entity_ids:
            all_ai = self._db.get_entities(entity_type="ai")
            all_ai = [e for e in all_ai if e["name"] != AGENT_ENTITY_NAME]
            if len(all_ai) < 2:
                return ("Error: Need at least 2 AI entities configured. "
                        "Create them in the Consensus UI first.")
            entity_ids = [e["id"] for e in all_ai[:AUTO_SELECT_ENTITIES]]

        # Validate all are AI
        for eid in entity_ids:
            entity = self._db.get_entity(eid)
            if not entity:
                return f"Error: Entity {eid} not found."
            if entity["entity_type"] == "human":
                return (f"Error: Entity '{entity['name']}' is human. "
                        "Only AI entities can participate in automated discussions.")

        if not moderator_id:
            moderator_id = entity_ids[0]

        # Create and configure a fresh ConsensusApp
        app = ConsensusApp(self._db.db_path)

        for eid in entity_ids:
            is_mod = (eid == moderator_id)
            app.add_to_discussion(eid, is_moderator=is_mod)

        app.set_topic(topic)

        mod_participates = len(entity_ids) <= 2
        result = await app.start_discussion(
            moderator_participates=mod_participates,
            max_rounds=max_rounds,
            cost_limit=cost_limit,
        )
        if isinstance(result, dict) and "error" in result:
            return f"Error starting discussion: {result['error']}"

        # Run turn loop. A skipped turn (the participant's provider failed)
        # is recorded and the discussion goes on, as it does in the UI; any
        # other failure stops it, since retrying would repeat the same turn.
        turn_errors: list[str] = []

        async def _run_turns() -> str:
            """Run AI turns to the end; return why it stopped early, or ""."""
            while app.discussion.is_active and app.discussion.status == "active":
                current = app.discussion.current_speaker
                if not current or current.entity_type.value == "human":
                    return "Discussion stopped: no AI speaker due"
                turn_result = await app.generate_ai_turn()
                if isinstance(turn_result, dict) and "error" in turn_result:
                    if not turn_result.get("skipped"):
                        return f"Turn failed: {turn_result['error']}"
                    turn_errors.append(f"{current.name}: {turn_result['error']}")
                complete_result = await app.complete_turn()
                if isinstance(complete_result, dict):
                    if "error" in complete_result:
                        return ("Turn completion failed: "
                                f"{complete_result['error']}")
                    if (complete_result.get("max_rounds_reached")
                            or complete_result.get("cost_limit_reached")
                            or complete_result.get("method_complete")):
                        return ""
            return ""

        try:
            run_error = await asyncio.wait_for(
                _run_turns(), timeout=RUN_DISCUSSION_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            run_error = (f"Discussion timed out after "
                         f"{RUN_DISCUSSION_TIMEOUT_SECONDS} s")
        if run_error:
            logger.warning("run_discussion: %s", run_error)

        # Conclude
        conclusion_error = ""
        try:
            conclude_result = await app.conclude_discussion()
            conclusion_error = conclude_result.get("conclusion_error", "")
        except Exception as e:
            logger.warning("Conclusion failed: %s", e)
            conclusion_error = str(e) or type(e).__name__

        # Extract results.  Match on the moderator's own message rather
        # than any message containing "Final Synthesis": the #71 failure
        # notice contains that phrase too, and returning it here would
        # hand an automated caller the error text as the synthesis.
        conclusion = ""
        for m in reversed(app.discussion.messages):
            if m.role == MessageRole.MODERATOR and "Final Synthesis" in m.content:
                conclusion = m.content
                break

        total_cost = sum(m.cost or 0 for m in app.discussion.messages)
        participant_names = [e.name for e in app.discussion.entities]

        return json.dumps({
            "discussion_id": app.discussion.id,
            "topic": topic,
            "conclusion": conclusion,
            "conclusion_error": conclusion_error,
            "error": run_error,
            "turn_errors": turn_errors,
            "total_messages": len(app.discussion.messages),
            "total_cost": round(total_cost, 6),
            "participants": participant_names,
            "rounds_completed": app.discussion.current_round,
        }, indent=2)
