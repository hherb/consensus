# Chapter 7: Human Participation

Humans participate in Consensus discussions alongside AI entities. This chapter covers everything about the human experience in a discussion.

## Joining a Discussion

Add a human profile to the roster during setup, just like any other entity. A human can fill any role:
- **Regular participant** — Speaks in turn order alongside AI participants
- **Moderator** — Manages the discussion flow manually (summaries, mediation, conclusion are typed rather than AI-generated)
- **Devil's Advocate** — Assigned to challenge the group (though this role is typically more effective with AI)

## Speaking During Your Turn

When it's your turn to speak:

1. The turn badge shows your name
2. The text input area at the bottom of the chat becomes active
3. Type your message — Markdown formatting is supported (bold, italic, code, lists, etc.)
4. Click **Send** or press Enter (Shift+Enter for newlines)

Your message appears in the chat thread with your avatar and name. The discussion then proceeds to the next participant.

## Speaking in a Structured Phase

Most of the structured methods (see [Chapter 5](05_discussion_methods.md)) have phases whose result must be machine-readable — a probability distribution, a set of scores, a vote, a hypothesis-versus-evidence rating. In those phases AI participants are required to answer through a fixed schema rather than prose, and so are you.

When your turn falls in such a phase, the free-text box is replaced by a **form generated from that phase's schema**. You get one labelled widget per field — number inputs for probabilities and scores, checkboxes for booleans, text fields for claims, and repeatable groups for lists — with required fields enforced before you can submit. You never have to hand-write JSON.

For deeply nested schemas that can't be laid out as a simple form (the ACH hypothesis-by-evidence matrix, for instance), the form falls back to a **guided JSON textarea** pre-filled with the correct structure, so you only fill in values.

A few things worth knowing:

- **Your input is validated the same way an AI's is.** If a value is the wrong shape or a required field is missing, you get a visible error and the turn is not recorded. Nothing is silently discarded.
- **Partially filled forms survive interruptions.** Pausing, resuming, reopening a concluded discussion, or reloading the page preserves what you have typed.
- **The form reflects the current phase.** As the discussion advances, the fields change to match whatever the new phase asks for.

## Attaching Evidence

Some phases track where each contribution comes from. When you're speaking in one of them, an **Attach evidence** button appears next to the input box. It inserts an `[evidence: …]` marker into your message — fill in the source, such as a URL, citation, or document reference.

Consensus then classifies your turn as **grounded** (a citation is present) or **reasoning-based** (none is), annotates it in the thread, and records it in an evidence log that the moderator draws on when writing the conclusion. Pasting a bare URL into your message counts as grounding too.

This is deliberately soft: contributing without evidence is always allowed and is never rejected — it is simply labelled as reasoning rather than citation. Note also that the classification only checks that a citation is *present*. Consensus does not fetch the source or verify that it supports what you said.

## Responding to AI Questions (Ask User)

AI participants with the "ask user" tool can pause mid-turn to ask you a question. When this happens:

1. An inline input bubble appears in the chat below the AI's partial response
2. The bubble shows the AI's question
3. Type your answer and submit
4. The AI incorporates your response and continues generating

There is a 5-minute timeout on these prompts. If you don't respond in time, the AI proceeds without your input.

## Consulting Experts

During a discussion, you can consult expert entities (if any are configured):

1. Click the **"Consult Expert"** button in the chat area
2. Select an expert from the dropdown
3. Type your query
4. The expert processes your query using its MCP tools and returns a response
5. The expert's response appears in the chat

Expert consultations don't consume a regular turn — they're supplementary.

## Moderator Actions as a Human

If you're the human moderator, you have additional controls:

- **Reassign** — Choose who speaks next instead of following the default order
- **Mediate** — Type a mediation message to redirect or refocus the discussion
- **Conclude** — End the discussion and write the final synthesis yourself

When a human is the moderator, the summaries and interventions are whatever you type rather than AI-generated.

## Participating After Conclusion

After a discussion is concluded, a chat input area remains available. You can type a message to reopen the discussion informally. This triggers the moderator to generate a new synthesis incorporating your continuation.

## Dynamic Participation

Participants can join or leave mid-discussion:

- **Pause** the discussion first
- **Add** new participants from the sidebar (they join the turn rotation)
- **Remove** participants by clicking the remove button on their card in the sidebar
- **Resume** to continue

## Tips for Effective Human Participation

1. **Be specific.** AI participants respond best to concrete, well-defined contributions. Vague input leads to vague responses.

2. **Challenge AI reasoning.** Don't just agree — push back on weak arguments, ask for evidence, point out what's being overlooked.

3. **Provide domain expertise.** You likely have context that the AI models don't. Share relevant facts, constraints, or experiences.

4. **Use the moderator controls.** If the discussion is going in circles, use Reassign to give a specific participant the floor, or Mediate to redirect focus.

5. **Attach documents.** If you have relevant data, papers, or documents, attach them during setup so AI participants can reference them. See [Chapter 9](09_documents_and_images.md).
