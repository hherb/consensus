# Chapter 6: Running a Discussion

Once you start a discussion, the interface changes to the live discussion view with three panels.

## The Interface

### Header Bar

The header shows:
- **Back button** — Returns to the setup interface (discussion continues in the background)
- **Topic title** — The discussion topic
- **Turn badge** — Shows "Turn N: [Speaker Name] (round X/Y)" during active turns, "Paused", or "Concluded"
- **Cost badge** — Running total of API costs (turns orange at 80% of budget)
- **Action buttons** — Reassign, Mediate, Pause/Resume, Conclude, Export. Once a discussion is concluded, a separate **Resume Discussion** button appears, which reopens it for further turns (distinct from Pause/Resume, which only suspends a live discussion)

### Left Panel — Participants

Shows all participants with:
- Colour-coded avatar with initials
- Name and type (Human, AI model name)
- **MOD** badge on the moderator
- **DA** badge on the Devil's Advocate
- An animated speaking indicator on the current speaker

### Centre Panel — Chat

The main conversation thread. Messages appear in order with:
- Speaker name and avatar
- Rendered Markdown content (bold, italic, code blocks, lists, headers, etc.)
- For AI messages: model name, token count (prompt/completion), latency, and cost
- Tool call history shown as collapsible sections (click to expand details)
- Typing indicator with stage text while AI is generating

At the bottom, a text input area appears when it is a human participant's turn.

### Right Panel — Storyboard

A running timeline of moderator summaries — one per turn. This provides a high-level narrative of the discussion's progression. The final conclusion appears here with distinct styling when the discussion ends.

## Moderator Controls

### Reassign Turn

Click **Reassign** to override the normal turn order and choose who speaks next. A dialog shows all participants — select one and confirm. Useful when:
- A specific participant needs to respond to a point
- You want to skip ahead in the rotation
- The method's turn order needs manual adjustment

### Mediate

Click **Mediate** to trigger a moderator intervention. A dialog appears where you can enter optional guidance text. The moderator will then provide a mediation message addressing the current state of the discussion.

### Pause and Resume

- **Pause** stops the discussion. While paused, you can:
  - Add new participants from your profiles
  - Remove existing participants
  - Review the conversation so far
- **Resume** continues from where the discussion left off

### Conclude

Click **Conclude** to end the discussion. The moderator generates a final synthesis that appears in both the chat and the storyboard. The discussion status changes to "Concluded".

A concluded discussion can be reopened — see [Chapter 13](13_history_and_export.md).

### Export

The **Export** dropdown offers three formats:
- **JSON** — Structured data with all metadata (messages, participants, costs, tool calls)
- **HTML** — Self-contained web page with styling, suitable for sharing or archiving
- **PDF** — Opens the HTML export for printing via your browser's print dialog

## How Turns Work

1. The moderator determines who speaks next (round-robin by default, or method-specific ordering)
2. If it's an AI participant's turn, the system sends the conversation context and prompts to the AI's model
3. The AI generates a response, potentially using tools (web search, document queries, etc.)
4. The moderator generates a summary of the turn (visible in the storyboard)
5. The next participant's turn begins

If it's a human participant's turn, the input area at the bottom of the chat becomes active. See [Chapter 7](07_human_participation.md).

## Turn Indicators

While an AI is generating, you'll see:
- A typing indicator with the participant's name
- Stage text describing what's happening (e.g., "Generating response", "Calling tool: web_search")
- A progress bar during multi-step tool use

## Cost Monitoring

The cost badge in the header updates after each AI turn. If a budget limit is set:
- The badge turns **orange** at 80% of the limit
- When the limit is reached, a dialog appears offering to:
  - Set a new higher limit
  - Conclude the discussion immediately

## Error Recovery

If an AI participant's API call fails (network error, rate limit, etc.), Consensus retries up to 3 times with exponential backoff. If all retries fail, the turn is skipped and the discussion continues with the next participant.
