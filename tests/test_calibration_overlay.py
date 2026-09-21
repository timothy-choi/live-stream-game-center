"""Overlay rendering and the authoring CLI. Offline, no manual inspection."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from conftest import M1_PROFILE_PATH

from lsgc.calibration import (
    CalibrationProfile,
    CalibrationValidationError,
    ChannelMismatchError,
    ProfileSchemaError,
    Region,
    render_overlay,
    save_overlay,
)
from lsgc.calibration.cli import EXIT_CALIBRATION, EXIT_OK, EXIT_USAGE, main, parse_region


class TestRenderOverlay:
    def test_returns_an_image_of_the_same_shape_and_dtype(
        self, valid_profile: CalibrationProfile, synthetic_frame: np.ndarray
    ) -> None:
        annotated = render_overlay(synthetic_frame, valid_profile)
        assert annotated.shape == synthetic_frame.shape
        assert annotated.dtype == synthetic_frame.dtype

    def test_does_not_mutate_the_input(
        self, valid_profile: CalibrationProfile, synthetic_frame: np.ndarray
    ) -> None:
        before = synthetic_frame.copy()
        render_overlay(synthetic_frame, valid_profile)
        assert np.array_equal(synthetic_frame, before)

    def test_draws_on_every_region_boundary(
        self, valid_profile: CalibrationProfile, synthetic_frame: np.ndarray
    ) -> None:
        annotated = render_overlay(synthetic_frame, valid_profile)
        regions = [
            valid_profile.scoreboard,
            valid_profile.left_score,
            valid_profile.right_score,
            valid_profile.left_identity,
            valid_profile.right_identity,
            *valid_profile.stable_anchors,
            *valid_profile.dynamic_ignore,
        ]
        for region in regions:
            top_edge = annotated[region.y, region.x : region.right]
            original = synthetic_frame[region.y, region.x : region.right]
            assert not np.array_equal(top_edge, original), f"nothing drawn for {region}"

    def test_leaves_untouched_areas_alone(
        self, valid_profile: CalibrationProfile, synthetic_frame: np.ndarray
    ) -> None:
        annotated = render_overlay(synthetic_frame, valid_profile)
        # Far below every region and its labels.
        assert np.array_equal(annotated[400:700, :], synthetic_frame[400:700, :])

    def test_region_kinds_use_distinct_colours(
        self, valid_profile: CalibrationProfile, synthetic_frame: np.ndarray
    ) -> None:
        """So a developer can tell an anchor from an ignore region at a glance."""
        annotated = render_overlay(synthetic_frame, valid_profile)

        def edge_colour(region: Region) -> tuple[int, ...]:
            return tuple(annotated[region.y, region.x + region.width // 2])

        board = edge_colour(valid_profile.scoreboard)
        score = edge_colour(valid_profile.left_score)
        anchor = edge_colour(valid_profile.stable_anchors[0])
        ignore = edge_colour(valid_profile.dynamic_ignore[0])
        assert len({board, score, anchor, ignore}) == 4

    def test_rescales_to_a_larger_frame_of_the_same_aspect(
        self, valid_profile: CalibrationProfile
    ) -> None:
        big = np.zeros((1080, 1920, 3), dtype=np.uint8)
        annotated = render_overlay(big, valid_profile)
        assert annotated.shape == big.shape
        scaled = valid_profile.for_frame_size(1920, 1080)
        row = annotated[scaled.scoreboard.y, scaled.scoreboard.x : scaled.scoreboard.right]
        assert row.any()

    def test_rejects_a_mismatched_aspect_ratio(
        self, valid_profile: CalibrationProfile
    ) -> None:
        square = np.zeros((720, 720, 3), dtype=np.uint8)
        with pytest.raises(CalibrationValidationError, match="aspect ratio"):
            render_overlay(square, valid_profile)

    def test_rejects_a_non_bgr_image(self, valid_profile: CalibrationProfile) -> None:
        with pytest.raises(CalibrationValidationError, match="HxWx3"):
            render_overlay(np.zeros((720, 1280), dtype=np.uint8), valid_profile)

    def test_labels_stay_inside_the_image(
        self, valid_profile: CalibrationProfile
    ) -> None:
        """A region flush against the top must not push its label off-frame."""
        from dataclasses import replace

        edge_profile = replace(
            valid_profile,
            scoreboard=Region(0, 0, 600, 80),
            left_score=Region(10, 0, 40, 30),
            right_score=Region(400, 0, 40, 30),
            left_identity=Region(60, 0, 60, 60),
            right_identity=Region(460, 0, 60, 60),
            stable_anchors=(Region(200, 0, 20, 40),),
            dynamic_ignore=(Region(300, 0, 50, 20),),
        )
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        assert render_overlay(frame, edge_profile).shape == frame.shape


class TestSaveOverlay:
    def test_writes_a_readable_png(
        self, valid_profile: CalibrationProfile, synthetic_frame: np.ndarray, tmp_path: Path
    ) -> None:
        import cv2

        path = save_overlay(synthetic_frame, valid_profile, tmp_path / "out" / "overlay.png")
        assert path.exists()
        assert cv2.imread(str(path)).shape == synthetic_frame.shape


class TestRegionArgumentParsing:
    def test_parses_x_y_width_height(self) -> None:
        assert parse_region("357,65,576,65") == Region(357, 65, 576, 65)

    def test_tolerates_spaces(self) -> None:
        assert parse_region(" 1, 2 , 3,4 ") == Region(1, 2, 3, 4)

    @pytest.mark.parametrize("bad", ["1,2,3", "1,2,3,4,5", "a,b,c,d", "1,2,0,4", "1,2,-3,4"])
    def test_rejects_malformed_values(self, bad: str) -> None:
        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            parse_region(bad)


class TestAuthoringCli:
    ARGS = [
        "create",
        "--channel-id", "UC_cli_channel",
        "--profile-id", "cli-test",
        "--reference-size", "1280x720",
        "--scoreboard", "300,60,600,80",
        "--left-score", "400,80,40,30",
        "--right-score", "760,80,40,30",
        "--left-identity", "320,70,60,60",
        "--right-identity", "820,70,60,60",
        "--left-identity-presentation", "logo",
        "--right-identity-presentation", "name,abbreviation",
        "--anchor", "500,70,20,40",
        "--ignore", "600,80,50,20",
    ]

    def test_creates_validates_summarises_and_writes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "profile.json"
        assert main([*self.ARGS, "--out", str(out)]) == EXIT_OK

        printed = capsys.readouterr().out
        assert "UC_cli_channel" in printed
        assert "VALID" in printed

        from lsgc.calibration import load_profile

        profile = load_profile(out, expect_channel="UC_cli_channel")
        assert profile.revision == 1
        assert profile.right_identity_presentation.to_names() == ["abbreviation", "name"]

    def test_invalid_geometry_fails_with_a_calibration_exit_code(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = list(self.ARGS)
        args[args.index("--left-score") + 1] = "10,400,40,30"  # outside the board
        code = main([*args, "--out", str(tmp_path / "bad.json")])
        assert code == EXIT_CALIBRATION
        assert "not inside scoreboard" in capsys.readouterr().err
        assert not (tmp_path / "bad.json").exists()

    def test_missing_anchor_is_rejected(self, tmp_path: Path) -> None:
        args = [a for a in self.ARGS if a not in ("--anchor", "500,70,20,40")]
        assert main([*args, "--out", str(tmp_path / "x.json")]) == EXIT_CALIBRATION

    def test_show_prints_a_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["show", str(M1_PROFILE_PATH)]) == EXIT_OK
        assert "madden27-arcade-prismlive" in capsys.readouterr().out

    def test_show_can_enforce_the_channel(self) -> None:
        assert main(
            ["show", str(M1_PROFILE_PATH), "--expect-channel", "UCz3q0f-dMPNIyQhntPTE7gQ"]
        ) == EXIT_OK

    def test_show_rejects_the_wrong_channel(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["show", str(M1_PROFILE_PATH), "--expect-channel", "UC_other"]) == (
            EXIT_CALIBRATION
        )
        assert "calibrated for channel" in capsys.readouterr().err

    def test_overlay_writes_an_annotated_image(
        self, tmp_path: Path, synthetic_frame: np.ndarray
    ) -> None:
        import cv2

        source = tmp_path / "frame.png"
        cv2.imwrite(str(source), synthetic_frame)
        out = tmp_path / "annotated.png"
        assert main(["overlay", str(M1_PROFILE_PATH), str(source), "--out", str(out)]) == EXIT_OK
        assert cv2.imread(str(out)) is not None

    def test_overlay_reports_an_unreadable_image(self, tmp_path: Path) -> None:
        assert main(
            ["overlay", str(M1_PROFILE_PATH), str(tmp_path / "nope.png")]
        ) == EXIT_USAGE

    def test_overlay_default_output_stays_under_artifacts(self) -> None:
        """Generated images must land in the gitignored artifacts tree."""
        from lsgc.calibration.overlay import ARTIFACT_DIR

        assert ARTIFACT_DIR.parts[0] == "artifacts"


class TestErrorTypesAreDistinguishable:
    def test_calibration_errors_are_not_ingestion_errors(self) -> None:
        from lsgc.errors import IngestionError

        assert not issubclass(CalibrationValidationError, IngestionError)
        assert not issubclass(ChannelMismatchError, IngestionError)
        assert not issubclass(ProfileSchemaError, IngestionError)
