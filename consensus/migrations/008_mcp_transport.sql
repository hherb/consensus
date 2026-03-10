-- Add transport type and URL to mcp_servers for HTTP+SSE support
ALTER TABLE mcp_servers ADD COLUMN transport TEXT NOT NULL DEFAULT 'stdio';
ALTER TABLE mcp_servers ADD COLUMN url TEXT NOT NULL DEFAULT '';
ALTER TABLE mcp_servers ADD COLUMN headers TEXT NOT NULL DEFAULT '{}';
