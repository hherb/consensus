"""Tunable limits for the institutional memory tools."""

EMBED_TIMEOUT = 10.0
EMBED_MAX_RETRIES = 3
EMBED_BASE_DELAY = 1.0
SEARCH_DEFAULT_LIMIT = 5
# Characters of a message sent to the embedder when indexing it.
MESSAGE_EMBED_MAX_CHARS = 1000
# Characters of each matching message shown in a discussion_search result.
SEARCH_SNIPPET_CHARS = 300
