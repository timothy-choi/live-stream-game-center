# Implementation Status

Shared memory across coding sessions and tools. Keep it short and factual.
Record only what has actually been run and observed.

Last updated: 2026-09-20 (Milestone 1)

## Product

Multi-user score tracking for YouTube game broadcasts, first game mode
**Madden 27 Arcade**. Owners connect a YouTube channel, calibrate the
scoreboard for that channel, and the backend watches scheduled/live broadcasts,
reads the scoreboard, and publishes an ESPN-style scoreboard and per-game pages.

Two constraints shape everything:

- **Backend tracking must work when zero users have the app open.** Any
  embedded YouTube player is presentation only and never a video source.
  Backend video ingestion is fully independent from the embed.
- **MVP limitation: one game per YouTube broadcast.**

## Architectural direction

Python API, Python vision/processing worker, PostgreSQL, lightweight frontend.
No microservices, no Redis/Celery/Kafka/Kubernetes. None of this exists yet.

## Milestone sequence

| # | Milestone | Status |
| --- | --- | --- |
| M0 | Repo + backend frame ingestion feasibility | **PASS, verified** |
| M1 | Real Madden recording ingestion + recorded-video sampling | **PASS, verified** |
| M2 | Channel-bound calibration foundation | **PASS, verified** |
| M3 | Continuous frame acquisition for a single live broadcast | Not started |
| M4 | Scoreboard region detection + score OCR observations | Not started |
| M5 | Score validation and trusted persisted state | Not started |
| M6 | API + persistence | Not started |
| M7 | Frontend scoreboard and game pages | Not started |

## M0 status: PASS

Goal: prove Python backend code can obtain and decode usable frames from
YouTube without a browser.

- **ARCHIVED YOUTUBE INGESTION: VERIFIED**
- **LIVE YOUTUBE INGESTION: VERIFIED**

## M1 status: PASS

Goal: ingest a real recorded Madden 27 Arcade game and establish reproducible
recorded-video sampling for later calibration.

- **MADDEN RECORDING INGESTION: VERIFIED** (`hBblAZD-ApY`, 1280x720)
- **RECORDED-VIDEO SEEKING: VERIFIED** (exact, +0.000s error, 5s..1910s)
- **SCOREBOARD-VISIBLE / ABSENT EXAMPLES: FOUND** (2 visible, 1 absent, 1 transition)

M1 exposed and fixed a real gap in M0: **M0's resolver only worked for sources
with a single playable URL.** Past-livestream VODs - which is what a recorded
Madden broadcast is - are served exclusively as DASH segments, and M0's code
handed the undecodable base URL to FFmpeg. See "DASH segment delivery" below.

## M2 status: PASS

Goal: define the channel-bound calibration data that a later detector will use
to find the scoreboard, scores and team identities.

- **CALIBRATION MODEL + VALIDATION: VERIFIED** (`lsgc.calibration`)
- **JSON SERIALIZATION ROUND TRIP: VERIFIED**
- **RESOLUTION SCALING 720p -> 1080p: VERIFIED** against a real 1080p frame
- **M1 FIXTURE REPRESENTED: VERIFIED** (`tests/fixtures/m1_fixture_profile.json`)
- **CHANNEL BINDING + REVISIONS: VERIFIED**

No detection, matching, OCR or recognition — a profile records *where to look*,
nothing about what is found there.

## Verified commands

```bash
# setup
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"

# tests (offline, 193 passed)
./.venv/bin/python -m pytest

# archived: 5/5 frames, 1280x720, pts 0.000-4.000s, exit 0
./.venv/bin/python -m lsgc.probe "https://www.youtube.com/watch?v=aqz-KE-bpKQ" --frames 5 --save 2

# live: 5/5 frames, 1280x720, pts 0.000-4.000s, exit 0
./.venv/bin/python -m lsgc.probe "https://www.youtube.com/@NASA/live" --frames 5 --save 2

# failure paths: exit 2 acquisition, exit 3 decode
./.venv/bin/python -m lsgc.probe "https://www.youtube.com/watch?v=aaaaaaaaaaa" --frames 3
./.venv/bin/python -m lsgc.probe "https://www.youtube.com/@NASA/live" --frames 40 --interval 10 --timeout 8
```

## M1: representative Madden recording

`hBblAZD-ApY` - "49ers @ Giants Game 5 - Super Season 8", archived
(`was_live=True`, `is_live=False`), 1952s reported duration, broadcast through
PRISM Live. Decoded at **1280x720** (itag 298, 60fps, ~4018 kbps).

