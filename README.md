# Live Stream Game Center

Backend-driven score tracking for YouTube game broadcasts. The first supported
game mode will be **Madden 27 Arcade**.

The defining constraint of this product: **tracking runs on the backend with
zero viewers watching**. Any YouTube player embedded in the eventual frontend is
presentation only and is never a source of video data. The backend must obtain
frames independently.

**Current milestone: 12 complete (PASS) — ESPN-style web app foundation.**
The browser is a consumer of the M11 API. **The browser does not perform
score recognition. The browser does not start tracking. Closing the browser
does not stop tracking.** See
[`docs/implementation-status.md`](docs/implementation-status.md).

## Requirements

- Python 3.11+ (verified on 3.13)
- FFmpeg and ffprobe on `PATH` (verified on 8.1.2)
- PostgreSQL 16 for persistence (optional for offline tests; required to
  recover confirmed scores and confirmed matchups after a restart)
- YouTube Data API v3 credentials for M9 broadcast discovery (API key and/or
  OAuth access token). Never committed; see `.env.example`.
- FastAPI for the M11 read-only API. Orchestrator stays off unless
  `LSGC_RUN_ORCHESTRATOR=true`.
- Node.js 20+ for the M12 web app (`web/`).

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

Detection is per frame and per channel. There is no cross-frame smoothing.
The threshold was measured against one channel's calibration rather than
assumed to be universal. Score reading is a separate step, gated on this
result; detection does not use OCR.

## Numeric score reading

When the detector says the calibrated scoreboard is **PRESENT**, `lsgc.score`
crops the calibrated left and right score regions and reads non-negative
integers from those pixels. The result is a `RawScoreObservation`: what this
frame *appears* to show, not a confirmed score and not trusted game state.

```python
from lsgc.calibration import load_profile
from lsgc.score import ScoreObservationPipeline, ScoreReader, load_glyphs
from lsgc.vision import ScoreboardDetector, load_reference

profile = load_profile("profile.json", expect_channel=CHANNEL)
reference = load_reference("reference.json", expect_channel=CHANNEL)
glyphs = load_glyphs("glyphs.json", expect_channel=CHANNEL)

pipeline = ScoreObservationPipeline(
    ScoreboardDetector(profile, reference),
    ScoreReader(glyphs),
)
observation = pipeline.observe_frame(frame)  # None when the board is ABSENT
if observation is not None:
    observation.left.value    # int or None
    observation.right.readable
```

```bash
# gated read of one frame (exit 3 if the scoreboard is absent)
./.venv/bin/python -m lsgc.score read profile.json glyphs.json frame.png \
    --reference reference.json --expect-channel UCz3q0f...
```

Left and right are independent. A side may be `readable=False, value=None`
instead of inventing a number. Recognition is digit localisation plus
zero-normalised correlation against 0–9 grayscale glyphs for this channel’s
Arcade score font — not full-frame OCR, and not a timestamp-to-score table.

## Temporal score validation

A raw M6 observation never overwrites trusted state. `lsgc.track` consumes a
chronological stream of `RawScoreObservation` objects (and absences) and
answers: what pair has enough consecutive visual evidence to trust *right now*?

```python
from lsgc.track import ConfirmationPolicy, TemporalScoreValidator

validator = TemporalScoreValidator()  # required=2, min_span=2s, max_gap=8s
result = validator.apply(observation)  # or validator.note_absence(...)
validator.state.pair                   # None until first confirmation
validator.history                      # in-memory ConfirmedScoreChange events
validator.end_acquisition()            # freeze; not a game-final
validator.reset()                      # explicit new-game boundary
```

```bash
# deterministic JSON replay (no video)
./.venv/bin/python -m lsgc.track replay sequence.json

# thin live wiring: LiveFrameSession -> M3/M6 -> validator (developer probe)
./.venv/bin/python -m lsgc.track live "YOUTUBE_WATCH_URL" \
    --profile tests/fixtures/m1_fixture_profile.json \
    --reference tests/fixtures/m3_reference.json \
    --glyphs tests/fixtures/m6_digit_glyphs.json \
    --expect-channel UCz3q0f-dMPNIyQhntPTE7gQ
```

Initialization also requires confirmation: the first readable frame is not
trusted, and a session does not start at 0-0. Partial reads never invent the
missing side. Scoreboard absence, acquisition gaps, and `source_epoch` changes
preserve trusted score and restart candidate evidence. Football scoring rules
are not used. Acquisition ENDED does not print FINAL.

