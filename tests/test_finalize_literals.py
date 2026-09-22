"""Runtime source must not bake current-fixture literals into algorithms."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "lsgc" / "finalize"

FORBIDDEN = (
    "Seahawks",
    "49ers",
    "GAME STATS",
    "YOU WON",
    "CONTINUE",
    "Madden",
    "SEA",
    "TOUCHDOWN",
    "FIELD GOAL",
    "EXTRA POINT",
)


def test_finalize_package_has_no_layout_literals() -> None:
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert token not in text, f"{path.name} contains {token!r}"
        assert "35-49" not in text
