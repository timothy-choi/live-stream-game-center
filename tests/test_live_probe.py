from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from lsgc.live import cli
from lsgc.live.state import SessionHealth, SessionState
from lsgc.models import Frame


class _FakeSession:
    def __init__(self, url: str, **kwargs: object) -> None:
        self.url = url
        self.kwargs = kwargs
        self._cancelled = False
        self._state = SessionState.ENDED
        started = datetime(2026, 6, 1, tzinfo=UTC)
        self._health = SessionHealth(
            state=SessionState.ENDED,
            watch_url=url,
            source_id="livevid",
            interval_s=float(kwargs.get("interval_s", 2.0)),  # type: ignore[arg-type]
            frames_emitted=1,
            frames_skipped=0,
            resolve_attempts=1,
            reconnect_count=0,
            consecutive_failures=0,
            source_epoch=1,
            last_frame_at=started,
            last_pts_s=1.0,
            last_error=None,
            started_at=started,
            ended_at=started,
            longest_gap_s=2.0,
            last_gap_s=2.0,
        )

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def health(self) -> SessionHealth:
        return self._health

    def frames(self):
        image = np.zeros((24, 32, 3), dtype=np.uint8)
        yield Frame(
            index=0,
            image=image,
            width=32,
            height=24,
            timestamp_s=1.0,
            source_url=self.url,
            source_id="livevid",
            is_live=True,
            received_at=datetime(2026, 6, 1, tzinfo=UTC),
            extra={"source_epoch": 1},
        )


def test_parser_defaults() -> None:
    args = cli.build_parser().parse_args(["https://youtu.be/x"])
    assert args.interval == 2.0
    assert args.duration == 900.0
    assert args.profile is None


def test_url_is_required() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_partial_detector_args_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "LiveFrameSession", _FakeSession)
    assert cli.main(["https://youtu.be/x", "--profile", "p.json"]) == cli.EXIT_USAGE


def test_success_path_prints_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "LiveFrameSession", _FakeSession)
    assert cli.main(["https://www.youtube.com/watch?v=x", "--duration", "0"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "live-probe" in out
    assert "summary" in out
    assert "does not mean any game score is final" in out
    assert "googlevideo" not in out
