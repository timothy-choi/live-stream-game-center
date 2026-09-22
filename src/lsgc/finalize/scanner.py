"""Rescan recovered frames with the live M3 + M6 + M7 machinery.

Confirmed recovered states are numeric left/right pairs that were actually
visible. A point difference is only a hint that live tracking missed a
scoreboard state. It is never mapped onto sport-specific scoring events.
"""

from __future__ import annotations

from dataclasses import dataclass

from lsgc.finalize.recovery import RecoveryCoverage, RecoveryWindow
from lsgc.score.pipeline import ScoreObservationPipeline
from lsgc.track.models import ConfirmedScoreChange, ScoreProvenance, TrustedScoreState
from lsgc.track.policy import ConfirmationPolicy
from lsgc.track.validator import TemporalScoreValidator


@dataclass(frozen=True)
class RecoveredTransition:
    pair: tuple[int, int]
    previous: tuple[int, int] | None
    evidence_count: int
    first_seen_timestamp_s: float | None
    first_seen_frame_index: int | None
    confirmed_timestamp_s: float | None
    confirmed_frame_index: int | None
    confirmed_epoch: int | None


@dataclass(frozen=True)
class RecoveryScanResult:
    transitions: tuple[RecoveredTransition, ...]
    coverage: RecoveryCoverage
    conflict: bool
    detail: str


def scan_recovered_scores(
    window: RecoveryWindow,
    pipeline: ScoreObservationPipeline,
    *,
    durable_pair: tuple[int, int] | None,
    known_pairs: set[tuple[int, int]],
    policy: ConfirmationPolicy,
) -> RecoveryScanResult:
    """Walk available frames and confirm new left/right pairs temporally.

    Duplicate pairs already in durable history are ignored. A temporally
    confirmed decrease relative to the walking terminal is conflict, not a
    rewrite. A single readable frame cannot produce a transition.
    """
    validator = TemporalScoreValidator(policy)
    if durable_pair is not None:
        seed = ConfirmedScoreChange(
            revision=1,
            previous_left=None,
            previous_right=None,
            new_left=durable_pair[0],
            new_right=durable_pair[1],
            evidence_count=2,
            first_seen_timestamp_s=window.requested_start_s,
            first_seen_frame_index=None,
            confirmed_timestamp_s=window.requested_start_s,
            confirmed_frame_index=None,
            confirmed_epoch=None,
            initialization=True,
        )
        validator.restore(
            state=TrustedScoreState(
                left_score=durable_pair[0],
                right_score=durable_pair[1],
                initialized=True,
                revision=1,
                last_confirmed_timestamp_s=window.requested_start_s,
            ),
            history=(seed,),
        )
    known = set(known_pairs)
    current = durable_pair
    found: list[RecoveredTransition] = []
    for frame in window.frames:
        observation = pipeline.observe_frame(frame)
        if observation is None or not observation.both_readable:
            continue
        pair = (int(observation.left.value), int(observation.right.value))  # type: ignore[arg-type]
        if current is not None and pair == current:
            validator.apply(observation)
            continue
        if pair in known:
            continue
        result = validator.apply(observation)
        if result.change is None:
            continue
        confirmed = result.change.new_pair
        if confirmed in known or (current is not None and confirmed == current):
            continue
        if current is not None and (
            confirmed[0] < current[0] or confirmed[1] < current[1]
        ):
            return RecoveryScanResult(
                transitions=tuple(found),
                coverage=window.coverage,
                conflict=True,
                detail=(
                    f"recovered pair {confirmed[0]}-{confirmed[1]} decreases "
                    f"terminal {current[0]}-{current[1]}"
                ),
            )
        found.append(
            RecoveredTransition(
                pair=confirmed,
                previous=current,
                evidence_count=result.change.evidence_count,
                first_seen_timestamp_s=result.change.first_seen_timestamp_s,
                first_seen_frame_index=result.change.first_seen_frame_index,
                confirmed_timestamp_s=result.change.confirmed_timestamp_s,
                confirmed_frame_index=result.change.confirmed_frame_index,
                confirmed_epoch=result.change.confirmed_epoch,
            )
        )
        known.add(confirmed)
        current = confirmed
    return RecoveryScanResult(
        transitions=tuple(found),
        coverage=window.coverage,
        conflict=False,
        detail="recovered visible scoreboard states from available video",
    )


def recovered_change(
    game_revision: int,
    transition: RecoveredTransition,
    *,
    initialization: bool,
) -> ConfirmedScoreChange:
    previous = transition.previous
    return ConfirmedScoreChange(
        revision=game_revision,
        previous_left=None if previous is None else previous[0],
        previous_right=None if previous is None else previous[1],
        new_left=transition.pair[0],
        new_right=transition.pair[1],
        evidence_count=max(transition.evidence_count, 2),
        first_seen_timestamp_s=transition.first_seen_timestamp_s,
        first_seen_frame_index=transition.first_seen_frame_index,
        confirmed_timestamp_s=transition.confirmed_timestamp_s,
        confirmed_frame_index=transition.confirmed_frame_index,
        confirmed_epoch=transition.confirmed_epoch,
        initialization=initialization,
        source=ScoreProvenance.RECOVERED_GAMEPLAY_SCOREBOARD,
    )
