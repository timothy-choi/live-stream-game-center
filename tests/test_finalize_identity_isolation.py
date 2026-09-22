"""Identity / M10 isolation for M12.5B."""

from __future__ import annotations

from pathlib import Path


def test_finalize_does_not_import_identity() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "lsgc" / "finalize"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "lsgc.identity" not in text
        assert "gemini" not in text.lower()
        assert "Seahawks" not in text