## Durable trusted-score persistence

M8 writes M7 confirmations to PostgreSQL so trusted score and confirmed
history survive a process restart. Unconfirmed candidate evidence is **not**
stored. Creating a game row does not insert 0-0.

**Persisted trusted score = temporally confirmed visual score.** It is not
official, corrected, final, or winner-certified.

Durable identity is `(platform, external_broadcast_id)` — for YouTube, the
video/broadcast id, never the title or watch URL. Tracking status is
`TRACKING` or `ACQUISITION_ENDED` (acquisition ended ≠ game-final).

```bash
cp .env.example .env   # then edit; .env is gitignored
docker compose -f docker-compose.postgres.yml up -d
./.venv/bin/python -m lsgc.persist migrate

# PostgreSQL integration tests (uses LSGC_TEST_DATABASE_URL, a *_test database)
LSGC_TEST_DATABASE_URL=postgresql://lsgc:lsgc_dev@127.0.0.1:54329/lsgc_test \
  ./.venv/bin/python -m pytest -m postgres
```

`TemporalScoreValidator.restore` reloads only durable confirmed state. Runtime
`Frame.index` ordering starts fresh after restart so a new `LiveFrameSession`
is not treated as stale. If a confirmation is accepted in memory but the
database write fails, the tracker reloads from PostgreSQL rather than keeping
the uncommitted score.

M7 `reset()` is not mapped to DELETE of persisted history. A new broadcast id
is a new game.

## Broadcast discovery and tracking orchestration

M9 is the **control plane**. It uses the YouTube Data API v3 to discover
broadcasts for explicitly configured channels. It does **not** scrape HTML and
does **not** use yt-dlp playlists as a schedule database. yt-dlp remains the
**data plane** inside M4 (`LiveFrameSession`) for playable media.

Identity is the exact YouTube video/broadcast ID. A scheduled broadcast that
goes live is the same durable game because the ID is unchanged. Titles and
timestamps are mutable metadata only.

Platform lifecycle (`UNKNOWN` / `UPCOMING` / `LIVE` / `ENDED`) is stored
separately from acquisition/tracking (`TRACKING` / `ACQUISITION_ENDED`).
**Neither is GAME FINAL.** Ending a YouTube broadcast or an M4 session does
not choose a winner or certify a score.

```bash
# one-shot metadata sync (needs DATABASE_URL + YouTube credentials)
./.venv/bin/python -m lsgc.broadcast sync

# reconciliation loop: sync + ensure local tracking workers
./.venv/bin/python -m lsgc.broadcast run
```

Configuration (placeholders in `.env.example`; never commit real values):

| variable | purpose |
| --- | --- |
| `LSGC_YOUTUBE_CHANNEL_ID` or `LSGC_YOUTUBE_CHANNEL_IDS` | explicit connected channel(s); no hardcoded `UC…` in source |
| `LSGC_YOUTUBE_API_KEY` | Data API key (`search.list` + `videos.list`) |
| `LSGC_YOUTUBE_ACCESS_TOKEN` | optional OAuth; prefers `liveBroadcasts.list mine=true` |
| `LSGC_CALIBRATION_DIR` | `{dir}/{channel_id}/profile.json` + `reference.json` + `glyphs.json` + optional `logos.json` |
| `LSGC_BROADCAST_SYNC_INTERVAL_S` | poll interval; default 1800s with an API key, 120s with OAuth |

Tracking starts only when: the broadcast belongs to the configured channel,
platform state is `LIVE`, `tracking_enabled` is true, that channel’s
calibration exists, and no local worker already owns the ID. Missing
calibration is `NOT_READY` — another channel’s profile is never substituted.

The supervisor keeps **zero or one** in-process worker per broadcast ID.
Each worker is `LiveFrameSession` → shared M3 presence check → independent
score path (`ScoreObservationPipeline` → `DurableScoreTracker`) and identity
path (`MatchupObservationPipeline` → `DurableMatchupTracker`). Opening a
game page is not involved.

```
python tools/m9_validate.py   # optional real Data API check; never run by pytest
```

## Team identity and confirmed matchup

M10 reads **who is on the scoreboard**, independently of M6/M7/M8 scores.

Layers stay separate:

