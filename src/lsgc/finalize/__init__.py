"""Profile-driven postgame completion and verified final score.

Runtime algorithms receive a frame plus a FinalizationProfile. They do not
encode a particular video game's postgame layout. Layout, anchors, and score
regions are calibration data bound to one channel/profile.
"""

from lsgc.finalize.detector import (
    CompletionDetector,
    CompletionPresence,
    detect_completion,
)
from lsgc.finalize.errors import FinalizationError, FinalizationValidationError
from lsgc.finalize.policy import FinalizationPolicy
from lsgc.finalize.profile import SCHEMA_VERSION, FinalizationProfile
from lsgc.finalize.reader import read_final_score
from lsgc.finalize.reconcile import (
    ReconciliationDecision,
    ReconciliationKind,
    reconcile_final_score,
)
from lsgc.finalize.recovery import (
    CompositeRecoverySource,
    FrameSequenceSource,
    MemoryFrameWindow,
    RecoveryCoverage,
    RecoveryWindow,
)
from lsgc.finalize.scanner import RecoveredTransition, RecoveryScanResult, scan_recovered_scores
from lsgc.finalize.storage import (
    load_finalization_profile,
    load_finalization_profile_unchecked,
    profile_from_dict,
    profile_to_dict,
    save_finalization_profile,
)
from lsgc.finalize.temporal import (
    CompletionEvidence,
    CompletionConfirmer,
    PostgameScoreConfirmer,
)

__all__ = [
    "SCHEMA_VERSION",
    "CompletionConfirmer",
    "CompletionDetector",
    "CompletionEvidence",
    "CompletionPresence",
    "CompositeRecoverySource",
    "FinalizationError",
    "FinalizationPolicy",
    "FinalizationProfile",
    "FinalizationValidationError",
    "FrameSequenceSource",
    "MemoryFrameWindow",
    "PostgameScoreConfirmer",
    "RecoveredTransition",
    "ReconciliationDecision",
    "ReconciliationKind",
    "RecoveryCoverage",
    "RecoveryScanResult",
    "RecoveryWindow",
    "detect_completion",
    "load_finalization_profile",
    "load_finalization_profile_unchecked",
    "profile_from_dict",
    "profile_to_dict",
    "read_final_score",
    "reconcile_final_score",
    "save_finalization_profile",
    "scan_recovered_scores",
]
