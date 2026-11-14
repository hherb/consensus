"""A participant's AI turn: prompt selection, the structured-output path
and the tool execution loop."""

import json
import logging
import time

from ..ai_client import AIResponse
from ..methods import get_active_method
from ..models import Entity
from ..structured_output import generate_structured_turn
from ..tools import MAX_TOOL_ITERATIONS, ToolCallRecord
from ..tools_image import is_vision_capable

logger = logging.getLogger(__name__)


class TurnMixin:
    """Generates an AI participant's contribution."""

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

        # Check for discussion images and build appropriate context
        discussion_images = []
        if self.discussion.id:
            discussion_images = self.db.get_discussion_images(self.discussion.id)

        if discussion_images and is_vision_capable(
            cfg.model, pricing_cache=self.db.pricing, base_url=cfg.base_url,
        ):
            messages = await self._build_multimodal_context(
                system_prompt, task,
                current_entity_id=entity.id,
                images=discussion_images,
            )
        else:
            messages = await self._build_context(system_prompt, task,
                                                 current_entity_id=entity.id)

        # Structured-output phases (issue #23): force the declared
        # output tool instead of free-text parsing.  Registry tools are
        # not offered on these turns — the phase output is the task.
        output_spec = (method.get_output_tool(entity, self.discussion)
                       if method else None)
        if output_spec is not None:
            return await generate_structured_turn(
                client, cfg, entity, self.discussion, messages,
                output_spec, method)

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
                dsml_content = msg_dict.get("content") or ""
                # Use placeholder if empty — some APIs reject empty assistant messages
                if not dsml_content.strip():
                    dsml_content = "(Calling tools...)"
                messages.append({
                    "role": "assistant",
                    "content": dsml_content,
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

                self._emit_progress({
                    "discussion_id": self.discussion.id,
                    "entity_name": entity.name,
                    "tool_name": tool_name,
                    "message": f"Using {tool_name}...",
                })
                start = time.monotonic()
                tool_result = await self._tool_registry.execute(
                    tool_name=tool_name,
                    arguments=arguments,
                    caller_entity_id=entity.id,
                    discussion_id=self.discussion.id,
                    moderator_id=self.discussion.moderator_id,
                )
                tool_latency = int((time.monotonic() - start) * 1000)
                self._emit_progress({
                    "discussion_id": self.discussion.id,
                    "entity_name": entity.name,
                    "tool_name": tool_name,
                    "message": (
                        f"{tool_name} failed — continuing..."
                        if tool_result.is_error
                        else f"{tool_name} finished; thinking..."
                    ),
                })

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
