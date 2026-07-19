# Chapter 4: Setting Up a Discussion

The **New Discussion** tab is where you define what to discuss, who participates, and how the discussion should be structured.

## The Topic

Enter your discussion topic or question in the text area at the top. This can be anything from a simple question to a complex multi-faceted problem:

- *"What are the most promising applications of gene therapy in oncology?"*
- *"Should our startup pivot from B2C to B2B?"*
- *"Evaluate the evidence for and against universal basic income."*

The topic text is shown to all participants and used by the moderator to guide the discussion.

## Building the Roster

### Adding Participants

The left side of the setup shows your saved profiles. By default, the 6 most recently used profiles appear. Use the search box to find others, or click **"+ Create New Profile"** to make a new one on the spot.

Click a profile to add it to the **Discussion Roster** on the right.

### Minimum Requirements

A discussion needs:
- **At least 2 participants** (any combination of human and AI)
- **Exactly 1 moderator** (assigned from the roster)

The **Start Discussion** button remains disabled until these conditions are met.

### Assigning Roles

Each entity in the roster has controls:

| Control | What it does |
|---------|-------------|
| **Set Moderator** | Designates this entity as the discussion moderator |
| **Set Devil's Advocate** | (AI only) Gives this entity a specialised prompt to challenge assumptions and identify weaknesses |
| **Remove** | Removes the entity from the roster |
| **Context Strategy** | Per-entity override for how much history this participant sees (see [Chapter 11](11_context_strategies.md)) |

## Discussion Settings

The settings card below the roster controls the discussion parameters:

### Moderator Participation

**"Moderator also participates in discussion turns"** — When checked, the moderator speaks during regular turns in addition to providing summaries and managing flow. When unchecked (the default), the moderator only intervenes for summaries, phase transitions, and mediation.

### Max Rounds

The maximum number of complete rounds through all participants. Set to **0** for unlimited rounds. The discussion can always be concluded manually regardless of this setting.

### Budget

A cost limit in dollars, **defaulting to $1.00** (maximum $100). When the total API cost reaches this limit, a dialog appears offering to set a new limit or conclude. Set to **0** for no limit. Because the field is pre-filled rather than empty, a discussion you never adjust will stop at $1.00 — raise it up front for long runs. See [Chapter 16](16_cost_tracking.md) for details.

### Discussion Method

Select an analytical framework from the dropdown. Each method structures the discussion into phases with specific goals. The default is **Open Discussion** (free-form round-robin).

When you select a method, a description appears below the dropdown explaining how it works. See [Chapter 5](05_discussion_methods.md) for details on all 18 methods.

### Method Recommendation

Not sure which method to use? The **method recommendation panel** helps:

1. Select the type of answer you're looking for (explore perspectives, make a decision, forecast, identify risks, test a hypothesis, resolve a disagreement, or other)
2. Click **"Suggest Method"**
3. An LLM analyses your topic and answer type, then returns ranked recommendations with confidence scores and reasoning

Alternatively, select the **Guided Triage** method — it conducts this analysis interactively as the first phase of the discussion itself.

### Context Strategy Defaults

Set the default context strategy and window size for all participants. Individual participants can override these in the roster. See [Chapter 11](11_context_strategies.md) for an explanation of each strategy.

## Reference Materials

### Documents

Attach documents that participants can reference during the discussion:

- **Upload File** — Select a PDF, HTML, or text file from your computer
- **Add URL** — Paste a URL to a web page, PDF, or text document

Documents are ingested, chunked, and made searchable. AI participants with document tools enabled can query them during the discussion. See [Chapter 9](09_documents_and_images.md).

### Images

Attach images for visual reference:

- **Upload Image** — PNG, JPEG, GIF, WebP, or SVG (max 20 MB)
- **Add URL** — Paste a URL to an image

Vision-capable AI models (GPT-4o, Claude 3+, Gemini) receive images directly as visual content. Non-vision models can use the image description tool. See [Chapter 9](09_documents_and_images.md).

## Starting the Discussion

Once you have a topic, at least 2 participants, and a moderator assigned, click **"Start Discussion"**. The interface switches to the live discussion view.

Continue to [Chapter 6: Running a Discussion](06_running_a_discussion.md) to learn about the live interface.
