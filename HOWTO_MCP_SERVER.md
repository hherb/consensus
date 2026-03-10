# How to Integrate Consensus via MCP Server

This guide walks you through connecting Consensus to an AI coding assistant
using the Model Context Protocol (MCP). We use **Claude Code in VS Code** as the
example, but the same approach works with any MCP-compatible client.

---

## What You Get

Once connected, your AI assistant gains access to 13 tools:

| Category | Tools |
|----------|-------|
| **Browse** | `list_discussions`, `read_discussion`, `list_entities`, `list_documents`, `read_document` |
| **Semantic search** | `search_discussions`, `search_memories`, `search_documents`, `query_knowledge_graph` |
| **Write** | `store_memory`, `delete_memory`, `assert_knowledge`, `run_discussion` |

Your assistant can search past discussions, recall entity memories, query the
knowledge graph, store its own persistent memories across sessions, and even
trigger full AI-moderated discussions programmatically.

---

## Prerequisites

1. **Consensus installed** with its entry point available on `$PATH`:

   ```bash
   cd /path/to/consensus
   uv pip install -e ".[all]"
   ```

   Verify:

   ```bash
   which consensus-mcp
   # should print something like: /Users/you/.local/bin/consensus-mcp
   ```

2. **At least one AI entity configured** in Consensus (with a working provider
   and API key). The MCP server reuses your existing Consensus database — it
   needs configured entities to run discussions.

3. **Ollama running** (optional, for semantic search). If Ollama is not
   available, the list/read tools still work — only the four semantic search
   tools will return graceful error messages.

   ```bash
   ollama serve
   # Make sure you have an embedding model pulled:
   ollama pull nomic-embed-text
   ```

---

## Step 1: Verify the MCP Server Starts

Run it manually first to confirm everything works:

```bash
consensus-mcp
```

The server reads JSON-RPC from stdin and writes to stdout. It will sit waiting
for input — that is correct. Press `Ctrl-C` to exit.

If you see import errors, make sure Consensus is installed in the same Python
environment that `consensus-mcp` resolves to.

---

## Step 2: Configure Claude Code in VS Code

Claude Code discovers MCP servers from a JSON configuration file. You have two
options for where to place it:

### Option A: Project-level (recommended for team use)

Create `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "consensus": {
      "command": "consensus-mcp"
    }
  }
}
```

This file can be committed to version control so every team member gets the
integration automatically.

### Option B: User-level (available in all projects)

Edit your Claude Code settings file at `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "consensus": {
      "command": "consensus-mcp"
    }
  }
}
```

### Using an absolute path

If `consensus-mcp` is not on your `$PATH` (e.g. it lives inside a virtual
environment), use the full path:

```json
{
  "mcpServers": {
    "consensus": {
      "command": "/Users/you/.local/bin/consensus-mcp"
    }
  }
}
```

### Using uv to run directly from the source tree

If you prefer not to install globally, you can invoke via `uv run`:

```json
{
  "mcpServers": {
    "consensus": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/consensus", "consensus-mcp"]
    }
  }
}
```

---

## Step 3: Restart Claude Code

After saving the configuration, restart the Claude Code extension:

1. Open the VS Code Command Palette (`Cmd+Shift+P`)
2. Type **"Claude Code: Restart"** and select it

Claude Code will launch the `consensus-mcp` process in the background.

---

## Step 4: Verify the Connection

Ask Claude Code something that would exercise the tools:

> "List my recent Consensus discussions."

Claude should call the `list_discussions` tool and return results from your
Consensus database. If it does not recognise the tools, check that:

- The config file path is correct
- `consensus-mcp` is executable and on the right `$PATH`
- You restarted Claude Code after editing the config

---

## Step 5: Using the Tools

Here are practical examples of what you can ask once connected:

### Browsing discussions

> "Show me the last 5 concluded discussions."

> "Read the full transcript of discussion 12."

### Searching by meaning

> "Search my discussions for anything related to database migration patterns."

> "What does the knowledge graph say about 'error handling'?"

### Using persistent memory

> "Remember that this project uses PostgreSQL 16 in production."

This stores a memory under the "Claude Code Agent" entity — it persists
across sessions.

> "What do you remember about this project's infrastructure?"

This searches the agent's stored memories semantically.

### Running a discussion

> "Run a Consensus discussion about whether we should migrate from REST to
> GraphQL. Use entities 2, 5, and 7."

This creates a real discussion in Consensus with your configured AI entities,
runs it to conclusion, and returns the synthesis. It is subject to a default
$1.00 cost limit (configurable via the `cost_limit` parameter).

### Querying documents

> "Search my Consensus documents for information about authentication flows."

> "List all documents in the Consensus library."

---

## Data Location

The MCP server reads from and writes to your existing Consensus database:

| Platform | Path |
|----------|------|
| macOS | `~/Library/Application Support/consensus/consensus.db` |
| Linux | `~/.local/share/consensus/consensus.db` |
| Windows | `%APPDATA%/consensus/consensus.db` |

This is the same database used by the Consensus desktop and web apps. Any
discussions, entities, memories, or documents you have there are immediately
accessible via the MCP server.

---

## Troubleshooting

### "Tool not found" or tools not appearing

- Verify `consensus-mcp` runs without errors: `consensus-mcp < /dev/null`
- Check your config JSON is valid (no trailing commas)
- Restart Claude Code after config changes

### Semantic search returns errors

- Ensure Ollama is running: `curl http://localhost:11434/api/tags`
- Pull an embedding model: `ollama pull nomic-embed-text`
- The five list/read tools work without Ollama — only the four search tools
  require it

### "No AI entities available"

The `run_discussion` tool needs at least one AI entity with a configured
provider and API key. Open Consensus (desktop or web) and set up your entities
first.

### Permission or path errors

- The MCP server needs read/write access to the database directory
- If using a virtualenv, make sure the `command` in your config resolves to the
  correct environment

### Checking logs

The MCP server logs to stderr. To see its output, run it manually with input
piped in:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | consensus-mcp
```

You should see a JSON response with server capabilities printed to stdout.

---

## Security Notes

- **Memory isolation**: The agent can only delete its own memories, never those
  of other entities. This is hardcoded and enforced at both the application and
  database layers.
- **Cost limits**: `run_discussion` defaults to a $1.00 cost cap. Set
  `cost_limit` explicitly if you need more.
- **No credentials stored**: The MCP server does not handle API keys directly —
  it uses the providers already configured in your Consensus database (which
  reference environment variables, not raw keys).
- **Read/write scope**: The server has full read access to your Consensus data
  and can create memories, knowledge triples, and discussions. It cannot delete
  discussions, modify entities, or change provider configurations.
