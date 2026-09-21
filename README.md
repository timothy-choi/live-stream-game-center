# Live Stream Game Center

Backend-driven score tracking for YouTube game broadcasts. The first supported
game mode will be **Madden 27 Arcade**.

The defining constraint of this product: **tracking runs on the backend with
zero viewers watching**. Any YouTube player embedded in the eventual frontend is
presentation only and is never a source of video data. The backend must obtain
frames independently.

**Current milestone: 3 — calibrated scoreboard-presence detection.** Frame
ingestion works, a calibration profile records where a given channel draws its
scoreboard, and the backend can now tell from a single frame whether that
scoreboard is actually visible. No OCR, score reading, team recognition, API,
database, or frontend exists yet. See
[`docs/implementation-status.md`](docs/implementation-status.md).

## Requirements

- Python 3.11+ (verified on 3.13)
- FFmpeg and ffprobe on `PATH` (verified on 8.1.2)

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
```

## Run the ingestion probe

```bash
./.venv/bin/python -m lsgc.probe "https://www.youtube.com/watch?v=aqz-KE-bpKQ" --frames 5
```

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--frames N` | number of samples to decode (default 5) |
| `--interval S` | seconds between samples; `0` takes consecutive frames |
| `--max-height N` | cap the source resolution requested (default 720) |
| `--start-at S` | seek offset, archived sources only |
| `--timeout S` | hard decode deadline (default 90) |
| `--save N` | write the first N frames as PNG under `artifacts/frames/` |
| `--label NAME` | prefix for saved diagnostic filenames |

Live channel URLs work directly, e.g. `https://www.youtube.com/@NASA/live`.

Exit codes distinguish the failing layer: `0` success, `1` bad arguments,
`2` source acquisition failed, `3` decoding failed.

`Frame.timestamp_s` is the absolute offset within the source, so it stays
meaningful after seeking. Saved diagnostics are named with the video id and
that offset, which makes a sample reproducible:

```bash
./.venv/bin/python -m lsgc.probe "https://www.youtube.com/watch?v=hBblAZD-ApY" \
    --start-at 240 --frames 1 --interval 0 --save 1 --label SCOREBOARD_VISIBLE_1
# -> artifacts/frames/SCOREBOARD_VISIBLE_1_hBblAZD-ApY_t00240.000s.png
```

Sources served as DASH segments (YouTube serves past livestreams this way)
are handled by fetching only the segments covering the requested offset, so
seeking into a long recording stays cheap.

## Calibration

A calibration profile records **where** a specific channel draws its
scoreboard: the board bounds, the two score regions, the two team-identity
regions and what those identities show (`name`, `abbreviation`, `logo`, or a
combination), plus stable anchor regions and dynamic ignore regions. It records
no opponent identity and no source timestamps.

Calibration is **per channel**. A profile is bound to a stable channel id and
is never a global Madden default.

```bash
# author a profile: validates, prints a summary, writes JSON
./.venv/bin/python -m lsgc.calibration create \
    --channel-id UCz3q0f-dMPNIyQhntPTE7gQ --profile-id my-channel \
    --reference-size 1280x720 \
    --scoreboard 357,65,576,65 \
    --left-score 470,89,59,26   --right-score 753,89,60,26 \
    --left-identity 368,68,95,59 --right-identity 838,78,66,49 \
    --left-identity-presentation logo --right-identity-presentation logo \
    --anchor 529,70,16,56 --anchor 734,72,18,52 \
    --ignore 546,84,31,17 \
    --out profile.json

# inspect, and draw the geometry over a representative frame
./.venv/bin/python -m lsgc.calibration show profile.json --expect-channel UCz3q0f...
./.venv/bin/python -m lsgc.calibration overlay profile.json frame.png
# -> artifacts/calibration/my-channel_r1_1280x720.png
```

Regions are `x,y,width,height` in pixels from the top-left of the frame, with
exclusive right/bottom edges. They are stored against the reference frame size
they were measured on and rescaled deterministically to another frame of the
same aspect ratio; a different aspect ratio is rejected rather than stretched.

`schema_version` versions the file format; `revision` versions the
measurements, and increments when a channel is recalibrated. Older revisions
keep their meaning.

## Scoreboard-presence detection

A calibration profile says *where* the scoreboard is. Detection also needs to
know *what* the stable parts look like there, which is a separate artifact: a
`ScoreboardReference` holding one small grayscale patch per stable anchor,
sampled from frames that show the normal scoreboard.

```bash
# build reference evidence from representative frames
./.venv/bin/python -m lsgc.vision build-reference profile.json good1.png good2.png \
    --expect-channel UCz3q0f... --out reference.json

# check a frame: exit 0 present, 3 absent, 2 error
./.venv/bin/python -m lsgc.vision detect profile.json reference.json frame.png \
    --expect-channel UCz3q0f... --overlay
```

```python
from lsgc.calibration import load_profile
from lsgc.vision import ScoreboardDetector, load_reference

profile = load_profile("profile.json", expect_channel=CHANNEL)
reference = load_reference("reference.json", expect_channel=CHANNEL)
detector = ScoreboardDetector(profile, reference)

result = detector.detect_frame(frame)     # or .detect(bgr_array)
result.present      # bool
result.score        # lowest anchor correlation, in [-1, 1]
result.anchors      # per-anchor intensity and structure similarity
```

Each anchor is compared with its reference patch on both grayscale intensity
and gradient structure, and a frame is accepted only when every anchor matches
on both. That conservatism is the point: a TOUCHDOWN banner sits in the same
screen band and reuses the board's divider chrome, so a looser test would
accept it and later feed unrelated graphics to score reading.

Detection is per frame and per channel. There is no cross-frame smoothing, no
OCR, and the threshold was measured against one channel's calibration rather
than assumed to be universal.

## Tests

```bash
./.venv/bin/python -m pytest
```

Tests are offline. They decode a synthetic clip generated by FFmpeg and detect
against synthetic imagery, which covers the code but deliberately proves nothing
about YouTube access or real Madden footage. Those are verified by running the
probe against a real URL and by the manual validation run:

```bash
python tools/m3_fixture.py capture     # needs YouTube; writes to artifacts/
python tools/m3_validate.py --sweep
```

## Layout

```
src/lsgc/
  models.py     Frame and MediaSource: the contracts between layers
  resolver.py   YouTube URL -> playable media URL (the only yt-dlp consumer)
  decoder.py    media URL -> bounded stream of decoded frames
  probe.py      ingestion CLI
  calibration/  channel-bound scoreboard geometry: model, validation,
                JSON storage, overlay, authoring CLI. Consumes frame
                imagery only - never yt-dlp or FFmpeg.
  vision/       scoreboard-presence detection: reference evidence,
                detector, diagnostic overlay, CLI
tools/          developer scripts for building and scoring the M3
                validation set; need network, never run by pytest
tests/
  fixtures/     calibration profile, reference evidence and labelled
                validation metadata for the M1 Madden recording
docs/implementation-status.md
artifacts/      generated diagnostics, gitignored
```

Never commit cookie jars, tokens, or generated media.
