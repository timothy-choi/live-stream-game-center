"""Compare confirmed postgame digits with the latest durable gameplay score.

Forward reconciliation requires both sides to be >= the durable pair.
A decrease on either side is a conflict, not an automatic correction.
Football scoring rules are not used.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReconciliationKind(str, Enum):
    AGREE = "AGREE"
    UPDATE = "UPDATE"
    ESTABLISH = "ESTABLISH"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class ReconciliationDecision:
    kind: ReconciliationKind
    durable: tuple[int, int] | None
    postgame: tuple[int, int]
    detail: str

    @property
    def accepted_pair(self) -> tuple[int, int] | None:
        if self.kind is ReconciliationKind.CONFLICT:
            return None
        return self.postgame


def reconcile_final_score(
    durable: tuple[int, int] | None,
    postgame: tuple[int, int],
) -> ReconciliationDecision:
    """Mandatory comparison before FINAL.

    CASE A: pairs equal -> AGREE
    CASE B: postgame strictly forward vs durable -> UPDATE
    CASE C: no durable pair -> ESTABLISH
    CASE D: postgame would reduce a side -> CONFLICT
    """
    if durable is None:
        return ReconciliationDecision(
            kind=ReconciliationKind.ESTABLISH,
            durable=None,
            postgame=postgame,
            detail="no durable trusted score; confirmed postgame may establish it",
        )
    if durable == postgame:
        return ReconciliationDecision(
            kind=ReconciliationKind.AGREE,
            durable=durable,
            postgame=postgame,
            detail="durable trusted score already matches confirmed postgame score",
        )
    if postgame[0] >= durable[0] and postgame[1] >= durable[1]:
        return ReconciliationDecision(
            kind=ReconciliationKind.UPDATE,
            durable=durable,
            postgame=postgame,
            detail=(
                f"postgame {postgame[0]}-{postgame[1]} is forward of durable "
                f"{durable[0]}-{durable[1]}"
            ),
        )
    return ReconciliationDecision(
        kind=ReconciliationKind.CONFLICT,
        durable=durable,
        postgame=postgame,
        detail=(
            f"FINAL_SCORE_CONFLICT durable {durable[0]}-{durable[1]} vs "
            f"postgame {postgame[0]}-{postgame[1]}"
        ),
    )
