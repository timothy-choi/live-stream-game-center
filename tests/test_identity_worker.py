"""TrackingWorker score and identity paths are independent."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from lsgc.broadcast.calibration import ChannelCalibration
from lsgc.broadcast.models import WorkerOutcome
from lsgc.broadcast.worker import TrackingWorker
from lsgc.identity.models import MatchupObservation
from lsgc.persist import PLATFORM_YOUTUBE
from lsgc.score.models import RawScoreObservation
from tests.broadcast_fakes import CHANNEL_A, RecordingSessionFactory, readable_obs
from tests.persist_memory import InMemoryGameRepository
from tests.test_identity_validator import obs as matchup_obs
from tests.test_identity_recognizer import _profile
from lsgc.models import Frame


class ScoreThenBoom:
    def __init__(self) -> None:
        self.calls = 0

    def observe_frame(self, frame: Frame) -> RawScoreObservation:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("score ocr exploded")
        return readable_obs(frame, 14, 7)


class IdentityThenBoom:
    def __init__(self) -> None:
        self.calls = 0

    def observe_frame(self, frame: Frame) -> MatchupObservation:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("logo exploded")
        return matchup_obs("SF", "GB", index=frame.index, ts=frame.timestamp_s or 0.0)


class RecordingIdentity:
    def __init__(self) -> None:
        self.seen: list[int] = []

    def observe_frame(self, frame: Frame) -> MatchupObservation:
        self.seen.append(frame.index)
        return matchup_obs("SF", "GB", index=frame.index, ts=frame.timestamp_s or 0.0)


def _frame(index: int, ts: float) -> Frame:
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    return Frame(
        index=index,
        image=image,
        width=2,
        height=2,
        timestamp_s=ts,
        source_url="https://www.youtube.com/watch?v=VID10",
        received_at=datetime.now(tz=UTC),
        source_id="VID10",
        extra={"source_epoch": 1},
    )


def test_identity_read_failure_does_not_block_score() -> None:
    store = InMemoryGameRepository()
    game = store.create_or_get_game(
        platform=PLATFORM_YOUTUBE, external_broadcast_id="VID10", channel_id=CHANNEL_A
    )
    frames = (_frame(0, 0.0), _frame(1, 2.0), _frame(2, 4.0))
    identity = IdentityThenBoom()
    worker = TrackingWorker(
        game,
        calibration=ChannelCalibration(profile=_profile(), reference=None, glyphs=None),  # type: ignore[arg-type]
        store=store,
        session_factory=RecordingSessionFactory(frames=frames),
        pipeline_factory=lambda _cal: type(
            "P",
            (),
            {"observe_frame": staticmethod(lambda frame: readable_obs(frame, 7, 0))},
        )(),
        identity_pipeline_factory=lambda _cal: identity,
    )
    outcome = worker.run()
    assert outcome is WorkerOutcome.ACQUISITION_ENDED
    assert worker.identity_read_errors == 1
    game = store.get_game(game.id)
    assert game.pair == (7, 0)
    assert game.confirmed_left_team_id == "SF"
    assert game.confirmed_right_team_id == "GB"


def test_score_read_failure_does_not_block_identity() -> None:
    store = InMemoryGameRepository()
    game = store.create_or_get_game(
        platform=PLATFORM_YOUTUBE, external_broadcast_id="VID11", channel_id=CHANNEL_A
    )
    frames = (_frame(0, 0.0), _frame(1, 2.0), _frame(2, 4.0))
    scores = ScoreThenBoom()
    identity = RecordingIdentity()
    worker = TrackingWorker(
        game,
        calibration=ChannelCalibration(profile=_profile(), reference=None, glyphs=None),  # type: ignore[arg-type]
        store=store,
        session_factory=RecordingSessionFactory(frames=frames),
        pipeline_factory=lambda _cal: scores,
        identity_pipeline_factory=lambda _cal: identity,
    )
    outcome = worker.run()
    assert outcome is WorkerOutcome.ACQUISITION_ENDED
    assert worker.score_read_errors == 1
    game = store.get_game(game.id)
    assert identity.seen == [0, 1, 2]
    assert game.confirmed_left_team_id == "SF"
    assert game.pair == (14, 7)
