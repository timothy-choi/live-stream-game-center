"""Reconciliation matrix and temporal confirmation."""

from __future__ import annotations

from lsgc.finalize.policy import FinalizationPolicy
from lsgc.finalize.reconcile import ReconciliationKind, reconcile_final_score
from lsgc.finalize.temporal import CompletionConfirmer, CompletionEvidence


def test_case_a_agree() -> None:
    decision = reconcile_final_score((35, 49), (35, 49))
    assert decision.kind is ReconciliationKind.AGREE


def test_case_b_forward_update() -> None:
    decision = reconcile_final_score((35, 42), (35, 49))
    assert decision.kind is ReconciliationKind.UPDATE
    assert decision.accepted_pair == (35, 49)


def test_case_c_establish() -> None:
    decision = reconcile_final_score(None, (35, 49))
    assert decision.kind is ReconciliationKind.ESTABLISH


def test_case_d_does_not_reduce() -> None:
    decision = reconcile_final_score((35, 49), (35, 42))
    assert decision.kind is ReconciliationKind.CONFLICT
    assert decision.accepted_pair is None


def test_one_frame_is_not_completion() -> None:
    confirmer = CompletionConfirmer(FinalizationPolicy())
    first = confirmer.observe(True, timestamp_s=0.0, frame_index=0)
    assert first is CompletionEvidence.COMPLETION_CANDIDATE
    assert not confirmer.confirmed
    second = confirmer.observe(True, timestamp_s=2.0, frame_index=1)
    assert second is CompletionEvidence.COMPLETION_CONFIRMED


def test_gap_resets_completion_candidate() -> None:
    confirmer = CompletionConfirmer(FinalizationPolicy())
    confirmer.observe(True, timestamp_s=0.0, frame_index=0)
    reset = confirmer.observe(False, timestamp_s=2.0, frame_index=1)
    assert reset is CompletionEvidence.NO_COMPLETION_EVIDENCE
    again = confirmer.observe(True, timestamp_s=4.0, frame_index=2)
    assert again is CompletionEvidence.COMPLETION_CANDIDATE