1. `TeamIdentityObservation` — one calibrated crop, one side, one frame.
2. `MatchupObservation` — left + right from a scoreboard-present frame.
3. `ConfirmedMatchup` — the same complete pair seen repeatedly in order.
4. Durable confirmed matchup — PostgreSQL fields + append-only
   `confirmed_matchup_changes`.

A raw logo/name read never overwrites the durable pair. After confirmation,
a different complete pair is a **CONFLICT**: the original pair is kept
(one broadcast = one game). Candidate votes are not stored.

**Canonical identity** is a stable team code (`SF`, `NYG`, `GB`, `LAC`, `LV`,
`MIN`, …), not the display name. Left/right are scoreboard positions, not
home/away. The catalog lists 32 NFL teams; **visual templates currently
cover the six teams observed on this Arcade fixture.**

Presentation routing follows M2 `IdentityPresentation`: attempt usable
name/abbreviation text first (exact aliases only; no fuzzy guess), otherwise
logo recognition. The established Madden 27 Arcade fixture is **LOGO** on
both sides, so the practical path is logo matching. Text OCR on this overlay
is unimplemented and unvalidated (`UnvalidatedTextReader`).

Logo method (this overlay, after comparing grayscale ZNCC, color cosine,
and combinations): 32×24 grayscale descriptor, zero-normalised correlation,
gated by mean-BGR cosine. Combined score = min(ZNCC, color). Reject unless
the winner clears `min_score=0.70` and beats the next team by
`min_margin=0.15`. Those cuts were frozen on construction/tuning crops.
Similarity is a heuristic, not a probability. UNKNOWN is a correct outcome.

Runtime recognition does not use video IDs, titles, timestamps, playlist
position, or expected-matchup fields. A YouTube title may be stored by M9
and compared after confirmation for logging; a disagreement does not rewrite
visual identity.

Default temporal policy (live 2s cadence): two complete distinct-team pairs
spanning ≥ 2.0s, with an 8s candidate gap. Sparse labelled offsets disable
the gap the same way M7 sparse replay does.

```bash
./.venv/bin/python -m lsgc.identity read \
    tests/fixtures/m1_fixture_profile.json \
    tests/fixtures/m10_logo_templates.json \
    artifacts/m3/t0040.000.png \
    --reference tests/fixtures/m3_reference.json \
    --expect-channel UCz3q0f-dMPNIyQhntPTE7gQ
```

## Continuous live acquisition

`LiveFrameSession` keeps one YouTube **watch URL** open and yields sampled
`Frame`s until the broadcast ends, the caller cancels, or retries are
exhausted. Resolved CDN URLs are ephemeral; the watch URL is what gets
re-resolved after a stall.

```bash
# bounded developer run (default 15 minutes). Optional M3 classification:
./.venv/bin/python -m lsgc.live_probe "YOUTUBE_WATCH_URL" \
    --interval 2 --duration 900 \
    --profile tests/fixtures/m1_fixture_profile.json \
    --reference tests/fixtures/m3_reference.json \
    --expect-channel UCz3q0f-dMPNIyQhntPTE7gQ
```

`--duration 0` runs until the broadcast ends or you interrupt. Ending
acquisition does **not** mark a game score final. Diagnostics never print
signed media URLs.

The 2-second default cadence is a developer default, not a measured optimum.

## Product API (M11)

The API is a **read-only** delivery boundary. Tracking stays backend-owned by
M9. Zero viewers, one viewer, many viewers, or disconnecting every subscriber
does not start, stop, or reset the M4–M10 pipeline.

**PostgreSQL is the source of truth.** Realtime SSE is an optimization for
freshness. Clients must never depend on receiving every message.

### Start

```bash
# API only — no YouTube credentials required
./.venv/bin/python -m lsgc.api

# Same process as M9 orchestration (needs YouTube + DATABASE_URL)
LSGC_RUN_ORCHESTRATOR=true ./.venv/bin/python -m lsgc.api
```

Or `uvicorn lsgc.api.app:app_from_env --factory --host 127.0.0.1 --port 8000`.

Configuration is environment-only. Do not hard-code PostgreSQL credentials.
See `.env.example`. **M11 has no authentication** — local/development trust
only. CORS is opt-in via `LSGC_CORS_ORIGINS` (never unrestricted by default).

### Routes

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Process alive + database reachable (YouTube not required) |
| GET | `/api/games` | Product list/feed. `lifecycle`, `channel_id`, `limit`, `offset` |
| GET | `/api/games/{id}` | Snapshot: identity, lifecycle, matchup, trusted score |
| GET | `/api/games/{id}/score-events` | Append-only confirmed score timeline |
| GET | `/api/games/{id}/events` | SSE product events |

