# Chapter 15: MCP Integration

MCP (Model Context Protocol) is a standard for connecting AI models to external tools and data sources. Consensus integrates with MCP in two ways: as a **client** (connecting to external MCP servers) and as a **server** (exposing its capabilities to external AI agents).

## Consensus as MCP Client

### Configuring MCP Servers

Navigate to the **Providers** tab and scroll to the **MCP Servers** section.

Click **"+ Add MCP Server"** and choose a transport:

#### Stdio Transport (Local Process)

For MCP servers that run as local processes:

| Field | Description | Example |
|-------|-------------|---------|
| **Name** | A friendly name | "File System Tools" |
| **Description** | What this server provides | "Access to local file system" |
| **Command** | The executable to run | `npx`, `python`, `node` |
| **Arguments** | Command arguments (comma-separated) | `-y, @modelcontextprotocol/server-filesystem, /path` |
| **Environment Variables** | KEY=VALUE pairs (one per line) | `HOME=/Users/me` |

#### HTTP Transport (Remote Server)

For MCP servers accessible over the network:

| Field | Description | Example |
|-------|-------------|---------|
| **Name** | A friendly name | "Remote Research Tools" |
| **Description** | What this server provides | "Academic paper search" |
| **Server URL** | The HTTP endpoint | `https://mcp.example.com/sse` |
| **HTTP Headers** | KEY=VALUE pairs (one per line) | `Authorization=Bearer token123` |

### MCP Configuration Files

MCP servers can also be loaded from configuration files. Consensus checks these locations:

1. Path specified in `CONSENSUS_MCP_CONFIG` environment variable
2. `./mcp_servers.json` in the current directory
3. `~/.consensus/` directory
4. Platform data directory

### Expert Entities

Expert entities are a special integration of MCP. An expert is an AI entity backed by an MCP server — it has access to the server's tools and can be consulted during discussions.

To create an expert:
1. Configure an MCP server (as above)
2. Create an entity profile of type **Expert**
3. Link the entity to the MCP server

During discussions, experts can be consulted in two ways:
- AI participants can invoke experts through tool calls
- Human users can click the **"Consult Expert"** button in the chat

Experts process queries using their MCP tools and return a response, then step back from the discussion.

## Consensus as MCP Server

The `consensus-mcp` entry point exposes Consensus as an MCP server over stdio, allowing external AI agents (like Claude Code) to drive discussions programmatically.

```bash
consensus-mcp
```

### Available Tools

**Read-only tools:**

| Tool | Description |
|------|-------------|
| `list_discussions` | List discussions, optionally filtered by status |
| `read_discussion` | Get a full discussion transcript with messages and storyboard |
| `list_entities` | List all saved entity profiles |
| `list_documents` | List all documents in the library |
| `read_document` | Read a document's full text or a specific section |
| `search_discussions` | Semantic search across all past discussion messages |
| `search_memories` | Search an entity's persistent memories |
| `search_documents` | Semantic search over all documents |
| `query_knowledge_graph` | Search or traverse the knowledge graph |

**Write tools:**

| Tool | Description |
|------|-------------|
| `store_memory` | Save a persistent memory for the agent |
| `delete_memory` | Delete a memory (own memories only) |
| `assert_knowledge` | Add a knowledge graph triple |
| `run_discussion` | Run a full automated discussion and return the conclusion |

### Using with Claude Code

Add the Consensus MCP server to your Claude Code configuration:

```json
{
  "mcpServers": {
    "consensus": {
      "command": "consensus-mcp",
      "args": []
    }
  }
}
```

Claude Code can then run discussions, search past conversations, and use the knowledge graph as part of its workflow.