```bash
# ingest the Madden recording
./.venv/bin/python -m lsgc.probe "https://www.youtube.com/watch?v=hBblAZD-ApY" --frames 5

# reproduce a diagnostic frame (writes under the gitignored artifacts/)
./.venv/bin/python -m lsgc.probe "https://www.youtube.com/watch?v=hBblAZD-ApY" \
    --start-at 240 --frames 1 --interval 0 --save 1 --label SCOREBOARD_VISIBLE_1
```

### DASH segment delivery (the M0 gap M1 fixed)

Every format for this recording is `http_dash_segments`; there is no HLS or
progressive option. The format's `url` is a base URL that is **not decodable**
on its own - M0 fed it to ffprobe and got `Invalid data found when processing
input`. The real media is 383 segments addressed by `&sq=N`.

Two properties make bounded seeking possible, both verified:

- **Segments are self-contained.** `sq=60` decodes standalone.
- **Segments carry absolute timestamps.** `sq=60` reports
  `start_time=295.73`, so a segment knows where it sits in the recording.

So seeking fetches only the segments covering the wanted offset (~2MB), never
the 980MB file.

### Timestamp and seek semantics

`Frame.timestamp_s` is the **absolute offset within the source recording**, in
seconds. FFmpeg runs with `-copyts` and frames are picked with `select` rather
than the `fps` filter, because `fps` resamples onto a grid and restamps frames
with grid times (it reported a frame as 297s when 297.5s was requested).

Measured on `hBblAZD-ApY`, requested vs first returned frame:

| Requested | First frame | Error |
| --- | --- | --- |
| 5s / 240s / 300s | identical | +0.000s |
| 302.5s (non-integer) | 302.500s | +0.000s |
| 900s / 1140s / 1500s | identical | +0.000s |
| 1850s / 1900s / 1910s | identical | +0.000s |
| 1914s and beyond | none | clean `DecodeError` |

**Seeking is exact for this recording.** Note the fragment list covers only
0..1910.73s while YouTube metadata reports `duration=1952`; the last ~41s is
not addressable. Offsets past the end fail cleanly rather than hanging.

Caveat: segment index is derived from the nominal `target_duration`, so the
decoder starts one segment early to absorb sources whose timeline does not
begin at zero. Exactness is verified for this recording, not guaranteed for
every recording.

### Diagnostic frames - TEST FIXTURE METADATA ONLY

> **These offsets are development fixtures. They are NOT calibration data and
> NOT runtime detection inputs.** They say nothing about when a scoreboard
> appears in any other recording or in any live broadcast. Another video will
> show its scoreboard at completely different times. Runtime detection must be
> visual, never timestamp-based.

| Name | Offset | Content |
| --- | --- | --- |
| SCOREBOARD_VISIBLE_1 | 240s | gameplay, score 7-7, 1st, 3:11 |
| SCOREBOARD_VISIBLE_2 | 1500s | gameplay, score 49-28, 4th, 4:51 |
| SCOREBOARD_ABSENT | 5s | pregame "QUICK PLAY" team-select menu |
| TRANSITION_TOUCHDOWN | 1140s | TOUCHDOWN banner + play-call menu |

### Scoreboard geometry (manual measurement, 1280x720)

Feasibility evidence only - **not** a calibration profile. The same fixed
coordinates fit both the 7-7 and the 49-28 frame, so the layout is
position-stable within this broadcast.

| Region | x | y | size |
| --- | --- | --- | --- |
| Whole scoreboard | 357..932 | 65..129 | 576x65 |
| Left identity (logo) | 368..462 | 68..126 | 95x59 |
| Left score | 470..528 | 89..114 | 59x26 |
| Quarter | 546..576 | 84..100 | 31x17 |
| Game clock | 618..660 | 83..101 | 43x19 |
| Play clock | 709..733 | 84..100 | 25x17 |
| Down & distance | 586..648 | 112..128 | 63x17 |
| Ball on | 656..697 | 112..128 | 42x17 |
| Right score | 753..812 | 89..114 | 60x26 |
| Right identity (logo) | 838..903 | 78..126 | 66x49 |

Individual score digits measure ~25x21 px; a two-digit score ~54x21.

