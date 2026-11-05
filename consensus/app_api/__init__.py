"""Domain mixins composed by ``ConsensusApp`` (issue #61).

Each mixin holds one group of public methods moved verbatim out of
``consensus/app.py``; ``ConsensusApp`` owns the state they use.
"""

from .mcp_experts import MCPExpertsMixin
from .catalog import CatalogMixin
from .discussion import DiscussionMixin
from .history import HistoryMixin
from .tool_assignments import ToolAssignmentsMixin
from .documents import DocumentsMixin
from .images import ImagesMixin

__all__ = [
    "MCPExpertsMixin",
    "CatalogMixin",
    "DiscussionMixin",
    "HistoryMixin",
    "ToolAssignmentsMixin",
    "DocumentsMixin",
    "ImagesMixin",
]
