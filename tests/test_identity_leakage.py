"""Production identity code must not import validation manifests or video ids."""

from __future__ import annotations

import ast
from pathlib import Path

IDENTITY_DIR = Path(__file__).resolve().parents[1] / "src" / "lsgc" / "identity"

FORBIDDEN = (
    "hBblAZD-ApY",
    "G2kMfIDFNIQ",
    "A8Qd1dKHKUE",
    "wDbkLLJT4i8",
    "UITXyHeI5KE",
    "m10_identity_validation_set",
    "m5_validation_set",
    "m3_validation_set",
    "expected_team",
    "playlist_position",
)


def test_identity_package_has_no_video_or_manifest_mappings() -> None:
    for path in sorted(IDENTITY_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tests"):
                raise AssertionError(f"{path} imports tests: {node.module}")
        for needle in FORBIDDEN:
            assert needle not in text, f"{path.name} contains {needle!r}"