**Identity presentation: logos and team colours only. No team names or
abbreviations appear on the gameplay scoreboard.** Opponent recognition will
therefore need logo/colour matching, not text OCR of the scoreboard. Full team
names do appear on the pregame menu ("SAN FRANCISCO 49ERS" / "NEW YORK
GIANTS"), which may be a better identity source - but it is a transient screen,
not part of the scoreboard.

Dynamic areas: both scores, quarter, game clock, play clock, down & distance,
ball-on. Stable structure: overall board outline and position, the white curved
dividers separating team panels from the dark centre panel, the dark centre
panel itself, and the team-coloured left/right panels.

### Resolution assessment: 720P SCOREBOARD VISUALLY SUITABLE FOR CALIBRATION

The board occupies 576x65 px (45% of frame width) and score digits are ~25x21
px, rendered as high-contrast white on saturated red/blue. Side-by-side crops
of 720p and 1080p at the same instant show 720p digits clean and unambiguous;
1080p is sharper but adds no legibility for the score itself.

This recording also offers **1920x1080 (itag 299, ~6686 kbps)**, verified to
ingest and seek identically. The default stays 720p; use `--max-height 1080`.

Tradeoff: 1080p costs ~1.66x bandwidth and ~2.25x bytes per frame
(6.2MB vs 2.7MB per decoded BGR frame), which matters once many broadcasts are
tracked concurrently. The case for 1080p is the *small* elements - play clock
and down & distance are only ~11-12px tall at 720p, roughly 17px at 1080p. If a
later milestone needs those, revisit per-channel.

## M2: channel-bound calibration

`lsgc.calibration` consumes frame imagery only. It never imports yt-dlp or
FFmpeg, so the `YouTube -> resolver -> MediaSource -> decoder -> Frame ->
calibration` boundary still holds.

| Module | Purpose |
| --- | --- |
| `geometry.py` | `Region`, the coordinate convention, resolution scaling |
| `profile.py` | `IdentityPresentation`, `CalibrationProfile`, validation, channel binding, revisions |
| `storage.py` | JSON serialization and schema-version handling |
| `overlay.py` | Developer diagnostic overlay |
| `cli.py` | `create` / `show` / `overlay` authoring commands |
| `errors.py` | Calibration errors, deliberately *not* subclasses of `IngestionError` |

```bash
# author a profile (validates, prints a summary, writes JSON)
./.venv/bin/python -m lsgc.calibration create --channel-id UC... --profile-id NAME \
    --reference-size 1280x720 --scoreboard X,Y,W,H --left-score X,Y,W,H ... \
    --left-identity-presentation logo --anchor X,Y,W,H --ignore X,Y,W,H --out PATH.json

# inspect, and draw it over a representative frame (writes under artifacts/)
./.venv/bin/python -m lsgc.calibration show PATH.json --expect-channel UC...
./.venv/bin/python -m lsgc.calibration overlay PATH.json FRAME.png --expect-channel UC...
```

### Coordinate convention

Origin top-left `(0, 0)`, x right, y down, whole pixels. A `Region` covers the
**half-open** box `[x, x+width) x [y, y+height)`, so `right`/`bottom` are
exclusive and a region maps exactly onto `image[y:y+h, x:x+w]`. Adjacent
regions tile with no overlap and no one-pixel gap. M1's notes used inclusive
ranges (`357..932`); the same box is now written `x=357, width=576`.

Containment (`Region.contains`) allows touching edges. Invalid rectangles are
unconstructible: `__post_init__` rejects non-integers, negative origins and
non-positive sizes, so holding a `Region` means holding a valid one.

### Geometry scaling rule

Regions are stored against the reference frame size they were measured on. A
profile is remapped with `profile.for_frame_size(w, h)`:

- Aspect ratio must match within **0.5%** (`ASPECT_RATIO_TOLERANCE`), otherwise
  the call raises rather than silently distorting. The tolerance is not zero
  because YouTube serves 480p as 854x480, which is 16:9 only to within 0.08%;
  4:3 (33% out) and mod-16 padded 1920x1088 (0.74% out) are still rejected.
- Each **edge** is scaled and rounded independently, then converted back to
  width/height. Scaling edges rather than origin-plus-size keeps regions that
  share a boundary sharing it afterwards.
- Rounding is **half-up** (`floor(v + 0.5)`), not Python's banker's `round`, so
  results are predictable and directly assertable.
- Results are clamped into the target frame, and no region can collapse to zero
  pixels.

Verified against a real 1920x1080 frame of the same broadcast: the 720p profile
scaled up still lands on every element.

### Channel binding

A profile carries a stable platform `channel_id` (a YouTube `UC...` id, never a
display name). `assert_channel()` raises `ChannelMismatchError` on a mismatch,
and `load_profile(path, expect_channel=...)` makes the expected channel a
**required** keyword argument. Tooling that must inspect a file without knowing
its channel calls the explicitly named `load_profile_unchecked()`. The result is
that runtime code cannot quietly treat one channel's profile as a global Madden
default. There is no channel database yet; the id is supplied explicitly.

### Schema version vs profile revision

Two independent numbers:

- **`schema_version`** (currently `1`) versions the *file format*. It changes
  when this code changes the shape of the data. Loading an unknown version
  fails with `ProfileSchemaError` before anything else is parsed, so a future
  format is never misread as a malformed current one.
- **`revision`** versions the *measurements*. It increments when a human
  recalibrates a channel, via `next_revision()`. Profiles are frozen, so
  revision 1 keeps its meaning when revision 2 is created, and both serialize
  to independent files. A channel recalibrated ten times is still schema 1.

`profile_id` identifies the calibration lineage and is stable across revisions.

### Stable anchors and dynamic ignore regions

**Stable anchors** are small regions expected to stay visually constant while
the real scoreboard is displayed. They exist to separate "something occupies the
scoreboard band" from "the calibrated scoreboard is present" — M1 found a
TOUCHDOWN banner sitting exactly where the scoreboard goes, showing the scoring
team's logo on both sides. At least one is required; all must lie inside the
scoreboard bounds. M2 only stores them; matching is a later milestone.

**Dynamic ignore regions** are areas expected to change, which must not be
treated as stable evidence. They are generic rectangles — a profile does not
label which is the clock and which is the down marker — and are only required
to lie inside the frame, since a distraction outside the board is still worth
ignoring. Scores and identities stay separately represented even when they also
appear here.

### M1 fixture validation — FIXTURE VALUES, NOT PRODUCT DEFAULTS

`tests/fixtures/m1_fixture_profile.json`, calibrated from the M1 diagnostic
frames of `hBblAZD-ApY` at **1280x720**, channel `UCz3q0f-dMPNIyQhntPTE7gQ`:

| Element | Region (x, y, w, h) |
| --- | --- |
| scoreboard | 357, 65, 576, 65 |
| left score | 470, 89, 59, 26 |
| right score | 753, 89, 60, 26 |
| left identity | 368, 68, 95, 59 |
| right identity | 838, 78, 66, 49 |
| anchor 0 (left panel divider) | 529, 70, 16, 56 |
| anchor 1 (right panel divider) | 734, 72, 18, 52 |
| 7 dynamic ignores | both scores, quarter, game clock, play clock, down & distance, ball-on |

Both identity presentations are **LOGO**, because this broadcast's gameplay
scoreboard shows logos and no usable team text. That is an observation about
this capture setup, not a Madden-wide rule and not a default: the model
supports `name`, `abbreviation`, `logo` and combinations, and presentation is a
required argument with no default value.

Regions were re-measured against the actual images rather than copied from M1's
notes. Anchors were chosen empirically: comparing the 7-7 frame with the 49-28
frame, each anchor changes by a mean of ~4.8/4.0 grey levels while the score
regions change by ~91/107. An early anchor candidate was rejected because a test
caught it overlapping the play-clock ignore region.

**A calibration profile contains geometry and layout, never permanent opponent
identity.** The fixture's teams, logos, colours and scores are not stored, and a
test asserts the JSON mentions none of them.

**Recorded-video timestamps are fixture metadata, not calibration or runtime
input.** No source offset appears in `CalibrationProfile`; another broadcast
will show its scoreboard at completely different times.

## Verified technical findings

- **yt-dlp version is load-bearing.** The preinstalled system yt-dlp
  `2026.06.09` resolves a URL that then returns **HTTP 403** from
  `googlevideo.com`. It selects the `android_vr` client, which YouTube's SABR
  rollout broke on 2026-08-18. Stable `2026.8.19` fixed client selection
  (dropped `android_vr`, added `web_embedded` fallbacks and a `visionos`
  client). The project pins `yt-dlp>=2026.8.19`.
- **Streamlink is not viable as the resolver.** Streamlink 7.6.0 rejects the
  archived test video with `This plugin does not support protected videos, try
  yt-dlp instead`. Not added as a dependency.
- **No PO token or cookies were needed** for the public archived video or the
  public live stream on this machine/IP. No authentication was used or bypassed.
- **Both archived and live resolve to HLS** (`m3u8_native`, format `311`,
  1280x720). ffmpeg reads the live playlist near the live edge and does not
  download the whole broadcast.
- **Frames are OpenCV-ready with no conversion**: `numpy.ndarray`,
  `dtype=uint8`, shape `(H, W, 3)`, BGR, C-contiguous.
- **Live throughput is not a bottleneck at this scale**: 300 consecutive live
  frames (~5s of 60fps video) decoded in under 2 seconds.
- **JS runtimes present** (node v20.20.2, deno 2.8.3). yt-dlp needs one for
  signature/nsig challenges; treat this as a deployment requirement.

## Timing metadata: what we actually get

`Frame.timestamp_s` is the decoder presentation timestamp from FFmpeg's
`showinfo` filter. `Frame.received_at` is the wall-clock UTC read time.

**Known limitation:** on live sources PTS started at `0.000` for a broadcast
running for hours, so PTS is an offset within the segment window we opened,
**not** a broadcast wall-clock time. Correlating a frame to real broadcast time
is unsolved and matters for Milestone 5 score validation. `received_at` is the
only real-time clock available today, and it lags the broadcast by the stream
delay.

## Decisions made

1. **yt-dlp for source resolution, FFmpeg for decoding.** Chosen after testing;
   streamlink failed on the test video.
2. **FFmpeg subprocess over `cv2.VideoCapture`.** Explicit control over
   headers, read timeouts, bounded frame counts, and process termination.
3. **Never write media to disk.** Raw BGR is piped from FFmpeg and wrapped in
   numpy. Frame count is bounded at the FFmpeg invocation (`-frames:v`).
4. **Resolver headers are replayed to FFmpeg.** Google CDN URLs are bound to
   the requesting client; without the original user-agent the fetch can 403.
5. **`SourceResolutionError` vs `DecodeError` are separate types** with
   separate CLI exit codes (2 and 3), because "YouTube refused us" and "we
   cannot decode" need different operational responses.
6. **Vision code will depend only on `Frame`**, never on yt-dlp or FFmpeg, so
   the resolver can be replaced without touching the pipeline.
7. **`certifi` is an explicit dependency** — the macOS python.org build does
   not trust system roots and fails with `CERTIFICATE_VERIFY_FAILED`.

## Known blockers and limitations

- **One successful live test does not prove production reliability.** Untested:
  multi-hour continuous capture, reconnect after segment gaps, resolution
  changes mid-broadcast, ad insertion, DVR-window behaviour, and datacenter IPs
  (cloud IP ranges attract bot checks far more than a residential IP).
- **YouTube extraction is an adversarial moving target.** The 403 discovered
  during this milestone is exactly that risk. Pin yt-dlp, monitor upstream, and
  expect a broken extractor to be an operational event.
- **PO tokens / cookies may become necessary** on other networks. Not required
  here. If ever added, cookie jars must stay gitignored and out of commits.
- Live PTS is not broadcast wall-clock (see above).
- The probe samples once and exits. Long-lived capture is M3.

### M1-specific limitations

- Everything scoreboard-related above is **one broadcast from one channel**.
  Layout, resolution, and identity presentation may differ per channel, which
  is exactly why calibration is channel-bound.
- The overlay is PRISM Live phone-broadcast output; a different capture setup
  will place things differently.
- Seek exactness is verified for this recording only.
- The last ~41s of this recording is not addressable via its fragment list.
- `-copyts` also changed what live sources report: the NASA stream now shows
  absolute HLS media-timeline values (e.g. 81211.962s) instead of 0. It is
  still **not** wall-clock broadcast time. Live timing is otherwise untouched
  and remains an M3/M5 problem.

### M2-specific limitations

- **Calibration is authored by hand.** Coordinates are measured by a developer
  and passed to the CLI. There is no assisted or automatic region proposal, and
  no product UI.
- **One profile exists**, for one channel. Nothing proves the model generalises
  to a second capture setup until a second channel is calibrated.
- **Anchors are stored, never matched.** Their usefulness as detection evidence
  is asserted only by the frame-difference measurement above, which compares two
  frames from a single broadcast.
- **Scaling is proportional only.** No letterbox detection, cropping, rotation
  or perspective correction. A feed whose shape differs must be recalibrated.
- **No profile registry.** Profiles are files; nothing maps a channel to its
  current profile or resolves which revision is active. That arrives with
  persistence.
- Only schema version 1 exists, so no migration path has been exercised.

## Next action

M3: turn the one-shot probe into a supervised, long-running capture loop for a
single live broadcast — reconnect on segment gaps and stream restarts, sample
at a fixed cadence for the length of a game, and record whether ingestion
survives a multi-hour broadcast. Explicitly **not** yet: detection, OCR,
persistence, API.

M1 answered M0's open question by identifying and measuring a representative
Madden recording. M2 turned those measurements into a validated, versioned,
channel-bound profile that a future detector can consume.