There is no separate `/feed`. Home-page “live + next N upcoming” uses
`GET /api/games` with `lifecycle=LIVE,UPCOMING` and `limit`. Default order is
LIVE, then UPCOMING by scheduled start, then recently ENDED. Default `limit`
is 50 (max 100).

All timestamps are ISO-8601 UTC with a `Z` suffix. Naive values are not
returned.

### Score and matchup

- Score status is `UNCONFIRMED` or `TRUSTED`. Never `FINAL` / `OFFICIAL`.
- Before M7 confirmation, `left`/`right` are `null` and `revision` is `0`.
  The API does not invent `0-0`.
- Matchup status is `UNCONFIRMED`, `CONFIRMED`, or `CONFLICT`.
- Before M10 confirmation, teams are `null`.
- After confirmation, teams come from the M10 catalog (`id`, `display_name`,
  `abbreviation`). A later contradiction keeps the confirmed pair and sets
  `CONFLICT` plus a short `conflict_pair`.

### Realtime (SSE)

SSE is used because M11 is one-way server → client. WebSockets are not
implemented.

Event envelope: `event_id`, per-game `sequence`, `game_id`, `type`,
`occurred_at`, `score_revision`, `matchup_revision`, `cursor`, `payload.game`.

Types: `GAME_SNAPSHOT`, `SCORE_CONFIRMED`, `MATCHUP_CONFIRMED`,
`MATCHUP_CONFLICT`, `PLATFORM_LIFECYCLE_CHANGED`, `TRACKING_STATUS_CHANGED`,
`RESYNC_REQUIRED`.

**Reconnect algorithm**

1. Fetch `GET /api/games/{id}` (and score-events if needed).
2. Subscribe to `/api/games/{id}/events`. The first event is `GAME_SNAPSHOT`
   generated **after** registration, so a mutation between snapshot and
   subscribe is visible (lost-wakeup closed).
3. On disconnect, fetch durable state again (or `?once=true` for a single
   snapshot) and resubscribe.
4. Ignore stale/duplicate events using `sequence`, `cursor`, and
   `score_revision`.

No generic event table was added. Catch-up is derived from `games`,
`confirmed_score_changes`, and matchup columns. An in-process hub is notified
after successful writes. Publisher failures are swallowed. Process restart
drops ephemeral notifications; durable snapshot/cursor recovery still works.

Subscriber queues are bounded (`LSGC_SSE_MAX_QUEUE`, default 32). Overflow
marks the subscriber and sends `RESYNC_REQUIRED` instead of growing memory
or blocking the tracking worker. Heartbeats are SSE comments
(`LSGC_SSE_HEARTBEAT_S`, default 15s) and are not game state.

FastAPI concurrent requests use a small `psycopg_pool` connection pool. M9
workers may still use one serialized connection; the API must not share it.

```bash
python tools/m11_validate.py            # in-memory snapshot/subscribe/reconnect
python tools/m11_validate.py --postgres # same against LSGC_TEST_DATABASE_URL
```

## Web app (M12)

React + TypeScript + Vite in `web/`. The browser talks only to M11:

- `GET /api/games`
- `GET /api/games/{id}`
- `GET /api/games/{id}/score-events`
- `GET /api/games/{id}/events` (SSE)

**The browser does not perform score recognition. The browser does not start
tracking. Closing the browser does not stop tracking.**

### Local startup

```bash
# API with CORS for Vite. For a durable DB, set DATABASE_URL and:
#   LSGC_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173 \
#   ./.venv/bin/python -m lsgc.api
# Demo API (in-memory representative games, no YouTube, no tracking workers):
./.venv/bin/python tools/m12_demo_api.py
# Optional validation-only hook (not an M11 product route):
#   POST /__demo/confirm-score?game_id=live&left=35&right=14

# Frontend
cd web
cp .env.example .env.local   # VITE_API_BASE_URL=http://127.0.0.1:8000
npm install
npm run dev                  # http://localhost:5173
npm test
npm run build
```

Do not set backend CORS to `*`. Origins stay explicit.

Routes: `/` scoreboard, `/games/:gameId` detail, unknown routes show not-found.

