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
from .hypothesize import HypothesizeHandler
from .gather_evidence import GatherEvidenceHandler
from .evaluate_matrix import EvaluateMatrixHandler
from .analyse_ach import AnalyseACHHandler
from .estimate import EstimateHandler
from .revise_delphi import ReviseDelphiHandler
from .synthesise_delphi import SynthesiseDelphiHandler
from .frame_hypotheses import FrameHypothesesHandler
from .prior_beliefs import PriorBeliefsHandler
from .diffuse_beliefs import DiffuseBeliefsHandler
from .diagnose_beliefs import DiagnoseHandler
from .deliberate import DeliberateHandler
from .vote import VoteHandler
from .tally import TallyHandler
from .decompose import DecomposeHandler
from .analyze_subquestions import AnalyzeSubquestionsHandler
from .integrate_subquestions import IntegrateSubquestionsHandler
from .recompose import RecomposeHandler
from .triage_intake import TriageIntakeHandler
from .triage_recommend import TriageRecommendHandler
from .triage_confirm import TriageConfirmHandler
from .arraignment import ArraignmentHandler
from .opening_statements import OpeningStatementsHandler
from .prosecution_case import ProsecutionCaseHandler
from .defense_case import DefenseCaseHandler
from .closing_arguments import ClosingArgumentsHandler

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
    "HypothesizeHandler",
    "GatherEvidenceHandler",
    "EvaluateMatrixHandler",
    "AnalyseACHHandler",
    "EstimateHandler",
    "ReviseDelphiHandler",
    "SynthesiseDelphiHandler",
    "FrameHypothesesHandler",
    "PriorBeliefsHandler",
    "DiffuseBeliefsHandler",
    "DiagnoseHandler",
    "DeliberateHandler",
    "VoteHandler",
    "TallyHandler",
    "DecomposeHandler",
    "AnalyzeSubquestionsHandler",
    "IntegrateSubquestionsHandler",
    "RecomposeHandler",
    "TriageIntakeHandler",
    "TriageRecommendHandler",
    "TriageConfirmHandler",
    "ArraignmentHandler",
    "OpeningStatementsHandler",
    "ProsecutionCaseHandler",
    "DefenseCaseHandler",
    "ClosingArgumentsHandler",
]
