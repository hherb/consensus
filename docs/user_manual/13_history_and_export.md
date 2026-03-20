# Chapter 13: History and Export

The **History** tab lets you review, resume, and export past discussions.

## Viewing History

The history list shows all past discussions with:
- **Topic** — The discussion question/topic
- **Date** — When the discussion was created
- **Status** — Active, Paused, or Concluded

## Resuming Discussions

### Active or Paused Discussions

Click **Resume** (or **View**) on any active or paused discussion to return to the live discussion view. The discussion continues exactly where you left off.

### Concluded Discussions

Click **Resume** on a concluded discussion to reopen it. The moderator generates a new synthesis acknowledging the continuation, and participants can take additional turns. This is useful when new information emerges or the group needs to revisit a conclusion.

You can also type a message in the chat area of a concluded discussion to continue informally — see [Chapter 7](07_human_participation.md).

## Exporting Discussions

Click the **Export** dropdown on any discussion (in the history list or in the discussion header) and choose a format:

### JSON Export

A structured data file containing:
- Discussion metadata (ID, topic, status, turn number, timestamps)
- All participants with name, type, model, avatar colour
- All messages with speaker, role, content, timestamp, AI metadata (model, tokens, latency, cost), and tool call records
- The complete storyboard (moderator summaries)

Best for: programmatic analysis, importing into other tools, data archiving.

### HTML Export

A self-contained HTML file with embedded CSS that can be opened in any browser:
- Light and dark mode support (follows system preference)
- Print-optimised CSS for saving as PDF via the browser's print dialog
- Collapsible tool call details
- Full message thread with participant styling and colour-coded avatars
- Cost and metadata information

Best for: sharing with others, creating a readable archive, printing.

### PDF Export

- In **desktop mode**: Replaces the page with the export HTML and shows a print toolbar
- In **web mode**: Opens the HTML export in a new browser tab for printing via the browser's print dialog

Best for: formal documentation, reports, offline archiving.

## Deleting Discussions

- Select discussions using the checkboxes in the history list
- Use **Select All** to check all discussions
- Click **"Delete selected"** to soft-delete

Deleted discussions enter a 7-day recovery window. An **Undo** toast notification appears for 6 seconds after deletion, allowing immediate recovery.