Home uses backend list order (LIVE, UPCOMING, ENDED). Empty sections are
omitted. Feed membership refreshes every `VITE_FEED_REFRESH_S` seconds
(default 60) and on window focus — that is discovery presentation, not
score tracking. Live cards subscribe per LIVE game via SSE and unsubscribe
when the game leaves LIVE or the page unmounts. Upcoming/ended games are
not kept on permanent SSE.

Score semantics match M11: unconfirmed scores render as — (never fabricated
0-0); ended games say ENDED / latest tracked score, never FINAL; sides are
left/right, not home/away; title is never treated as confirmed identity.

The YouTube embed is `https://www.youtube.com/embed/{external_broadcast_id}`
only. Show/hide is local presentation. Hiding the player does not stop
tracking.

Reconnect: fetch durable snapshot, subscribe, apply `GAME_SNAPSHOT` after
registration, ignore stale revisions, refetch on `RESYNC_REQUIRED`.

## Tests

```bash
./.venv/bin/python -m pytest
```

Tests are offline by default. They decode a synthetic clip generated by FFmpeg
and detect against synthetic imagery, which covers the code but deliberately
proves nothing about YouTube access or real Madden footage. PostgreSQL tests
are skipped unless `LSGC_TEST_DATABASE_URL` points at a `*_test` database.

```bash
python tools/m3_fixture.py capture     # needs YouTube; writes to artifacts/
python tools/m3_validate.py --sweep
python tools/m5_fixture.py capture     # needs YouTube; four M5 recordings
python tools/m5_validate.py
python tools/m6_validate.py            # score reading; reuses M3/M5 frames
python tools/m6_validate.py --1080
python tools/m7_validate.py            # sparse labelled replay (no new video)
python tools/m7_fixture.py capture     # needs YouTube; 2s clusters
python tools/m7_validate.py --dense    # default live policy on those clusters
python tools/m8_validate.py            # M7+M8 persist/restart; needs PostgreSQL
python tools/m9_validate.py            # optional YouTube Data API metadata check
python tools/m10_validate.py           # identity+matchup; reuses M3/M5 frames
python tools/m10_validate.py --1080
python tools/m10_validate.py --postgres
python tools/m11_validate.py
python tools/m11_validate.py --postgres
```

## Layout

```
src/lsgc/
  models.py     Frame and MediaSource: the contracts between layers
  resolver.py   YouTube URL -> playable media URL (the only yt-dlp consumer)
  decoder.py    media URL -> bounded or continuous sampled frames
  probe.py      bounded ingestion CLI
  live/         continuous session for one live watch URL + live_probe CLI
  live_probe.py python -m lsgc.live_probe entry
  calibration/  channel-bound scoreboard geometry: model, validation,
                JSON storage, overlay, authoring CLI. Consumes frame
                imagery only - never yt-dlp or FFmpeg.
  vision/       scoreboard-presence detection: reference evidence,
                detector, diagnostic overlay, CLI
  score/        numeric score reading: digit glyphs, crop preprocess,
                ScoreReader, detector-gated RawScoreObservation. Observation
                only — not trusted score state.
  track/        temporal confirmation: ConfirmationPolicy, TrustedScoreState,
                CandidateScore, ConfirmedScoreChange, TemporalScoreValidator.
                restore() loads durable confirmed state; not a game-final.
  persist/      PostgreSQL games + append-only confirmed_score_changes and
                confirmed_matchup_changes, DurableScoreTracker,
                DurableMatchupTracker, optional change publisher, connection
                pool for concurrent API reads.
  identity/     team catalog, logo templates, presentation routing,
                MatchupObservationPipeline, TemporalMatchupValidator.
                Raw identity is not a confirmed matchup.
  broadcast/    YouTube Data API discovery, schedule sync, eligibility,
                in-process TrackingSupervisor. Control plane only.
  api/          read-only FastAPI + SSE. Product schemas, event hub, routes.
                Never starts tracking.
web/            React + TypeScript + Vite scoreboard. M11 consumer only.
migrations/     versioned SQL
docker-compose.postgres.yml
tools/          developer scripts for M3–M12 labelled-set capture and scoring;
                network/DB as noted, never run by default pytest
tests/
  fixtures/     calibration profile, M3 reference, M6 digit glyphs, M10 logo
                templates, M3/M5/M6/M10 labelled validation metadata, M7
                dense-replay cluster offsets (no decoded frames)
docs/implementation-status.md
artifacts/      generated diagnostics, gitignored
```

Never commit cookie jars, tokens, or generated media.
