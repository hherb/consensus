# 6. Data Flow and Discussion Lifecycle

[Back to index](programmer-manual.md) | [Previous: Database](05-database.md) | [Next: API Reference](07-api-reference.md)

---

## Data Flow: A Turn From End to End

Here is what happens when an AI participant takes a turn in web mode:

```
1. Frontend (app.js)
   runTurnCycle() detects current speaker is AI
       |
       v
2. Frontend calls api.generateAiTurn()
   WebAPI._post('generate_ai_turn', {})
       |
       v
3. server.py: handle_api() dispatches to
   app.generate_ai_turn()
       |
       v
4. app.py: ConsensusApp.generate_ai_turn()
   - Checks current_speaker is AI
   - Calls self.moderator.generate_turn(entity)
       |
       v
5. moderator.py: Moderator.generate_turn(entity)
   - Resolves the system prompt (from DB or entity config)
   - Resolves the turn prompt
   - Builds context messages (system + last 20 messages + task)
   - Gets/creates an AIClient for this entity
   - Gets available tools via tool_registry.get_tools_for_entity()
   - Enters tool execution loop:
     a. Calls client.complete_with_tools(messages, model, tools, ...)
     b. If model returns tool_calls:
        - Executes each tool via tool_registry.execute()
        - Records ToolCallRecord (name, args, result, latency)
        - Appends tool results to context
        - Loops back (up to MAX_TOOL_ITERATIONS = 5)
     c. If no tool_calls or final iteration: exits loop
   - Returns AIResponse(content, model, tokens, latency, tool_calls)
       |
       v
6. Back in app.py:
   - Creates a Message dataclass from the AIResponse
   - Serialises tool_calls to tool_calls_json
   - Appends to self.discussion.messages (in-memory)
   - Persists to DB via self.db.add_message(...)
   - Calls self._notify() which triggers state push
   - Returns the message dict
       |
       v
7. server.py: wraps result + full state in JSON response
       |
       v
8. Frontend receives response:
   - onStateUpdate(json.state) re-renders messages
   - Tool calls shown inline (collapsible)
   - Proceeds to call api.completeTurn()
       |
       v
9. app.py: ConsensusApp.complete_turn()
   - If moderator is AI: generates summary via moderator.generate_summary()
   - Stores summary as a message + storyboard entry
   - Calls moderator.advance_turn() to move to next speaker
   - Returns next speaker info + full state
       |
       v
10. Frontend: if next speaker is also AI, loops back to step 1
```

In desktop mode, steps 2-7 go through `DesktopBridge._run_async()` instead of
HTTP, but the `ConsensusApp` logic is identical.

---

## The Discussion Lifecycle

A discussion moves through these states:

```
   setup  -->  active  -->  concluded
                 ^  |           |
                 |  v           |
               paused      (reopen)
                               |
                               v
                            active
```

### Setup Phase

1. User creates entity profiles (Profiles tab) and configures providers
   (Providers tab)
2. User enters a topic
3. User adds entities to the discussion, designates one as moderator
4. User optionally assigns tools to entities (Profiles tab)
5. User clicks "Start Discussion"

`start_discussion()`:
- Validates: topic set, >=2 participants, moderator designated
- Creates a DB `discussions` record (status=`active`)
- Builds the turn order (all participants except moderator, unless moderator
  also participates)
- Stores `discussion_members` records with turn positions
- Generates the moderator's opening message (from the "open" prompt template)
- Sets `discussion.is_active = True`

### Active Phase

The discussion runs in turns:
1. The current speaker takes their turn (human types, AI generates)
2. AI speakers may use tools during generation (web search, etc.)
3. After each turn, the moderator provides a summary (AI generates, or human
   types)
4. The summary is stored as a storyboard entry
5. Turn advances to the next speaker in order

Special actions during the active phase:
- **Reassign turn** -- jump to a different speaker
- **Mediate** -- moderator intervenes (AI generates mediation text, or human
  types)
- **Pause** -- temporarily suspend the discussion
- **Add/remove participants** -- dynamically modify the participant list
  mid-discussion
- **Conclude** -- end the discussion

### Paused Phase

`pause_discussion()`:
- Sets `discussion.status` to `paused`
- Logs a system message: "Discussion paused by [entity]"
- Persists turn state for later restoration

While paused:
- Participants can be added or removed
- Turn order is updated when participants change
- No turns can be taken

`resume_discussion()`:
- Restores status to `active`
- Logs a system message: "Discussion resumed by [entity]"
- Re-establishes turn continuity

### Concluded Phase

`conclude_discussion()`:
- If the moderator is AI, generates a final synthesis (conclusion prompt)
- Stores the conclusion as a message and storyboard entry
- Sets `discussion.is_active = False`
- Updates the DB record to `status='concluded'`, sets `ended_at`

### Reopening a Concluded Discussion

`reopen_discussion()` transitions a concluded discussion back to the active
phase, allowing further turns, summaries, and eventually a new conclusion.

### Loading Past Discussions

`load_discussion(discussion_id)` reconstructs the full `Discussion` object
from DB rows (members, messages, storyboard, turn order) and replaces the
current in-memory state. If the discussion was still active or paused, it can
be continued.

### Resetting

`reset()` replaces `self.discussion` with a fresh `Discussion()` and creates
a new `Moderator`. This returns to the setup phase in the UI.

---

[Next: API Reference](07-api-reference.md)
