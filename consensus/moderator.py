"""Moderator logic for managing discussions."""

import asyncio
import json
import logging
import time
from typing import Callable, Optional

from .models import Discussion, Entity, EntityType
from .ai_client import AIClient, AIResponse
from .database import Database
from .methods import get_active_method
from .tools import ToolCallRecord, ToolRegistry, MAX_TOOL_ITERATIONS

logger = logging.getLogger(__name__)

# How many recent messages to include in AI context
CONTEXT_MESSAGE_LIMIT = 20

# Moderator AI generation settings
MODERATOR_TEMPERATURE = 0.5
SUMMARY_MAX_TOKENS = 2048
CONCLUSION_MAX_TOKENS = 4096
MEDIATION_MAX_TOKENS = 2048


class Moderator:
    """Manages discussion flow, turn-taking, and synthesis."""

    def __init__(self, discussion: Discussion, db: Database,
                 key_resolver: Optional[Callable[[int, str], str]] = None,
                 tool_registry: Optional[ToolRegistry] = None) -> None:
        self.discussion = discussion
        self.db = db
        self._clients: dict[int, AIClient] = {}
        # Optional callback: (provider_id, env_var) -> api_key
        # When set, overrides the entity's resolved api_key for AI calls.
        self._key_resolver = key_resolver
        self._tool_registry = tool_registry

    def resolve_prompt(self, role: str, target: str, task: str,
                       **variables: object) -> str:
        """Look up a prompt template from DB and fill in template variables."""
        row = self.db.get_prompt_by_task(role, target, task)
        if not row:
            return ""
        template: str = row["content"]
        for key, val in variables.items():
            template = template.replace("{" + key + "}", str(val))
        return template

    def prompt_id(self, role: str, target: str, task: str) -> int:
        """Get prompt ID for metadata logging."""
        row = self.db.get_prompt_by_task(role, target, task)
        return row["id"] if row else 0

    def _participant_names(self) -> str:
        """Return a formatted comma-separated list of participant names and types."""
        return ", ".join(
            f"{e.name} ({'AI' if e.entity_type == EntityType.AI else 'Human'})"
            for e in self.discussion.entities
        )

    def _build_context(self, system_prompt: str, task: str,
                        current_entity_id: Optional[int] = None) -> list[dict]:
        """Build a proper OpenAI message array from discussion history.

        Uses role-based formatting: messages from the current entity become
        'assistant' role, all others become 'user' role with speaker prefix.
        """
        messages: list[dict] = [{"role": "system", "content": system_prompt}]

        # Add discussion topic as initial user context
        messages.append({
            "role": "user",
            "content": f"Discussion topic: {self.discussion.topic}",
        })

        for msg in self.discussion.messages[-CONTEXT_MESSAGE_LIMIT:]:
            if current_entity_id and msg.entity_id == current_entity_id:
                role = "assistant"
                content = msg.content
            else:
                role = "user"
                content = f"[{msg.entity_name}]: {msg.content}"
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": task})
        return messages

    def _resolve_api_key(self, entity: Entity) -> str:
        """Resolve the API key for an entity, using BYOK resolver if available."""
        if not entity.ai_config:
            return ""
        if self._key_resolver:
            return self._key_resolver(
                entity.ai_config.provider_id,
                "",  # env_var looked up by resolver
            )
        return entity.ai_config.api_key

    def _get_client(self, entity: Entity) -> AIClient:
        """Return a cached AI client for the given entity, creating one if needed."""
        if not entity.ai_config:
            raise ValueError(f"{entity.name} has no AI configuration")
        api_key = self._resolve_api_key(entity)
        # Recreate client if the API key has changed (e.g. BYOK per-request)
        existing = self._clients.get(entity.id)
        if existing and existing.api_key != api_key:
            # Key changed — schedule async close and create new one
            old_client = self._clients.pop(entity.id)
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(old_client.close())

                def _on_close_done(t: asyncio.Task) -> None:  # type: ignore[type-arg]
                    exc = t.exception()
                    if exc:
                        logger.debug("Error closing old AI client: %s", exc)

                task.add_done_callback(_on_close_done)
            except RuntimeError:
                pass  # No event loop; client will be garbage-collected
        if entity.id not in self._clients:
            self._clients[entity.id] = AIClient(
                base_url=entity.ai_config.base_url,
                api_key=api_key,
            )
        return self._clients[entity.id]

    async def close(self) -> None:
        """Close all cached AI clients."""
        for client in self._clients.values():
            try:
                await client.close()
            except Exception:
                logger.debug("Error closing AI client", exc_info=True)
        self._clients.clear()

    async def generate_turn(self, entity: Entity,
                            participant_role: str = "standard") -> AIResponse:
        """Generate an AI entity's contribution to the discussion.

        If the entity has tools assigned and the tool_registry is available,
        uses the tool execution loop: the AI can call tools and receive
        results before producing its final response.
        """
        client = self._get_client(entity)  # raises if no ai_config
        assert entity.ai_config is not None
        cfg = entity.ai_config
        participants = self._participant_names()

        # Check for method-specific prompts first
        method = get_active_method(self.discussion)
        method_system = method.get_system_prompt(entity, self.discussion) if method else ""
        method_turn = method.get_turn_prompt(entity, self.discussion) if method else ""

        # Select prompt task variants based on participant role
        system_task = "system"
        turn_task = "turn"
        if participant_role == "devils_advocate":
            system_task = "system_devils_advocate"
            turn_task = "turn_devils_advocate"

        if method_system:
            system_prompt = method_system
        elif cfg.system_prompt:
            system_prompt = cfg.system_prompt
        else:
            system_prompt = self.resolve_prompt(
                "participant", "ai", system_task,
                entity_name=entity.name,
                topic=self.discussion.topic,
                participants=participants,
            )

        if method_turn:
            task = method_turn
        else:
            task = self.resolve_prompt(
                "participant", "ai", turn_task,
                entity_name=entity.name,
                topic=self.discussion.topic,
                participants=participants,
            )
        if not task:
            task = f"It is your turn to speak as {entity.name}. Be concise."

        messages = self._build_context(system_prompt, task,
                                       current_entity_id=entity.id)

        # Get tools for this entity
        tool_schemas: list[dict] = []
        if self._tool_registry and self.discussion.id:
            tools = await self._tool_registry.get_tools_for_entity(
                entity.id, self.discussion.id,
                moderator_id=self.discussion.moderator_id,
            )
            tool_schemas = [t.to_openai_schema() for t in tools]

        # If no tools, use the simple path
        if not tool_schemas:
            return await client.complete(
                messages=messages,
                model=cfg.model,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            )

        # Augment system prompt with tool-use encouragement
        tool_names = ", ".join(t["function"]["name"] for t in tool_schemas)
        messages[0]["content"] += (
            f"\n\nYou have access to the following tools: {tool_names}. "
            "Use them proactively whenever they would strengthen your contribution. "
            "If the topic involves current events, recent data, specific facts, "
            "or claims that could be verified or enriched by external information, "
            "call the appropriate tool NOW — do not merely suggest that a search "
            "should be done. Act, don't narrate."
        )

        # Tool execution loop
        all_tool_records: list[ToolCallRecord] = []
        intermediate_content: list[str] = []  # content from tool-call iterations
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_latency_ms = 0

        for iteration in range(MAX_TOOL_ITERATIONS):
            # On last iteration, remove tools to force a final text response
            current_tools = tool_schemas if iteration < MAX_TOOL_ITERATIONS - 1 else None
            if iteration == MAX_TOOL_ITERATIONS - 1:
                messages.append({
                    "role": "system",
                    "content": "Tool use limit reached. Provide your final response now.",
                })

            try:
                result = await client.complete_with_tools(
                    messages=messages,
                    model=cfg.model,
                    tools=current_tools,
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_tokens,
                )
            except Exception as exc:
                if iteration == 0 and current_tools:
                    # Model likely doesn't support tool calling — fall
                    # back to a plain completion and warn the user.
                    warning = (
                        f"{cfg.model} does not support tool use — "
                        f"tools are disabled for this response"
                    )
                    logger.warning(
                        "Tool-enabled request failed for %s (%s): %s. "
                        "Falling back to plain completion.",
                        entity.name, cfg.model, exc,
                    )
                    resp = await client.complete(
                        messages=messages,
                        model=cfg.model,
                        temperature=cfg.temperature,
                        max_tokens=cfg.max_tokens,
                    )
                    resp.warning = warning
                    return resp
                raise

            total_prompt_tokens += result["prompt_tokens"]
            total_completion_tokens += result["completion_tokens"]
            total_latency_ms += result["latency_ms"]

            msg_dict = result["message"]

            # Check for tool calls
            api_tool_calls = msg_dict.get("tool_calls", [])
            if not api_tool_calls:
                # No tool calls — return final response.
                # Combine any content produced alongside earlier tool-call
                # iterations with this final response so nothing is lost.
                final_content = msg_dict.get("content") or ""
                if intermediate_content:
                    parts = intermediate_content + ([final_content] if final_content else [])
                    final_content = "\n\n".join(parts)
                return AIResponse(
                    content=final_content,
                    model=result["model"],
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                    latency_ms=total_latency_ms,
                    tool_calls=all_tool_records,
                )

            # Capture any content the model produced alongside tool calls
            # (e.g. reasoning, partial analysis) so it isn't lost.
            interim = msg_dict.get("content") or ""
            if interim.strip():
                intermediate_content.append(interim.strip())

            # Detect whether tool calls came from DSML parsing (flag set
            # by ai_client when it promotes DSML markup to tool_calls).
            is_dsml = msg_dict.pop("_dsml_tool_calls", False)

            # Append assistant message to context.  For DSML models we
            # must NOT include the synthetic tool_calls field — the model
            # doesn't understand OpenAI-format tool_calls in its history.
            if is_dsml:
                # Keep only content (already DSML-stripped) for the
                # assistant turn; drop the synthetic tool_calls.
                messages.append({
                    "role": "assistant",
                    "content": msg_dict.get("content", ""),
                })
            else:
                messages.append(msg_dict)

            # Execute each tool call
            tool_results_for_dsml: list[str] = []
            for tc in api_tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                try:
                    arguments = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    arguments = {}

                start = time.monotonic()
                tool_result = await self._tool_registry.execute(
                    tool_name=tool_name,
                    arguments=arguments,
                    caller_entity_id=entity.id,
                    discussion_id=self.discussion.id,
                    moderator_id=self.discussion.moderator_id,
                )
                tool_latency = int((time.monotonic() - start) * 1000)

                # Record for persistence
                all_tool_records.append(ToolCallRecord(
                    tool_name=tool_name,
                    arguments=arguments,
                    result=tool_result.content,
                    is_error=tool_result.is_error,
                    latency_ms=tool_latency,
                ))

                if is_dsml:
                    # Collect results to send as a single user message
                    status = "ERROR" if tool_result.is_error else "OK"
                    tool_results_for_dsml.append(
                        f"[Tool: {tool_name}] ({status})\n{tool_result.content}"
                    )
                else:
                    # Standard OpenAI tool result message
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": tool_result.content,
                    })

            # For DSML models, deliver all tool results as a user message
            # since they don't understand role:"tool" messages.
            if is_dsml and tool_results_for_dsml:
                messages.append({
                    "role": "user",
                    "content": (
                        "Here are the results of the tool calls you requested. "
                        "Use this information to formulate your response:\n\n"
                        + "\n\n".join(tool_results_for_dsml)
                    ),
                })

        # Exhausted tool iterations — make one final plain completion to
        # force a substantive text response if we have no content yet.
        fallback_content = "\n\n".join(intermediate_content) if intermediate_content else ""
        if not fallback_content.strip() and all_tool_records:
            logger.info(
                "No text content after %d tool iterations for %s; "
                "requesting final plain response.",
                MAX_TOOL_ITERATIONS, entity.name,
            )
            messages.append({
                "role": "system",
                "content": (
                    "You have completed your research. Now provide your "
                    "substantive response based on what you found."
                ),
            })
            try:
                final_resp = await client.complete(
                    messages=messages,
                    model=cfg.model,
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_tokens,
                )
                fallback_content = final_resp.content
                total_prompt_tokens += final_resp.prompt_tokens
                total_completion_tokens += final_resp.completion_tokens
                total_latency_ms += final_resp.latency_ms
            except Exception:
                logger.warning(
                    "Final plain completion failed for %s", entity.name,
                    exc_info=True,
                )

        return AIResponse(
            content=fallback_content,
            model=cfg.model,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
            latency_ms=total_latency_ms,
            tool_calls=all_tool_records,
        )

    async def generate_summary(self,
                               next_speaker_name: str = "") -> AIResponse:
        """Generate a moderator summary after a participant's turn."""
        mod = self.discussion.moderator
        if not mod or mod.entity_type != EntityType.AI or not mod.ai_config:
            return AIResponse(content="")

        client = self._get_client(mod)
        last_msg = self.discussion.messages[-1] if self.discussion.messages else None
        speaker = last_msg.entity_name if last_msg else "Unknown"
        participants = self._participant_names()

        system_prompt = self.resolve_prompt(
            "moderator", "ai", "system",
            entity_name=mod.name,
            topic=self.discussion.topic,
            participants=participants,
        )

        # Check for method-specific summary prompt
        method = get_active_method(self.discussion)
        method_summary = (
            method.get_summary_prompt(self.discussion, speaker,
                                      next_speaker_name)
            if method else ""
        )

        if method_summary:
            task = method_summary
        else:
            task = self.resolve_prompt(
                "moderator", "ai", "summarize",
                entity_name=mod.name,
                topic=self.discussion.topic,
                speaker_name=speaker,
                turn_number=str(self.discussion.turn_number),
                participants=participants,
                next_speaker_name=next_speaker_name,
            )
        if not task:
            task = f"Summarize turn {self.discussion.turn_number} by {speaker}."

        return await client.complete(
            messages=self._build_context(system_prompt, task,
                                         current_entity_id=mod.id),
            model=mod.ai_config.model,
            temperature=MODERATOR_TEMPERATURE,
            max_tokens=SUMMARY_MAX_TOKENS,
        )

    async def generate_conclusion(self) -> AIResponse:
        """Generate a final synthesis/conclusion for the discussion."""
        mod = self.discussion.moderator
        if not mod or mod.entity_type != EntityType.AI or not mod.ai_config:
            return AIResponse(content="")

        client = self._get_client(mod)
        participants = self._participant_names()

        system_prompt = self.resolve_prompt(
            "moderator", "ai", "system",
            entity_name=mod.name,
            topic=self.discussion.topic,
            participants=participants,
        )

        # Check for method-specific conclusion prompt
        method = get_active_method(self.discussion)
        method_conclusion = (
            method.get_conclusion_prompt(self.discussion)
            if method else ""
        )

        if method_conclusion:
            task = method_conclusion
        else:
            task = self.resolve_prompt(
                "moderator", "ai", "conclude",
                topic=self.discussion.topic,
                participants=participants,
            )
        if not task:
            task = f"Conclude the discussion on '{self.discussion.topic}'."

        return await client.complete(
            messages=self._build_context(system_prompt, task,
                                         current_entity_id=mod.id),
            model=mod.ai_config.model,
            temperature=MODERATOR_TEMPERATURE,
            max_tokens=CONCLUSION_MAX_TOKENS,
        )

    async def mediate(self, context: str = "") -> AIResponse:
        """Have the moderator intervene to mediate a conflict."""
        mod = self.discussion.moderator
        if not mod or mod.entity_type != EntityType.AI or not mod.ai_config:
            return AIResponse(content="")

        client = self._get_client(mod)
        participants = self._participant_names()

        system_prompt = self.resolve_prompt(
            "moderator", "ai", "system",
            entity_name=mod.name,
            topic=self.discussion.topic,
            participants=participants,
        )

        task = self.resolve_prompt(
            "moderator", "ai", "mediate",
            context=context or "A disagreement has arisen.",
            topic=self.discussion.topic,
            participants=participants,
        )
        if not task:
            task = f"Mediate: {context or 'A disagreement has arisen.'}"

        return await client.complete(
            messages=self._build_context(system_prompt, task,
                                         current_entity_id=mod.id),
            model=mod.ai_config.model,
            temperature=MODERATOR_TEMPERATURE,
            max_tokens=MEDIATION_MAX_TOKENS,
        )

    def get_human_guidance(self, role: str) -> str:
        """Get guidance text to display to a human moderator or participant."""
        return self.resolve_prompt(
            role, "human", "guidance",
            topic=self.discussion.topic,
            participants=self._participant_names(),
        )

    def peek_next_speaker(self) -> Optional[Entity]:
        """Return the next speaker without advancing the turn index."""
        if not self.discussion.turn_order:
            return None
        next_idx = (
            (self.discussion.current_turn_index + 1)
            % len(self.discussion.turn_order)
        )
        next_id = self.discussion.turn_order[next_idx]
        return self.discussion.get_entity(next_id)

    def advance_turn(self) -> Optional[Entity]:
        """Advance to the next speaker in the turn order."""
        if not self.discussion.turn_order:
            return None
        self.discussion.current_turn_index = (
            (self.discussion.current_turn_index + 1)
            % len(self.discussion.turn_order)
        )
        self.discussion.turn_number += 1
        return self.discussion.current_speaker

    def reassign_turn(self, entity_id: int) -> Optional[Entity]:
        """Reassign the current turn to a specific entity."""
        entity = self.discussion.get_entity(entity_id)
        if entity and entity_id in self.discussion.turn_order:
            self.discussion.current_turn_index = (
                self.discussion.turn_order.index(entity_id)
            )
            return entity
        return None
