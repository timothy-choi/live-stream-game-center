"""Canonical catalog, text aliases, title-disagreement helper. Offline."""

from __future__ import annotations

import pytest

from lsgc.identity.catalog import (
    DEFAULT_CATALOG,
    NFL_TEAMS,
    mentioned_team_ids,
    normalize_identity_text,
    title_visual_disagreement,
)
from lsgc.identity.text import resolve_normalized_text
from lsgc.identity.models import IdentityMethod, UnreadableIdentityReason


class TestCatalog:
    def test_thirty_two_unique_ids(self) -> None:
        ids = [team.team_id for team in NFL_TEAMS]
        assert len(ids) == 32
        assert len(set(ids)) == 32
        assert "SF" in ids and "NYG" in ids and "WAS" in ids

    def test_display_name_is_not_primary_key(self) -> None:
        team = DEFAULT_CATALOG.require("SF")
        assert team.team_id == "SF"
        assert team.display_name != team.team_id

    def test_exact_aliases(self) -> None:
        assert DEFAULT_CATALOG.resolve_text("49ers") == "SF"
        assert DEFAULT_CATALOG.resolve_text("nYg") == "NYG"
        assert DEFAULT_CATALOG.resolve_text("Green Bay") == "GB"
        assert DEFAULT_CATALOG.resolve_text("LA Chargers") == "LAC"

    def test_no_fuzzy_guess(self) -> None:
        assert DEFAULT_CATALOG.resolve_text("SAN FRANC") is None
        assert DEFAULT_CATALOG.resolve_text("PACKER") is None
        assert DEFAULT_CATALOG.resolve_text("XXX") is None

    def test_normalize_collapses_punctuation(self) -> None:
        assert normalize_identity_text("  san-francisco  ") == "SAN FRANCISCO"


class TestTextResolver:
    def test_known_abbreviation(self) -> None:
        obs = resolve_normalized_text("SF")
        assert obs.readable and obs.team_id == "SF"
        assert obs.method is IdentityMethod.TEXT_ABBREVIATION

    def test_unknown_is_not_nearest_team(self) -> None:
        obs = resolve_normalized_text("SFF")
        assert not obs.readable
        assert obs.team_id is None
        assert obs.reason is UnreadableIdentityReason.AMBIGUOUS

    def test_empty(self) -> None:
        obs = resolve_normalized_text("   ")
        assert not obs.readable
        assert obs.reason is UnreadableIdentityReason.EMPTY_CROP


class TestTitleMetadataBoundary:
    def test_agreeing_title_is_not_required(self) -> None:
        assert title_visual_disagreement("49ers @ Packers Game 8", "SF", "GB") is None

    def test_disagreement_does_not_rewrite_visual(self) -> None:
        msg = title_visual_disagreement("49ers @ Packers Game 8", "SF", "NYG")
        assert msg is not None
        assert "GB" in msg
        assert mentioned_team_ids("Chargers @ 49ers") == {"LAC", "SF"}
