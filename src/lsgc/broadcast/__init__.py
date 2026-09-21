"""YouTube broadcast discovery and tracking-session orchestration (M9).

Control plane: YouTube Data API v3 metadata.
Data plane: M4 LiveFrameSession (yt-dlp + FFmpeg).

YouTube ENDED / acquisition ENDED is not GAME FINAL.
"""

from lsgc.broadcast.calibration import (
    CalibrationLookup,
    ChannelCalibration,
    FilesystemCalibrationLookup,
    MappingCalibrationLookup,
)
from lsgc.broadcast.config import (
    BroadcastRuntimeConfig,
    load_connected_channels,
    load_runtime_config,
)
from lsgc.broadcast.eligibility import evaluate_eligibility
from lsgc.broadcast.errors import (
    BroadcastConfigError,
    BroadcastError,
    CalibrationNotReadyError,
    YouTubeApiError,
)
from lsgc.broadcast.models import (
    PLATFORM_YOUTUBE,
    ConnectedChannel,
    DiscoveredBroadcast,
    EligibilityDecision,
    EligibilityReason,
    OrchestratorStatus,
    SyncReport,
    WorkerOutcome,
    WorkerSnapshot,
    WorkerState,
    youtube_watch_url,
)
from lsgc.broadcast.orchestrator import BroadcastOrchestrator, orchestrator_from_config
from lsgc.broadcast.supervisor import TrackingSupervisor
from lsgc.broadcast.sync import ChannelSynchronizer
from lsgc.broadcast.youtube import YouTubeDataApiClient
from lsgc.persist.models import PlatformLifecycle

__all__ = [
    "PLATFORM_YOUTUBE",
    "BroadcastConfigError",
    "BroadcastError",
    "BroadcastOrchestrator",
    "BroadcastRuntimeConfig",
    "CalibrationLookup",
    "CalibrationNotReadyError",
    "ChannelCalibration",
    "ChannelSynchronizer",
    "ConnectedChannel",
    "DiscoveredBroadcast",
    "EligibilityDecision",
    "EligibilityReason",
    "FilesystemCalibrationLookup",
    "MappingCalibrationLookup",
    "OrchestratorStatus",
    "PlatformLifecycle",
    "SyncReport",
    "TrackingSupervisor",
    "WorkerOutcome",
    "WorkerSnapshot",
    "WorkerState",
    "YouTubeApiError",
    "YouTubeDataApiClient",
    "evaluate_eligibility",
    "load_connected_channels",
    "load_runtime_config",
    "orchestrator_from_config",
    "youtube_watch_url",
]
