"""Worker applies profile-driven finalization to a sequence of frames."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from lsgc.broadcast.calibration import ChannelCalibration
from lsgc.broadcast.models import WorkerOutcome
from lsgc.broadcast.worker import TrackingWorker
from lsgc.calibration import CalibrationProfile, IdentityPresentation, Region
from lsgc.models import Frame
from lsgc.persist.models import PLATFORM_YOUTUBE, CompletionStatus
from lsgc.track.models import ConfirmedScoreChange, ScoreProvenance
from lsgc.vision import build_reference
from tests.broadcast_fakes import RecordingSessionFactory, absence_pipeline_factory
from tests.finalize_support import layout_a
from tests.persist_memory import InMemoryGameRepository


def _frame(image, index: int, timestamp_s: float) -> Frame:
    height, width = image.shape[:2]
    return Frame(
        index=index,
        image=image,
        width=width,
        height=height,
        timestamp_s=timestamp_s,
        source_url="https://www.youtube.com/watch?v=qxsVHobKdhQ",
        received_at=datetime.now(tz=UTC),
        source_id="qxsVHobKdhQ",
        extra={"source_epoch": 1},
    )


def test_worker_finalizes_after_confirmed_postgame_and_stability() -> None:
    image, final_profile, final_reference, glyphs = layout_a(left=35, right=49)
    board = CalibrationProfile(
        profile_id="board",
        channel_id=final_profile.channel_id,
        revision=1,
        reference_width=1280,
        reference_height=720,
        scoreboard=Region(300, 60, 600, 80),
        left_score=Region(400, 80, 40, 30),
        right_score=Region(760, 80, 40, 30),
        left_identity=Region(320, 70, 60, 60),
        right_identity=Region(820, 70, 60, 60),
        left_identity_presentation=IdentityPresentation.LOGO,
        right_identity_presentation=IdentityPresentation.LOGO,
        stable_anchors=(Region(500, 70, 20, 40), Region(620, 70, 20, 40)),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    board_image = np.full((720, 1280, 3), 30, dtype=np.uint8)
    board_image[60:140, 300:900] = (70, 60, 50)
    board_ref = build_reference([board_image], board)

    store = InMemoryGameRepository()
    game = store.create_or_get_game(
        platform=PLATFORM_YOUTUBE,
        external_broadcast_id="qxsVHobKdhQ",
        channel_id=final_profile.channel_id,
    )
    store.persist_confirmation(
        game.id,
        ConfirmedScoreChange(
            revision=1,
            previous_left=None,
            previous_right=None,
            new_left=35,
            new_right=49,
            evidence_count=2,
            first_seen_timestamp_s=0.0,
            first_seen_frame_index=0,
            confirmed_timestamp_s=2.0,
            confirmed_frame_index=1,
            confirmed_epoch=1,
            initialization=True,
            source=ScoreProvenance.GAMEPLAY_SCOREBOARD,
        ),
    )
    calibration = ChannelCalibration(
        profile=board,
        reference=board_ref,
        glyphs=glyphs,
        finalization=final_profile,
        final_reference=final_reference,
    )
    frames = (
        _frame(image, 0, 100.0),
        _frame(image, 1, 102.0),
        _frame(image, 2, 104.0),
        _frame(image, 3, 112.0),
    )
    worker = TrackingWorker(
        store.get_game(game.id),
        calibration=calibration,
        store=store,
        session_factory=RecordingSessionFactory(frames=frames),
        pipeline_factory=absence_pipeline_factory,
        identity_pipeline_factory=lambda _c: None,
    )
    assert worker.run() is WorkerOutcome.ACQUISITION_ENDED
    final = store.get_game(game.id)
    assert final.completion_status is CompletionStatus.FINAL
    assert final.pair == (35, 49)
    assert final.final_score_verified
