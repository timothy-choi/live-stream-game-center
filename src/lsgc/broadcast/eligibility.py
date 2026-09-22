"""Explicit tracking eligibility. Missing calibration is NOT_READY, not a substitute."""

from __future__ import annotations

from lsgc.broadcast.calibration import CalibrationLookup
from lsgc.broadcast.models import EligibilityDecision, EligibilityReason, PlatformLifecycle
from lsgc.persist.models import CompletionStatus, Game


def evaluate_eligibility(
    game: Game,
    *,
    expected_channel_id: str,
    calibration: CalibrationLookup,
) -> EligibilityDecision:
    if not game.channel_id or game.channel_id != expected_channel_id:
        return EligibilityDecision(
            eligible=False,
            reason=EligibilityReason.WRONG_CHANNEL,
            detail="broadcast does not belong to the configured channel",
        )
    if game.completion_status is CompletionStatus.FINAL:
        return EligibilityDecision(
            eligible=False,
            reason=EligibilityReason.ALREADY_FINAL,
            detail="verified final score already recorded",
        )
    if not game.tracking_enabled:
        return EligibilityDecision(
            eligible=False,
            reason=EligibilityReason.TRACKING_DISABLED,
            detail="tracking_enabled is false",
        )
    bundle = calibration.get(expected_channel_id)
    if bundle is None:
        return EligibilityDecision(
            eligible=False,
            reason=EligibilityReason.MISSING_CALIBRATION,
            detail="NOT_READY: no calibration for this channel_id",
        )
    if bundle.profile.channel_id != expected_channel_id:
        return EligibilityDecision(
            eligible=False,
            reason=EligibilityReason.MISSING_CALIBRATION,
            detail="NOT_READY: calibration channel_id does not match broadcast",
        )
    if game.platform_lifecycle is not PlatformLifecycle.LIVE:
        if game.completion_status is CompletionStatus.FINALIZING:
            return EligibilityDecision(
                eligible=True,
                reason=EligibilityReason.ELIGIBLE,
                detail="finalizing drain continues after platform ENDED",
            )
        return EligibilityDecision(
            eligible=False,
            reason=EligibilityReason.NOT_LIVE,
            detail=f"platform lifecycle is {game.platform_lifecycle.value}",
        )
    return EligibilityDecision(eligible=True, reason=EligibilityReason.ELIGIBLE)
