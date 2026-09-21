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
| 1 | Repo + backend frame ingestion feasibility | **Complete, verified** |
| 2 | Continuous frame acquisition for a single live broadcast | Not started |
| 3 | Channel-bound scoreboard calibration | Not started |
| 4 | Scoreboard region detection + score OCR observations | Not started |
| 5 | Score validation and trusted persisted state | Not started |
| 6 | API + persistence | Not started |
| 7 | Frontend scoreboard and game pages | Not started |

## Milestone 1 status: COMPLETE

Goal: prove Python backend code can obtain and decode usable frames from
YouTube without a browser.

- **ARCHIVED YOUTUBE INGESTION: VERIFIED**
- **LIVE YOUTUBE INGESTION: VERIFIED**

## Verified commands

```bash
# setup
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"

# tests (offline, 18 passed)
./.venv/bin/python -m pytest

# archived: 5/5 frames, 1280x720, pts 0.000-4.000s, exit 0
./.venv/bin/python -m lsgc.probe "https://www.youtube.com/watch?v=aqz-KE-bpKQ" --frames 5 --save 2

# live: 5/5 frames, 1280x720, pts 0.000-4.000s, exit 0
./.venv/bin/python -m lsgc.probe "https://www.youtube.com/@NASA/live" --frames 5 --save 2

# failure paths: exit 2 acquisition, exit 3 decode
./.venv/bin/python -m lsgc.probe "https://www.youtube.com/watch?v=aaaaaaaaaaa" --frames 3
./.venv/bin/python -m lsgc.probe "https://www.youtube.com/@NASA/live" --frames 40 --interval 10 --timeout 8
```

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
- The probe samples once and exits. Long-lived capture is Milestone 2.

## Next action

Milestone 2: turn the one-shot probe into a supervised, long-running capture
loop for a single live broadcast — reconnect on segment gaps and stream
restarts, sample at a fixed cadence for the length of a game, and record
whether ingestion survives a multi-hour broadcast. Explicitly **not** yet:
detection, OCR, persistence, API.

Open question for the product owner: supply a representative **Madden 27 Arcade
YouTube broadcast** (archived is fine) so calibration work in Milestone 3
targets a real scoreboard layout rather than a guess.
