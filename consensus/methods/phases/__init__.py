"""Composable phase handlers for discussion methods.

Each handler encapsulates all behavior for one phase: prompts,
response processing, state initialization, and advancement logic.
"""

from .surface_assumptions import SurfaceAssumptionsHandler
from .challenge_assumptions import ChallengeAssumptionsHandler
from .assess_assumptions import AssessAssumptionsHandler
from .frame_premortem import FramePremortemHandler
from .premortem_imagine import PremortemImagineHandler
from .consolidate_premortem import ConsolidatePremortemHandler
from .state_positions import StatePositionsHandler
from .define_criteria import DefineCriteriaHandler
from .present_evidence import PresentEvidenceHandler
from .adjudicate import AdjudicateHandler
from .construct import ConstructHandler
from .attack import AttackHandler
from .revise_red_team import ReviseRedTeamHandler
from .assess_red_team import AssessRedTeamHandler

__all__ = [
    "SurfaceAssumptionsHandler",
    "ChallengeAssumptionsHandler",
    "AssessAssumptionsHandler",
    "FramePremortemHandler",
    "PremortemImagineHandler",
    "ConsolidatePremortemHandler",
    "StatePositionsHandler",
    "DefineCriteriaHandler",
    "PresentEvidenceHandler",
    "AdjudicateHandler",
    "ConstructHandler",
    "AttackHandler",
    "ReviseRedTeamHandler",
    "AssessRedTeamHandler",
]
