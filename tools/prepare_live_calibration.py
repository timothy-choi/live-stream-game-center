"""Install fixture calibration into a local, gitignored channel directory.

    LSGC_YOUTUBE_CHANNEL_ID=UC... python tools/prepare_live_calibration.py

Copies the M1/M3/M6/M10 Arcade fixture files to:

    artifacts/calibration/{channel_id}/profile.json
    artifacts/calibration/{channel_id}/reference.json
    artifacts/calibration/{channel_id}/glyphs.json
    artifacts/calibration/{channel_id}/logos.json

The fixture channel must match LSGC_YOUTUBE_CHANNEL_ID. Another capture
setup needs its own authored calibration; this script will refuse to bind
the Arcade fixture to a different channel.

Does not start tracking. Does not call YouTube. pytest never runs this.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("'").strip('"')


_load_env_file(ROOT / ".env")

from lsgc.broadcast.calibration import FilesystemCalibrationLookup  # noqa: E402
from lsgc.broadcast.config import CALIBRATION_DIR_ENV, YOUTUBE_CHANNEL_ID_ENV  # noqa: E402
from lsgc.broadcast.sanitize import sanitize_control_plane_text  # noqa: E402
from lsgc.calibration.storage import load_profile  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
DEFAULT_DEST = ROOT / "artifacts" / "calibration"
FILES = (
    ("m1_fixture_profile.json", "profile.json"),
    ("m3_reference.json", "reference.json"),
    ("m6_digit_glyphs.json", "glyphs.json"),
    ("m10_logo_templates.json", "logos.json"),
)


def main() -> int:
    channel = (os.environ.get(YOUTUBE_CHANNEL_ID_ENV) or "").strip()
    if not channel:
        print(f"error: set {YOUTUBE_CHANNEL_ID_ENV}", file=sys.stderr)
        return 2
    dest_root = Path(
        (os.environ.get(CALIBRATION_DIR_ENV) or "").strip() or DEFAULT_DEST
    )
    if not dest_root.is_absolute():
        dest_root = ROOT / dest_root
    profile = load_profile(
        FIXTURES / "m1_fixture_profile.json", expect_channel=channel
    )
    target = dest_root / channel
    target.mkdir(parents=True, exist_ok=True)
    for source_name, dest_name in FILES:
        source = FIXTURES / source_name
        if not source.is_file():
            print(f"error: missing fixture {source_name}", file=sys.stderr)
            return 2
        shutil.copy2(source, target / dest_name)
    lookup = FilesystemCalibrationLookup(dest_root)
    bundle = lookup.get(channel)
    if bundle is None:
        print("error: calibration directory did not load after copy", file=sys.stderr)
        return 2
    if bundle.profile.channel_id != channel or bundle.logos is None:
        print("error: loaded calibration is incomplete", file=sys.stderr)
        return 2
    print(
        f"calibration_dir={dest_root} channel={channel} "
        f"profile={profile.profile_id} revision={profile.revision} "
        f"logos={len(bundle.logos.templates)}"
    )
    print("set LSGC_CALIBRATION_DIR to that calibration_dir before starting the API")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {sanitize_control_plane_text(str(exc))}", file=sys.stderr)
        raise SystemExit(2) from exc
