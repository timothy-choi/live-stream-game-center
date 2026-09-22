# Implementation Status

Shared memory across coding sessions and tools. Keep it short and factual.
Record only what has actually been run and observed.

Last updated: 2026-09-21 (M12 PASS; M12.5A YouTube OAuth connection PASS)

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
No microservices, no Redis/Celery/Kafka/Kubernetes. PostgreSQL persistence
exists (M8). YouTube broadcast discovery and local tracking orchestration
exist (M9). Visual team identity, temporal matchup confirmation, and durable
confirmed matchup persistence exist (M10). A read-only FastAPI + SSE delivery
boundary exists (M11). A React scoreboard/detail consumer exists (M12).

## Milestone sequence

| # | Milestone | Status |
| --- | --- | --- |
| M0 | Repo + backend frame ingestion feasibility | **PASS, verified** |
| M1 | Real Madden recording ingestion + recorded-video sampling | **PASS, verified** |
| M2 | Channel-bound calibration foundation | **PASS, verified** |
| M3 | Calibrated scoreboard-presence detection | **PASS, verified** |
| M4 | Continuous frame acquisition for a single live broadcast | **PASS, verified** |
| M5 | Cross-matchup scoreboard-detector validation | **PASS, verified** |
| M6 | Numeric score reading foundation | **PASS, verified** |
| M7 | Temporal score validation + trusted live score state | **PASS, verified** |
| M8 | Durable game / trusted-score persistence + restart recovery | **PASS, verified** |
| M9 | YouTube broadcast discovery + tracking-session orchestration | **PASS, verified** |
| M10 | Team / opponent identity + temporal matchup + durable matchup | **PASS, verified** |
| M11 | API (REST / live updates) | **PASS, verified** |
| M12 | Frontend scoreboard and game pages | **PASS, verified** |
| M12.5A | YouTube OAuth channel connection | **PASS, verified** |

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

## M3 status: PASS

Goal: decide from a single frame whether a channel's calibrated scoreboard is
actually visible, so later milestones never read scores off unrelated graphics.

- **DETECTOR: VERIFIED** (`lsgc.vision`) — 40/40 correct on the labelled set
- **M2 ANCHORS REVALIDATED: BOTH RETAINED** on 40 samples, not the 2 M2 used
- **HARD NEGATIVES REJECTED: VERIFIED** (3 TOUCHDOWN banners, 1 EXTRA POINT)
- **1080p VIA 720p REFERENCE: VERIFIED** on real 1920x1080 frames, no retuning
- **THRESHOLD: MEASURED**, not guessed — any cut in (-0.08, 0.83) is perfect here

Single-frame only. No temporal debouncing, no OCR, no team or score reading.

## M4 status: PASS

Goal: keep one explicitly supplied live YouTube broadcast open and emit sampled
`Frame` objects until the broadcast ends, the caller cancels, or retries are
exhausted.

- **SESSION API + STATE MACHINE: VERIFIED OFFLINE** (`lsgc.live.LiveFrameSession`)
- **RECOVERY / RE-RESOLUTION: VERIFIED OFFLINE** (scripted resolver + decoder)
- **DUPLICATE / ORIGIN-RESET HANDLING: VERIFIED OFFLINE**
- **CANCELLATION: VERIFIED OFFLINE** (running and during backoff) **and** on the
  1200s live duration bound
- **M3 CONSUMER PATH: VERIFIED OFFLINE** and on live Frames
- **BASELINE LIVE ACQUISITION: VERIFIED** (`PWw6gEH32bo`)
- **NORMAL BROADCAST-END: VERIFIED** (`nI4DUFbEvrQ`)
- **CONTROLLED DISRUPTION / RECOVERY: NOT YET VERIFIED** on a real stream

These are three separate claims. Baseline acquisition and normal broadcast-end
passed on user-controlled PRISM/YouTube livestreams. Controlled user-induced
disruption/recovery has not been run and is **not** implied by either PASS.

### Real live evidence

**1. Baseline live acquisition — PASS** (`PWw6gEH32bo`, 1200s probe, interval 2s).
Started while Madden was on the menu; user entered Madden 27 Arcade during the
run. 586 frames (seq 0–585), ordered, no duplicates, skipped=0, failures=0.
M3 consumed every live Frame. Menu seq 0–44 ABSENT, seq 45 first PRESENT
(`conf +0.997`); later isolated ABSENT frames during gameplay were not visually
explained by the log. No fatal acquisition failure. Duration bound cancelled
cleanly (`final_state=cancelled`, process exit 0). Longest wall-clock gap
**51.96s** at seq 173→174 with a **second watch-URL resolve** (epoch 1→2);
frames then continued PRESENT. That gap is a spontaneous live-HLS stall /
re-resolution during an otherwise uninterrupted run. **It is not a controlled
reconnect test.**

**2. Normal broadcast-end — PASS** (`nI4DUFbEvrQ`, `--duration 0`). Session
first resolved `is_live=True`, reached RUNNING, and emitted 34 live frames.
User ended PRISM/YouTube normally. Decoder stalled 30s after media stopped;
session re-resolved the **same** stable watch URL (`resolves` 1→2); resolver
observed `is_live=False`; session transitioned to **ENDED** without manual
cancellation. FFmpeg/child cleanup succeeded. Diagnostics redacted CDN URLs
(`<redacted-media-url>`). Broadcast end did not mark a game score final.

**3. Controlled user-induced disruption/recovery — NOT YET VERIFIED.** Offline
scripted recovery still holds. Do not treat the 51.96s baseline gap or the
broadcast-end stall as that test.

Health counters on these runs: `resolves` (`resolve_attempts`) counts watch-URL
resolutions, including the initial resolve and later re-resolutions of the same
stable URL. `reconnects` (`reconnect_count`) stayed **0** on both real live
runs even when `resolves` went 1→2. Do not read `reconnects=0` as “no
re-resolution occurred.”

## M5 status: PASS

Goal: find out whether the existing M3 channel-calibrated scoreboard detector
recognizes the same Madden 27 Arcade scoreboard across **different matchups**
on the **same** calibrated channel, without matchup-specific visual content.

- **UNCHANGED M3 DETECTOR: VERIFIED** on four Super Season 7 archives
- **PATH A: NO DETECTOR / THRESHOLD / ANCHOR CHANGES**
- **ORIGINAL M3 33/7 REGRESSION: 40/40** still perfect at threshold 0.55
- **ANCHOR 0 / LEFT PANEL: MEASURED**, not assumed — team color moves a0
  slightly; it does not approach the decision boundary
- **SCORE / IDENTITY GEOMETRY: ALIGNED** on the four games (readiness only; no OCR)

Controlled live disruption/recovery remains unverified (M4 leftover). Score
reading is M6.

### Real cross-matchup evidence

Playlist `PLNh37ICHy5Cw` (“49ers - Super Season 7”), 60 entries, all channel
`UCz3q0f-dMPNIyQhntPTE7gQ`. Four games frozen **before** detector evaluation
for visual diversity, not for scores:

| key | video ID | matchup | why |
| --- | --- | --- | --- |
| chargers | `G2kMfIDFNIQ` | Chargers @ 49ers | navy bolt on **left** panel |
| packers | `A8Qd1dKHKUE` | 49ers @ Packers | green/gold G on **right** panel |
| raiders | `wDbkLLJT4i8` | Raiders @ 49ers | black/silver on **left** panel |
| vikings | `UITXyHeI5KE` | Vikings @ 49ers | purple horns on **left** panel |

Original fixture `hBblAZD-ApY` (49ers @ Giants) and M4 test streams
`PWw6gEH32bo` / `nI4DUFbEvrQ` were excluded. 99 manually labelled frames
(87 present / 12 absent) at 1280×720. Unchanged detector (profile
`madden27-arcade-prismlive`, reference from `hBblAZD-ApY` t=240/1500,
threshold **0.55**, min-of-anchors): **TP=87 TN=12 FP=0 FN=0**. Combined
positives +0.884…+0.997; combined negatives −0.280…+0.398; gap **+0.486**.

M4 live labels were not reused.

## M6 status: PASS

Goal: read the numeric LEFT and RIGHT scores displayed by the calibrated
Madden 27 Arcade scoreboard, as a per-frame **observation**. A single read is
never treated as a confirmed score change.

- **DETECTOR-GATED READER: VERIFIED** (`lsgc.score`) — ABSENT frames emit no
  observation; PRESENT frames crop calibrated score regions
- **HELD-OUT REAL VALIDATION: 222/222 SIDES EXACT** on 111 frames across five
  recordings, 0 incorrect numeric reads, 0 unreadable
- **1080p VIA 720p GLYPHS: VERIFIED** on four real 1920x1080 frames
- **M3/M5 DETECTOR UNCHANGED: 40/40 and 99/99** still perfect

Not trusted score state. No temporal confirmation, persistence, teams, clock,
or API.

## M7 status: PASS

Goal: consume a chronological stream of noisy M6 `RawScoreObservation` objects
and maintain a conservative **trusted visual score**. A raw OCR/read never
directly overwrites trusted state.

- **STATE MACHINE: VERIFIED OFFLINE** (`lsgc.track.TemporalScoreValidator`)
- **INITIALIZATION REQUIRES CONFIRMATION: VERIFIED** — no inferred 0-0
- **ONE-FRAME ERROR CANNOT MOVE TRUSTED STATE: VERIFIED** (injected wrong reads)
- **ABSENCE / GAP / RECONNECT PRESERVE TRUSTED SCORE: VERIFIED**
- **ACQUISITION END IS NOT GAME-FINAL: VERIFIED** (`frozen`, no FINAL)
- **EXPLICIT RESET: VERIFIED**
- **REAL ARCHIVED REPLAY: VERIFIED** on the five M6 recordings (sparse labels
  + dense 2s clusters). **0 false trusted transitions** on sampled sequences.
  This is not game-wide accuracy.

TRUSTED means temporally confirmed visual score. It does **not** mean official
score, final score, persisted score, or manually verified score.

| Module | Purpose |
| --- | --- |
| `policy.py` | `ConfirmationPolicy` — the only confirmation knobs |
| `models.py` | `TrustedScoreState`, `CandidateScore`, `ConfirmedScoreChange`, `ApplyResult` |
| `validator.py` | `TemporalScoreValidator` — deterministic apply / absence / freeze / reset |
| `cli.py` | `replay` (JSON, no video) and thin `live` probe |

```python
from lsgc.track import ConfirmationPolicy, TemporalScoreValidator

validator = TemporalScoreValidator()
result = validator.apply(observation)   # RawScoreObservation from M6
# result.outcome, result.state, result.candidate, result.change
validator.note_absence(frame_index=..., timestamp_s=..., source_epoch=...)
validator.end_acquisition()  # freeze; not FINAL
validator.reset()            # explicit new game
```

```bash
./.venv/bin/python -m lsgc.track replay sequence.json
python tools/m7_validate.py            # sparse M6 labels, gap restart off
python tools/m7_fixture.py capture     # needs YouTube; bounded 2s clusters
python tools/m7_validate.py --dense    # default live policy
```

A representative confirmed event (in-memory, not persisted):

```
ConfirmedScoreChange(
    revision=2,
    previous_left=14, previous_right=7,
    new_left=21, new_right=7,
    evidence_count=2,
    first_seen_timestamp_s=4.0, first_seen_frame_index=2,
    confirmed_timestamp_s=6.0, confirmed_frame_index=3, confirmed_epoch=1,
    initialization=False,
)
```

### Confirmation policy

`ConfirmationPolicy` is the only knob set. Defaults:

| parameter | default | why |
| --- | --- | --- |
| `required_observations` | **2** (minimum allowed) | one isolated observation must never become trusted; two consistent fully-readable pairs is the smallest rule that satisfies that |
| `min_span_s` | **2.0** | M4 developer sampling cadence; two frames that share a timestamp are not independent evidence |
| `max_candidate_gap_s` | **8.0** | four missed 2s samples; matching evidence after a longer hole restarts at count 1 rather than chaining across a discontinuity. `None` disables the time-gap restart (used only for sparse labelled replay) |

No football increment/monotonicity rules. M6 confidence/margin is carried on
the observation and is **not** multiplied into a fake probability.

### Candidate semantics

A candidate is one proposed pair. Evidence is **consecutive** consistent
fully-readable observations of that pair, in order:

- **start** — first fully-readable pair that is not the current trusted pair
- **accumulate** — next matching pair within `max_candidate_gap_s`
- **confirm** — count ≥ `required_observations` and source-time span ≥ `min_span_s` (or distinct `frame_index` if timestamps are missing)
- **replace** — a different fully-readable pair; previous evidence is discarded
- **reset** — trusted value reappears, or a discontinuity/gap; candidate cleared, trusted unchanged
- **partial / unreadable** — diagnostic only; never fabricates the missing side; never confirms a pair

Non-consecutive appearances of the same wrong value do not accumulate.

### Ordering, duplicates, gaps

- Duplicate: same `frame_index`, or same `(source_epoch, timestamp_s)`. Ignored; does not count as evidence.
- Stale: lower `frame_index`, lower `source_epoch`, or earlier PTS in the same epoch. Ignored; cannot regress trusted state.
- `source_epoch` increase or PTS hole > `max_candidate_gap_s`: **discontinuity**. Candidate evidence restarts. Trusted score is kept.
- Acquisition `end_acquisition()`: freeze. Later observations are ignored. No FINAL / winner / completed-game fields exist.
- `reset()`: explicit new-game boundary. Not inferred from 0-0, reconnect, or a long absence.

### Real archived replay

Two paths, both offline once frames exist. pytest never downloads video.

**Sparse labelled path** (`python tools/m7_validate.py`): M6 visual labels as
observations, `max_candidate_gap_s=None` because those offsets are tens of
seconds apart. Measures confirmation on consecutive labelled pairs of the
**same** score, not live 2s cadence. A score that appears only once in the
label set (Giants `14-7` at t=350) does not confirm — that is sampling, not a
claim the score was not on screen. Latency here is label spacing.

| recording | video | obs | init | changes | starts | replacements | false transitions | last trusted |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| giants | `hBblAZD-ApY` | 33 | 1 | 8 | 9 | 8 | 0 | 62-35 |
| chargers | `G2kMfIDFNIQ` | 23 | 1 | 4 | 5 | 10 | 0 | 24-56 |
| packers | `A8Qd1dKHKUE` | 21 | 1 | 4 | 6 | 4 | 0 | 35-7 |
| raiders | `wDbkLLJT4i8` | 21 | 1 | 7 | 8 | 3 | 0 | 10-49 |
| vikings | `UITXyHeI5KE` | 22 | 1 | 4 | 5 | 9 | 0 | 17-56 |

**Dense 2s-cluster path** (`python tools/m7_fixture.py capture` then
`python tools/m7_validate.py --dense`): three bounded seeks at t, t+2, t+4
around one init score and one real change per recording. Default live policy.
M6 reads of every cluster frame matched the expected visible pair; Giants
t=42/352/382 and Chargers t=362 / Vikings t=237 were also checked by eye.
Confirmation latency **2s / 2 observations** on every confirm. Jumps between
clusters (e.g. 0-0 → 14-7) are missed-intermediate recoveries, not claims those
games never showed 7-7.

| recording | clusters (verified visible) | confirmed | latency | false |
| --- | --- | --- | --- | --- |
| giants | 0-0 @40, 14-7 @350, 14-14 @380 | 0-0 → 14-7 → 14-14 | 2s | 0 |
| chargers | 0-0 @40, 7-14 @360, 7-21 @415 | 0-0 → 7-14 → 7-21 | 2s | 0 |
| packers | 0-0 @40, 7-0 @160, 14-0 @350 | 0-0 → 7-0 → 14-0 | 2s | 0 |
| raiders | 0-0 @40, 3-7 @250, 3-14 @360 | 0-0 → 3-7 → 3-14 | 2s | 0 |
| vikings | 0-0 @40, 0-7 @120, 0-14 @235 | 0-0 → 0-7 → 0-14 | 2s | 0 |

Do not read either table as full-game tracking.

## M8 status: PASS

Goal: persist M7 confirmed visual scores and history in PostgreSQL so a
process restart recovers trusted state without duplicating events or restoring
unconfirmed candidates.

- **SCHEMA + MIGRATIONS: VERIFIED** from an empty `lsgc_test` database
- **IDENTITY: VERIFIED** unique `(platform, external_broadcast_id)` = YouTube video id
- **NO IMPLIED 0-0: VERIFIED** on game create
- **ATOMIC CONFIRMATION: VERIFIED** history append + current score in one transaction
- **IDEMPOTENT RETRY / STALE / CONFLICT: VERIFIED**
- **WRITE FAILURE ROLLS BACK BOTH SIDES: VERIFIED** (injected)
- **RESTART RECOVERY: VERIFIED** new connection + `DurableScoreTracker.open`
- **CANDIDATE NOT RESTORED: VERIFIED**
- **NEW PROCESS Frame.index NOT STALE: VERIFIED**
- **ACQUISITION_ENDED ≠ FINAL: VERIFIED**
- **FIVE-RECORDING LABEL REPLAY: VERIFIED** persist, restart, continue; 0 false transitions

**Persisted trusted score = temporally confirmed visual score.** Not official,
corrected, final, or winner-certified.

| Module | Purpose |
| --- | --- |
| `persist/models.py` | `Game`, `TrackingStatus` (`TRACKING` / `ACQUISITION_ENDED`) |
| `persist/repository.py` | SQL; `persist_confirmation` is one transaction |
| `persist/tracker.py` | `DurableScoreTracker`: M7 apply + persist + reload-on-write-failure |
| `persist/migrate.py` | versioned `migrations/*.sql` |
| `track/validator.py` `restore` | durable confirmed state only; empty runtime ordering cursors |

```bash
docker compose -f docker-compose.postgres.yml up -d
DATABASE_URL=postgresql://lsgc:lsgc_dev@127.0.0.1:54329/lsgc \
  ./.venv/bin/python -m lsgc.persist migrate
LSGC_TEST_DATABASE_URL=postgresql://lsgc:lsgc_dev@127.0.0.1:54329/lsgc_test \
  python tools/m8_validate.py
```

Stack: PostgreSQL **16.14** (compose `postgres:16-alpine`), **psycopg 3.3.6**,
plain SQL migrations (no ORM). Chosen because the repo had no database layer
and M8 is two tables plus transactions.

Restart ordering: M7 `_last_index` / `_last_ts` / `_last_epoch` are **runtime
session** cursors. `restore()` sets them to `None`. Persisted
`confirmed_frame_index` is event metadata only. A new `LiveFrameSession` may
restart `Frame.index` at 0 without those observations being `IGNORED_STALE`.

Write-failure: on persist exception, `DurableScoreTracker` reloads the
validator from PostgreSQL so memory cannot stay ahead of durable state.

### Archived replay persistence (sparse M6 labels)

Restart = destroy tracker, new connection, `DurableScoreTracker.open`.
Candidate immediately after reload was **empty** on all five recordings.

| recording | obs | split | before restart | events before | after continuation | events after | false |
| --- | --- | --- | --- | --- | --- | --- | --- |
| giants `hBblAZD-ApY` | 33 | 16 | 21-14 r4 | 4 | 62-35 r9 | 9 | 0 |
| chargers `G2kMfIDFNIQ` | 23 | 11 | 7-14 r3 | 3 | 24-56 r5 | 5 | 0 |
| packers `A8Qd1dKHKUE` | 21 | 10 | 14-0 r3 | 3 | 35-7 r5 | 5 | 0 |
| raiders `wDbkLLJT4i8` | 21 | 10 | 3-14 r4 | 4 | 10-49 r8 | 8 | 0 |
| vikings `UITXyHeI5KE` | 22 | 11 | 0-14 r3 | 3 | 17-56 r5 | 5 | 0 |

Not full-game tracking. Continuation used a new `Frame.index` origin.

## M9 status: PASS

Goal: discover YouTube broadcasts for an **explicit connected channel**, keep
durable schedule/lifecycle state, and start **exactly one local tracking
worker** per eligible LIVE broadcast without any viewer.

- **CONTROL PLANE: VERIFIED OFFLINE** (`lsgc.broadcast`) — YouTube Data API v3
  behind `BroadcastCatalog`; pytest uses a fake client
- **EXACT VIDEO ID IDENTITY: VERIFIED** scheduled → live updates the same row
- **ALREADY-LIVE / UNSCHEDULED-LIVE: VERIFIED OFFLINE**
- **PAGINATION: VERIFIED OFFLINE** (`nextPageToken`, not `pageInfo.totalResults`)
- **API FAILURE DOES NOT END BROADCASTS: VERIFIED OFFLINE**
- **ELIGIBILITY + CHANNEL CALIBRATION: VERIFIED OFFLINE**
- **SUPERVISOR 0-OR-1 WORKER: VERIFIED OFFLINE**
- **NO VIEWER: VERIFIED OFFLINE** — `reconcile_once()` starts workers
- **RESTART RECONCILIATION: VERIFIED OFFLINE** empty registry then one worker
- **POSTGRESQL LIFECYCLE: VERIFIED** migration `002_broadcast_lifecycle.sql`
- **REAL YOUTUBE DATA API: NOT RUN** — no API key/OAuth token was present in
  this environment. Live tracking pipeline on a real stream was not re-run;
  M4 already proved live acquisition.

**YouTube ENDED / M4 acquisition ENDED ≠ GAME FINAL.**

| Module | Purpose |
| --- | --- |
| `broadcast/youtube.py` | Data API v3 HTTP client + payload normalization |
| `broadcast/sync.py` | Idempotent upsert by exact broadcast ID |
| `broadcast/eligibility.py` | LIVE + tracking_enabled + channel calibration |
| `broadcast/calibration.py` | Lookup by exact `channel_id` only |
| `broadcast/supervisor.py` | In-process 0-or-1 worker registry |
| `broadcast/worker.py` | M4 → M3/M6 → DurableScoreTracker |
| `broadcast/orchestrator.py` | One testable reconcile iteration + cancellable loop |
| `broadcast/cli.py` | `sync` / `run` |

```bash
./.venv/bin/python -m lsgc.broadcast sync
./.venv/bin/python -m lsgc.broadcast run
python tools/m9_validate.py   # optional; needs credentials; pytest never calls it
```

### Control plane vs data plane

- **Control plane:** `search.list` (`eventType=live|upcoming`) + `videos.list`
  (`snippet,liveStreamingDetails`) with an API key. If
  `LSGC_YOUTUBE_ACCESS_TOKEN` is set, `liveBroadcasts.list mine=true`
  `mine=true` alone is the official single filter (combining `mine` with
  `broadcastStatus` is `incompatibleParameters`); upcoming/live are kept and
  completed history is not imported,
  including unscheduled/persistent). OAuth scopes documented by Google:
  `youtube.readonly` / `youtube` / `youtube.force-ssl`.
- **Data plane:** unchanged M4 `LiveFrameSession` on
  `https://www.youtube.com/watch?v={id}`. yt-dlp is not the schedule database.

Quota (from Google's quota calculator / method docs, not guessed):

- `search.list`: own daily bucket, default **100 calls/day**, 1 per HTTP call
  including each extra page.
- `videos.list`: **1 unit** per call (up to 50 ids).
- `liveBroadcasts.list`: consumes Data API quota; the liveBroadcasts.list
  reference page does not state a unit cost, so none is claimed here.

Default sync interval **1800s** with an API key (two `search.list` event types
per cycle plus pagination). **120s** when an OAuth token is available.
A missing search hit is refreshed with `videos.list` for known LIVE/UPCOMING
ids; absence still does **not** infer ENDED.

### Exact identity and lifecycle

Durable key remains `(platform, external_broadcast_id)`. Migration 002 adds
`title`, `platform_lifecycle`, schedule/actual timestamps, `last_synced_at`,
`tracking_enabled`, worker error/finished fields. Trusted scores are never
written by metadata sync.

`tracking_enabled=false`: broadcast stays in the schedule; no automatic
worker; disabling an active worker stops it without deleting history and
without marking the broadcast ENDED.

Calibration path: `{LSGC_CALIBRATION_DIR}/{channel_id}/profile.json` plus
sibling `reference.json` and `glyphs.json`. Highest revision of a single
`profile_id` wins. Distinct profile ids for one channel are ambiguous →
NOT_READY. Foreign channel files are never used.

Worker failure (M4 FAILED): backoff `15s … 300s` at the supervisor; M4's
internal reconnect is not duplicated. M4 ENDED while YouTube still says LIVE
does not freeze trusted score as FINAL; later reconcile may retry. YouTube
ENDED stops the worker and sets `ACQUISITION_ENDED`.

Process restart: in-memory registry is empty; durable LIVE + eligible rows
get exactly one new worker; M8 restore still clears runtime ordering cursors.

### Offline / PostgreSQL evidence

Offline pytest after M9: **430 passed, 14 skipped** (postgres). Combined with
`LSGC_TEST_DATABASE_URL`: **444 passed**. M9 orchestration cases are in
`tests/test_broadcast_*.py`. PostgreSQL: scheduled→live same row, tracking
enabled + ended lifecycle survive a new connection, trusted history untouched
by metadata sync.

Real Data API enumeration was **not** executed here (no credentials in the
environment). Do not treat that gap as a live-ingestion failure; M4 already
passed on real streams.

## Verified commands

```bash
# setup
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"

# tests (offline + PostgreSQL when LSGC_TEST_DATABASE_URL is set; 444 passed)
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

## M3: calibrated scoreboard-presence detection

`lsgc.vision` consumes frame imagery plus a channel's calibration and answers
one question: is this channel's calibrated scoreboard on screen? It imports no
yt-dlp and no FFmpeg, so the ingestion boundary still holds.

| Module | Purpose |
| --- | --- |
| `reference.py` | `ScoreboardReference`: what normal stable structure looks like, plus JSON storage |
| `detector.py` | `ScoreboardDetector`, `DetectionResult`, `AnchorEvidence`, the threshold |
| `overlay.py` | Per-anchor diagnostic overlay |
| `cli.py` | `build-reference` / `show` / `detect` |
| `errors.py` | `VisionError`, `ReferenceSchemaError`, `ReferenceMismatchError` |

```bash
# build reference evidence from frames showing the normal scoreboard
./.venv/bin/python -m lsgc.vision build-reference PROFILE.json FRAME1.png FRAME2.png \
    --expect-channel UC... --out REFERENCE.json

# check one frame; exit 0 present, 3 absent, 2 error
./.venv/bin/python -m lsgc.vision detect PROFILE.json REFERENCE.json FRAME.png \
    --expect-channel UC... --overlay

# rebuild the validation frames (needs YouTube) and score the whole set
python tools/m3_fixture.py capture
python tools/m3_validate.py --sweep
```

### Validation set — FIXTURE METADATA ONLY

`tests/fixtures/m3_validation_set.json` holds **40 manually labelled samples**
from `hBblAZD-ApY`, established by inspecting each decoded frame's scoreboard
band, not by any automated rule. 76 frames were surveyed with bounded seeking
and curated down; the whole recording was never downloaded.

- **33 positives**: quarters 1-4; scores 0-0 through 62-35; kickoff, PAT,
  1st & 10, 2nd & Goal, 4th & Inches; with and without play-call menus;
  midfield and endzone backgrounds; both possession states. Includes one
  deliberately hard positive, the board mid fade-in at t=20.
- **7 negatives**: 3 pregame team-select menus and 4 hard negatives.
- **4 hard negatives**: three TOUCHDOWN banners (two blue/NY, one red/SF) and
  one EXTRA POINT banner. All occupy the scoreboard band and reuse the board's
  own divider chrome, so "something is there" would accept every one of them.

Categories the spec asked for that **do not exist** in this recording: replay,
halftime, quarter-break graphic and postgame. Quarters transition seamlessly
(t=550 shows 1st 0:06, t=565 shows 2nd 5:00) and the addressable fragments end
mid-play. Absence of a category is a property of this fixture, not evidence the
detector handles it.

The two frames the reference is built from (t=240, t=1500) are **excluded** from
the validation set, so the detector is never scored on the frames it was
derived from. A test enforces that.

Offsets identify frames for regression. They are not detector inputs and not
part of any `CalibrationProfile`.

### M2 anchor reevaluation — BOTH RETAINED

M2 chose its two anchors by differencing only two positive frames. Re-measured
across all 40 labelled samples (zero-normalised correlation against the
reference, lower of the intensity and structure measures):

| Anchor | Region | Positives (n=33) | Negatives (n=7) | Verdict |
| --- | --- | --- | --- | --- |
| 0, left panel divider | x=529 y=70 16x56 | 0.883..0.999, median 0.987 | -0.138..0.173 | **retained unchanged** |
| 1, right panel divider | x=734 y=72 18x52 | 0.825..0.999, median 0.996 | -0.107..0.230 | **retained unchanged** |

Neither anchor is contaminated by dynamic content: the weakest positives are
0.825 and 0.883, far above the strongest negatives at 0.230 and 0.173. Neither
was adjusted, because the evidence gives no reason to — and adjusting an anchor
that already separates cleanly would only risk overfitting to 40 frames. Two
anchors suffice; a third was not added because the combined gap is already
0.908 and every extra anchor is another thing to invalidate on recalibration.

These anchors belong to this channel's profile. They are not universal Madden
anchors.

### Reference evidence representation

`CalibrationProfile` says **where** to look; `ScoreboardReference` says **what**
it should look like there. They are separate objects with separate schema
versions.

A reference stores, per anchor, a **12x24 uint8 grayscale patch** averaged over
the representative frames, plus the anchor rectangle it was sampled from. Size
chosen by measurement: separation stops improving past roughly this grid
(12x24 gives a 0.908 gap, 16x32 gives 0.914 for nearly twice the data). Two
anchors is 576 integers, small enough to live in reviewable JSON next to the
profile, so **no binary crops are stored and no Madden imagery is committed**.
Averaging several frames keeps one frame's transient content from being baked
in as required structure.

The patches describe panel-divider chrome. They encode no team, score, clock,
colour or timestamp, and a test asserts the committed JSON mentions none.

Durable storage later: a profile and its reference are two small JSON documents
per channel revision, so whatever backs calibration in the persistence
milestone needs a blob or JSON column of a few tens of KB per revision, keyed by
channel and revision. Nothing here requires object storage.

### Detection method

Per anchor, the frame's crop is put on the descriptor grid and compared with the
stored patch two ways:

- **intensity similarity** — zero-normalised cross-correlation of grayscale,
  which ignores overall brightness and contrast;
- **structure similarity** — the same correlation on Sobel gradient magnitude,
  which responds to edge layout and, being unsigned, survives the panel behind
  a divider changing shade.

An anchor scores the lower of the two; the frame scores the lowest anchor. Taking
minima throughout means a frame is accepted only when **every** anchor matches on
**both** measures. That is deliberately conservative because a false positive
would later feed unrelated graphics to score reading.

No template matching over a search window, no feature matching, no ML. The
calibrated approach separates the set perfectly, so a model would be
unjustified. Nothing reads text, logos, scores or timestamps.

### Confidence and threshold

`DetectionResult.score` is the lowest anchor correlation, in [-1, 1], reported
alongside `threshold` and `margin`. Measured on the 40 samples at 1280x720:

| | n | min | median | max |
| --- | --- | --- | --- | --- |
| positives | 33 | +0.831 | +0.991 | +0.999 |
| negatives | 7 | -0.181 | -0.119 | **-0.077** |

Closest positive t=960 (+0.831, normal gameplay); closest negative t=360
(-0.077, TOUCHDOWN banner). Every threshold in **(-0.077, 0.831)** classifies
the set perfectly. `DEFAULT_THRESHOLD = 0.55` sits inside that plateau with
0.28 of headroom below the weakest positive and 0.63 above the strongest
negative, placed above the midpoint because false positives cost more.

This threshold was validated against one channel's calibration. It is not a
universal Madden constant.

### Real-fixture confusion matrix

```
                 predicted present   predicted absent
actual present                  33                  0
actual absent                    0                  7
```

TP 33, TN 7, FP 0, FN 0. Accuracy 1.0000, PRESENT precision 1.0000, PRESENT
recall 1.0000. No misclassifications. All four hard negatives rejected with
negative scores: t=125 (-0.181), t=360 (-0.077), t=370 (-0.116), t=1140
(-0.085).

**These are fixture-validation metrics on a small, manually selected set from
one channel. They are not an estimate of production accuracy.**

### Resolution handling

The detector rescales the profile with M2's deterministic rule and crops the
scaled anchor, then resamples onto the fixed descriptor grid — so resolution
normalisation is the descriptor itself, and no per-resolution threshold exists.
Real 1920x1080 frames of the same broadcast, scored against the 720p-derived
reference at the same threshold: t=780 +0.961 and t=1620 +0.964 present;
t=360 -0.081, t=1140 -0.099, t=5 -0.022 absent. All correct. Scores dip
slightly from 0.998 at 720p, which is expected resampling loss and is nowhere
near the threshold. 854x480 scores +0.915. Incompatible aspect ratios raise
`CalibrationValidationError` instead of being stretched.

### Channel safety

A reference records channel, profile lineage and profile revision, and
`assert_matches` also compares anchor geometry. `ScoreboardDetector` runs that
check once at construction, so a mismatched pair cannot reach a per-frame call,
and recalibrating invalidates a stale reference rather than silently comparing
the wrong pixels. `load_reference(path, expect_channel=...)` requires the
channel, matching `load_profile`.

## M4: continuous live acquisition

`lsgc.live.LiveFrameSession` coordinates resolver + decoder for **one**
explicitly supplied YouTube watch URL. The durable identity is that watch URL.
Resolved googlevideo/HLS URLs are ephemeral and are never the session identity.
The session does not import `lsgc.vision`; M3 consumes emitted `Frame`s.

```python
from lsgc.live import LiveFrameSession

session = LiveFrameSession(watch_url, interval_s=2.0)
for frame in session.frames():
    result = detector.detect_frame(frame)   # optional; session does not know
session.cancel()
```

### State machine

`STARTING` → `RUNNING` → (`RECOVERING` → `RUNNING`)* → `ENDED` | `FAILED` | `CANCELLED`

- **ENDED**: re-resolve shows the source is no longer live, or yt-dlp reports
  the live event has ended. Acquisition stops. **This is not a game-final.**
- **FAILED**: retry budget exhausted (`RetryPolicy.max_attempts`, default 8).
- **CANCELLED**: caller `cancel()`, duration timer, or generator closed early.

Health (`session.health`) is in-memory: frames emitted/skipped, resolve
attempts, reconnects, consecutive failures, last PTS, longest inter-frame gap,
sanitized last error. No database.

`resolve_attempts` (printed `resolves=`) counts every watch-URL resolution:
the initial resolve and later re-resolutions of the same stable URL.
`reconnect_count` (printed `reconnects=`) is a narrower counter of replacing a
working media connection after EOF or decode failure on a connection that had
already succeeded. On the real live runs it stayed 0 even when `resolves` went
from 1 to 2. Do not interpret `reconnects=0` as “no re-resolution occurred.”

### Sampling

Developer default **2.0s**, not a claimed optimum. FFmpeg `select` still does
the cadence so we do not ingest every 60-fps frame. `iter_frames` (bounded,
recorded seeking) is unchanged; live uses `iter_continuous_frames` (unbounded
until EOF/cancel/stall).

### Logical ordering and timestamps

`Frame.timestamp_s` is still source PTS — live HLS media-timeline offset, **not**
YouTube wall-clock. `Frame.received_at` is the only real-time clock.
`Frame.index` is session-monotonic among **emitted** frames.

After reconnect:

- PTS that does not advance past the last emitted PTS is dropped (duplicate /
  small reorder).
- PTS that jumps backward by more than one sample interval is a new media
  timeline origin (`source_epoch` increments) and is emitted as new
  progression, not as rewound time.

Downstream must not assume PTS is comparable across epochs.

### Recovery

On decode stall/error/EOF: backoff (1s, 2s, 4s, … cap 30s) and re-resolve the
**same watch URL**. A successful emission resets the consecutive-failure
counter. There is no tight infinite loop. Diagnostics redact googlevideo URLs,
signatures, and cookie-like headers.

A single FFmpeg EOF is **not** treated as broadcast-over. Only a subsequent
resolve that is not live, or an explicit "live event has ended" resolver
error, becomes ENDED. Ambiguous 403/timeout remains recoverable. Observed on
`nI4DUFbEvrQ`: stall after the user ended the stream, then re-resolve of the
same watch URL, `is_live=False`, ENDED.

### Cancellation

`session.cancel()` sets a thread-safe event. The decoder watchdog kills the
FFmpeg child. Backoff waits are `Event.wait`, so they return promptly. No
frames are emitted after cancel. Tests cover cancel while running and during
backoff.

## M5: cross-matchup presence detection

Same M3 detector, profile, reference, and 0.55 threshold. No new similarity
method. Validation frames live under gitignored `artifacts/m5/<key>/`.

```bash
python tools/m5_fixture.py capture     # needs YouTube
python tools/m5_validate.py
python tools/m3_validate.py            # original 33/7 regression
```

`tests/fixtures/m5_validation_set.json` holds 99 labels. Offsets are fixture
metadata only. A 1920x1080 subset of `UITXyHeI5KE` (t=40 present, t=100
TOUCHDOWN absent, t=600 present) was correct via existing profile scaling.

## M6: numeric score reading foundation

`lsgc.score` consumes a frame plus a channel's calibration, digit glyphs, and
an M3 detection result. It answers: what integers do the calibrated score
crops *appear* to show? It does not create trusted score state.

| Module | Purpose |
| --- | --- |
| `models.py` | `ScoreRead`, `RawScoreObservation`, `UnreadableReason` |
| `preprocess.py` | calibrated crop, min-channel white isolation, 8x cubic, projection split |
| `glyphs.py` | `DigitGlyphSet`: 18x28 grayscale patches for digits 0–9 |
| `reader.py` | `ScoreReader`: per-crop ZNCC match with an unreadable outcome |
| `pipeline.py` | `ScoreObservationPipeline`: M3 gate then independent L/R reads |
| `cli.py` | `build-glyphs` / `show` / `read` |

```bash
# gated read (exit 3 if ABSENT)
./.venv/bin/python -m lsgc.score read PROFILE.json GLYPHS.json FRAME.png \
    --reference REFERENCE.json --expect-channel UC...

python tools/m6_validate.py            # reuses gitignored M3/M5 frames
python tools/m6_validate.py --1080
```

### Recognition method

Compared on real score crops from this overlay:

- **Tesseract 5.5.1** (digits whitelist, PSM 7/8) after 8x upscale: empty on
  many single digits; 8/3 substitutions on `28`.
- **Binary connected-components + templates**: fragmenting 3/5/6/8 under
  bloom; 0 vs 6 confusion when templates were incomplete.
- **Chosen: min-channel localisation + grayscale digit ZNCC.** White ink is
  `min(B,G,R)`; boxes come from the column projection of an Otsu mask; the
  recogniser matches contrast-stretched grayscale patches, not the binary
  mask. Fits this scoreboard: 1–2 large rounded white numerals in a ~59x26
  crop, same font across matchups.

No full-frame OCR. No Tesseract/easyocr Python dependency. Runtime matching
never looks up a timestamp or video id.

Preprocessing stages that earned their keep on construction crops: min-channel
(team colour drops out), 8x cubic (native crops are tiny), Otsu+close for
localisation only, a 1px pad on the calibrated box. CLAHE, aggressive
binarisation, and topology/hole counting were tried and dropped — they
fragmented 3/5/8.

### Confidence

`ScoreRead.confidence` is the **minimum per-digit zero-normalised
correlation** in [-1, 1]. `margin` is the minimum gap from the best glyph to
the second-best. These are quality heuristics, not calibrated probabilities.

`DEFAULT_MIN_SCORE = 0.80`, `DEFAULT_MIN_MARGIN = 0.08`. Held-out true digit
matches scored ≥ 0.943 with gap ≥ 0.116. Forced ungated reads of menus and
TOUCHDOWN/EXTRA POINT banners topped out around 0.66 and are rejected. The
cut sits in that gap; it was not retuned per video.

`MAX_VALUE = 199` and at most 3 digit boxes are OCR-garbage bounds, not
football scoring rules. Unusual football values are accepted if the pixels
match.

### Detector gating

M3 decides presence. ABSENT → `observe()` returns `None` and does not read
scores. Ungated `ScoreReader.read_crop` on those same banner/menu crops
returns `readable=False` at the 0.80 cut rather than inventing a value.

### Glyph construction vs held-out validation

Nine `hBblAZD-ApY` frames (offsets 40, 130, 140, 350, 660, 1020, 1200, 1560,
1800) built the 0–9 glyphs and tuned split/preprocess. They are tagged
`glyph_construction` in `tests/fixtures/m6_score_validation_set.json` and
**excluded from held-out metrics**. Held-out: remaining labelled PRESENT
frames of that recording, plus every labelled PRESENT frame of the four M5
games. No M5 frame was used to build glyphs.

Labels are the visible left/right integers, confirmed on the calibrated
crops. Offsets are fixture metadata only.

### Held-out results (1280x720, threshold 0.80 / 0.08)

111 frames, 222 sides. **0 incorrect numeric reads.**

| recording | video | matchup | frames | unique pairs | L exact/inc/unr | R exact/inc/unr | both-exact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| giants | `hBblAZD-ApY` | 49ers @ Giants | 24 | 12 | 24/0/0 | 24/0/0 | 24 |
| chargers | `G2kMfIDFNIQ` | Chargers @ 49ers | 23 | 15 | 23/0/0 | 23/0/0 | 23 |
| packers | `A8Qd1dKHKUE` | 49ers @ Packers | 21 | 10 | 21/0/0 | 21/0/0 | 21 |
| raiders | `wDbkLLJT4i8` | Raiders @ 49ers | 21 | 11 | 21/0/0 | 21/0/0 | 21 |
| vikings | `UITXyHeI5KE` | Vikings @ 49ers | 22 | 14 | 22/0/0 | 22/0/0 | 22 |
| **held-out** | | | **111** | | **111/0/0** | **111/0/0** | **111** |

Side-level: exact 222, incorrect 0, unreadable 0, coverage 1.000, accuracy
among readable 1.000. Frame-level: both-exact 111, partial 0, any-wrong 0,
both-unreadable 0.

Packers has fewer unique score *pairs* (10) because Q2 was a long 21–0
stretch; the 21 labelled frames still cover 0 and 7–45, not copies of one
score. Construction-set frames (leaked) also read 18/18 exact and are not
counted above.

Coverage includes 0, single-digit, double-digit, repeated digits (e.g. 21-21,
7-7), PAT 6s, field-goal 3s, 13/17, 38/45/48/49/56/62, all four quarters in
the Giants recording, and five team-colour panels (red, navy, green, black,
purple).

**These are fixture-validation metrics on one channel. They are not an
estimate of production accuracy.**

### 1080p check

Existing 1920x1080 frames, 720p-derived profile scaling, same glyphs: Giants
t=780 28-21, t=1620 56-28; Vikings t=40 0-0, t=600 7-28. All exact
(conf ≥ 0.983).

### Error analysis

No held-out incorrect numeric read to inspect. Direct (ungated) reads of
ABSENT crops at the operating point were unreadable; the highest banner
correlation observed was 0.660 on a TOUCHDOWN left crop, below 0.80. M3
gating skipped all of those frames.

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

- **Two real live runs do not prove production reliability.** Verified: ~20
  minutes of continuous sampling on `PWw6gEH32bo`, and RUNNING→ENDED on
  `nI4DUFbEvrQ` after a normal PRISM/YouTube end. Still untested: multi-hour
  capture, **controlled** reconnect after an induced disruption, resolution
  changes mid-broadcast, ad insertion, DVR-window behaviour, and datacenter IPs
  (cloud IP ranges attract bot checks far more than a residential IP).
- **YouTube extraction is an adversarial moving target.** The 403 discovered
  during this milestone is exactly that risk. Pin yt-dlp, monitor upstream, and
  expect a broken extractor to be an operational event.
- **PO tokens / cookies may become necessary** on other networks. Not required
  here. If ever added, cookie jars must stay gitignored and out of commits.
- Live PTS is not broadcast wall-clock (see above).
- Bounded `lsgc.probe` still samples once and exits. Continuous capture is
  `lsgc.live` (M4, PASS).

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
  and remains an M4/M5 problem.

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

### M3-specific limitations

- **One channel, one game, 40 frames.** Perfect separation on this set is
  evidence the approach is sound here, not a production accuracy estimate. The
  next channel needs its own anchors, reference and threshold measurement.
- **Opponent-colour invariance is now measured on four extra matchups (M5).**
  Anchor 0 still sits on the left panel / centre-panel divider. Left-team color
  moves a0 (Chargers navy positives min +0.884 vs Packers 49ers-left median
  +0.997) but all 87 M5 positives stayed ≥ +0.884 against threshold 0.55.
  This is one channel's Arcade overlay, not a Madden-wide guarantee.
- **Categories missing from the fixture**: replay, halftime, quarter-break
  graphic, postgame. The detector's behaviour on them is unknown.
- **No temporal logic**, deliberately. A single dropped frame reads as absent,
  and debouncing belongs to the runtime milestone.
- **Reference frames were chosen by hand** (t=240, t=1500). There is no guidance
  yet on how many frames a good reference needs; two sufficed here.
- **Anchor geometry is fixed.** Nothing searches for a shifted board, so a
  capture whose overlay moves needs recalibration, not detection.
- The validation frames are gitignored, so `tools/m3_validate.py` needs a
  network capture step before it can run on a fresh clone.

### M4-specific limitations

- **Controlled user-induced disruption/recovery is not verified** on a real
  stream. Offline tests cover re-resolution. The 51.96s gap on `PWw6gEH32bo`
  (second resolve, then continuation) was spontaneous during baseline sampling,
  not that test. The stall→re-resolve→ENDED path on `nI4DUFbEvrQ` is normal
  broadcast-end handling, not an induced disruption.
- **`reconnects` stayed 0** on both live runs while `resolves` went 1→2. Use
  `resolves` for watch-URL re-resolution count; do not treat `reconnects` as
  that count.
- **Wall-clock delivery is bursty** on live HLS (typical ~5s catch-up gaps).
  PTS stayed at 2.000s while frames were flowing. The 51.96s hole on the
  20-minute run is the longest observed gap.
- **Live DASH fragment lists are not followed.** Continuous decode uses the
  playlist/media URL (YouTube live has been HLS in M0). If a live source is
  only DASH segments, decode fails and the session re-resolves.
- **PTS is still not wall-clock.** Session order is `Frame.index`, not PTS.
- **Broadcast ENDED is not a final score.** M4 stops acquisition only.
- **"Live event has ended" detection depends on yt-dlp wording** plus
  `is_live=False` on re-resolve. Ambiguous 403s are retried, not treated as
  ended. A **new** session against an already-ended URL (`PWw6gEH32bo` after
  it had become VOD) failed as never-live rather than ENDED, which is the
  designed never-saw-live path.
- **Default 2s cadence is a developer default**, not a measured optimum.
- **M3 labels on live frames are not a production accuracy estimate.** The
  20-minute run used the M1 fixture profile against live PRISM output; ten
  isolated ABSENT frames after gameplay started have no logged visual cause.

### M5-specific limitations

- **Four matchups on one channel, one capture layout.** Playlist Super Season 7
  PRISM Arcade overlay. Not universal Madden, not a second channel, not a
  different phone-capture crop.
- **Negatives are sparse relative to positives** (12 vs 87). Hard negatives
  found: SF TOUCHDOWN (packers t=420, vikings t=100), EXTRA POINT (raiders
  t=95 and t=465). Categories still thin or absent: replay, halftime graphic,
  postgame, quarter-break full-screen.
- **Anchor 0 still overlaps the left panel edge.** Team color is measurable
  there. The 0.55 threshold has headroom on these four games; a much brighter
  or noisier left panel is untested.
- **Raiders t=465 EXTRA POINT** scored combined +0.398 (a1 +0.619) because
  divider chrome remains while the banner covers the board. Min-of-anchors
  rejected it. Do not drop that combination rule.
- **No OCR in M5.** Score/identity boxes look geometrically correct; values
  were not read in that milestone (M6 reads scores).
- **M4 unlabelled live frames were not added** to this set.

### M6-specific limitations

- **Observations are not trusted score state.** One readable frame is not a
  confirmed score, not a score-change event, and not a game-final. Temporal
  confirmation is M7; persistence is M8; correction belongs later.
- **One channel, one overlay font.** Glyphs describe this PRISM Arcade score
  face. A different capture setup needs its own glyphs.
- **Held-out set is still a labelled convenience sample** (111 PRESENT
  frames). Perfect exact-match here is not a production accuracy estimate.
  Fade-in t=20 of the Giants recording was readable as 0-0; other transitions
  that M3 still calls PRESENT are untested.
- **No 3-digit score appeared** in the corpus. `MAX_VALUE=199` is an
  OCR-garbage cap, unexercised above 62.
- **Packers unique pairs are thinner** (10) because Q2 stayed 21-0 for a long
  stretch. Extra frames of that game would mostly duplicate 21-0.
- **Glyphs 3, 5, 9 were averaged from a single construction example each.**
  Held-out 3/5/9 still matched; a different stroke weight is untested.
- **Ungated reads of banners are rejected by the 0.80 cut**, but the
  production path must keep using M3 gating. Do not drop the detector and
  “just OCR the band.”
- **No team, logo, quarter, or clock reading.**

### M7-specific limitations

- **TRUSTED is not official, final, or human-verified.** It is temporally
  confirmed visual agreement on this overlay. A stable OCR error that lasts
  for two (or more) consecutive readable samples **will** become trusted; M7
  cannot prove a repeatedly seen value is false.
- **Sparse labelled replay is not live cadence.** M6 offsets are tens of
  seconds apart, so that path disables `max_candidate_gap_s`. Confirmation
  latency there is label spacing. Default-policy latency was measured only on
  the dense 2s clusters (nine frames per recording).
- **Dense clusters are three short windows per game**, not continuous
  tracking. Intermediate scores between clusters are not claimed. Giants
  sparse labels include a single `14-7` (t=350) that does not confirm until
  the dense cluster supplies a second 2s-apart sample.
- **No football-domain protection.** A visually stable decrease, skipped
  increment, or unusual pair will confirm if it meets the observation policy.
- **Candidate evidence is in-memory only.** M8 persists confirmed trusted
  score and history; unconfirmed candidates do not survive restart.
- **One explicit reset / one game per broadcast** remains the MVP assumption.
  Returning to 0-0, a reconnect, or a long absence does not start a new game.
- **Acquisition ENDED still does not mean the Madden game is final.**
- **M6 confidence is not a probability** and is not combined into one.
- Controlled real disruption/recovery remains unverified from M4.

### M8-specific limitations

- **Persisted trusted score is still not official, corrected, or final.**
- **Unconfirmed candidates are discarded on restart** by design. A score that
  had evidence=1 must be observed again.
- **One YouTube broadcast = one game.** M7 `reset()` does not delete SQL
  history; a new broadcast id is a new row.
- **No REST, WebSockets, frontend, teams, or corrections.**
- **Compose credentials are local examples** in `.env.example`, not a
  production secret store.
- Controlled real disruption/recovery remains unverified from M4.

### M9-specific limitations

- **No real YouTube Data API call was made in this M9 validation
  environment** (no `LSGC_YOUTUBE_API_KEY` / access token). Discovery
  semantics are implemented against the documented API and proven with
  scripted payloads. Live metadata (currently-live listing) was not
  observed on the development channel during M9.
- **No real live tracking run** was attached to M9 orchestration. M4 already
  proved LiveFrameSession on PRISM streams; M9 wires that session behind a
  supervisor using fakes in pytest.
- **API-key discovery uses `search.list`**, which Google documents as
  search-index based and quota-scarce (100/day). Index delay is why a missing
  hit is not treated as ENDED.
- **OAuth `liveBroadcasts.list` is implemented but unexercised** without a
  token. `broadcastType=all` is set because the default is `event` and would
  miss persistent/unscheduled lives.
- **Calibration is still files on disk**, not a durable registry/UI.
  `{dir}/{channel_id}/` is the M9 lookup. Multiple distinct `profile_id`s for
  one channel are refused rather than guessed.
- **Single-process only.** The supervisor is an in-memory dict, not a
  distributed lock. One `PostgresGameRepository` connection is serialized
  with a lock for worker threads.
- **No public API / no viewer UI.** `tracking_enabled` is persisted but has
  no owner UI yet.
- Completed/archived broadcasts are **not** polled every cycle (quota). ENDED
  is recorded when `videos.list` / `liveBroadcasts.list` provides end
  evidence, or when a discovered item is already ENDED.
- **YouTube ENDED still does not mean Madden game-final.**

## M10 status: PASS

Goal: recognize the teams actually shown on the gameplay scoreboard, confirm
the left/right pair over time, and persist that confirmed matchup so it
survives restart — without using title, schedule, playlist, or expected
opponents as recognition input.

- **IDENTITY ARCHITECTURE: VERIFIED** (`lsgc.identity`) — catalog, raw
  observation, matchup observation, temporal validator, durable tracker
- **LOGO RECOGNITION ON ARCADE LOGO FIXTURE: VERIFIED** held-out 235/238
  labelled sides correct, 0 incorrect, 3 UNKNOWN
- **M3 GATING: VERIFIED** 21/21 scoreboard-absent hard negatives produced no
  matchup observation
- **TEMPORAL CONFIRMATION: VERIFIED** offline A–O plus archived sparse and
  2s-cadence confirmation of the labelled pair on all five recordings
- **NO FALSE CONFIRMED MATCHUPS** on held-out archived frames
- **PERSISTENCE + RESTART: VERIFIED** offline and PostgreSQL
- **M9 WORKER DUAL PATH: VERIFIED** score and identity failures are independent
- **1080p: VERIFIED** on existing `artifacts/m3_1080` samples (3 present:
  2 both-correct, 1 detector-gated absent at t=360; confirmed SF/NYG)

**Confirmed matchup = temporally confirmed visual scoreboard identity.**
It is not official schedule truth, title-derived truth, or owner-verified
truth.

### Canonical IDs and catalog

`team_id` is the stable code (SF, NYG, GB, LAC, LV, MIN, …). Display names
are not identity. Left/right are scoreboard positions, not home/away.

Catalog support: 32 NFL teams. **Visually validated recognition: SF, NYG,
LAC, GB, LV, MIN** on this fixture. Do not read catalog size as coverage.

### Presentation routing

M2 identity regions for this channel are both `LOGO`. Architecture still
tries name/abbreviation first when those flags are set (exact aliases only).
This fixture cannot validate text OCR; `UnvalidatedTextReader` returns
UNKNOWN so logo fallback can run. Full-frame OCR is not used.

### Logo method

Compared on construction crops: grayscale 32×24 ZNCC, mean-BGR cosine, and
min(ZNCC, color). True combined scores ≥ ~0.87, false ≤ ~0.53, true margins
≥ ~0.34. Frozen thresholds: `min_score=0.70`, `min_margin=0.15`. Heuristic
similarity is not a calibrated probability.

Construction templates (excluded from held-out): Giants t=240 L=SF R=NYG;
Chargers t=40 L=LAC R=SF; Packers t=40 R=GB; Raiders t=40 L=LV; Vikings
t=40 L=MIN. Two SF patches because left and right crop geometry differ.
Provenance is documentation; runtime matching does not read video IDs.

### Held-out visual results (720p labelled set + 1080p extras)

Sides: labelled 238, correct 235, incorrect 0, UNKNOWN 3.
accuracy_all=0.987, accuracy_readable=1.000, coverage=0.987.

| team | n | correct | incorrect | UNKNOWN |
| --- | --- | --- | --- | --- |
| SF | 119 | 117 | 0 | 2 |
| NYG | 36 | 35 | 0 | 1 |
| LAC | 22 | 22 | 0 | 0 |
| GB | 20 | 20 | 0 | 0 |
| LV | 20 | 20 | 0 | 0 |
| MIN | 21 | 21 | 0 | 0 |

UNKNOWN: Packers t=1320 left SF combined 0.689 (below 0.70); Giants 1080p
t=360 labelled present but M3 ABSENT so identity did not run (counts as
UNKNOWN both sides). Thresholds were not relaxed.

Matchup/frame (720p held-out present): Giants 33/33 both-correct; Chargers
22/22; Packers 19 both + 1 partial; Raiders 20/20; Vikings 21/21; 1080p
2 both-correct + 1 both-unknown. Zero any-wrong frames.

Scoreboard-absent: 21 gated, 0 leaked (includes TOUCHDOWN / EXTRA POINT).

### Temporal policy

Default (live): `required_observations=2`, `min_span_s=2.0`,
`max_candidate_gap_s=8.0`. Justified by M4's 2s cadence: two in-order
complete pairs spanning at least one sample interval, with a gap reset so
scattered lifetime votes cannot accumulate.

Sparse labelled offsets are tens of seconds apart, so archived labelled
replay disables the gap (same as M7 sparse). Default policy was also run on
held-out 2s pairs t=42, t=44 for each 720p recording: all five confirmed the
labelled pair, no false confirmations.

Confirmation latency on sparse labels is label spacing (second present
frame), not live cadence. Live-cadence latency on t=42/44 is 2.0s / 2
observations.

After confirmation, a different complete pair increments `conflict_count`
and is persisted as `last_matchup_conflict`; the confirmed pair is not
rewritten.

### Persistence

Migration `003_confirmed_matchup.sql` (does not edit 001/002):

- `games.confirmed_left_team_id` / `confirmed_right_team_id` nullable
- `matchup_revision` 0 ⇒ both NULL; >0 ⇒ both non-NULL and left ≠ right
- team-id shape `^[A-Z]{2,3}$`
- append-only `confirmed_matchup_changes` unique `(game_id, revision)`

Creating a game row does not invent a matchup. Idempotent retry of the same
payload is `IDEMPOTENT`. A different pair after confirmation is
`ConflictingMatchupError`. Write failure reloads the validator from durable
state.

YouTube title remains metadata. Visual identity is not parsed from it.

### M10-specific limitations

- **Six visually validated teams**, not 32. Unobserved logos have no
  templates and would return UNKNOWN.
- **LOGO presentation only** is validated. Name/abbreviation OCR is an
  explicit unvalidated interface.
- **One channel, one Arcade overlay.** Another capture setup needs its own
  templates and threshold measurement.
- **Packers t=1320 left** is a true UNKNOWN at the frozen 0.70 cut. A stable
  fade/compression that stays below the cut will not confirm that side.
- **1080p t=360** was detector-gated; identity was not attempted. Not an
  arbitrary-resolution claim.
- **Confirmed matchup is not owner-corrected.** Later contradictory visual
  evidence surfaces CONFLICT; it does not recalibrate.
- **No final-score, winner, quarter, or clock reading.**
- **Sparse confirmation latency is label spacing.**

## M11 status: PASS

Goal: expose a product-facing read/delivery API over durable M8–M10 state
without letting clients own tracking.

- **REST SNAPSHOTS: VERIFIED** — `GET /api/games`, detail, score timeline
- **SSE PRODUCT EVENTS: VERIFIED** — snapshot-after-subscribe, score /
  matchup / lifecycle updates, bounded queues
- **RECONNECT / LOST-WAKEUP: VERIFIED** — durable snapshot after
  registration; score-events catch-up
- **VIEWER INDEPENDENCE: VERIFIED** — 0 / 1 / many / disconnect-all do not
  start or stop tracking
- **API WITHOUT YOUTUBE: VERIFIED** — `LSGC_RUN_ORCHESTRATOR=false`
- **POSTGRESQL AUTHORITY: VERIFIED** — pool reads + restart unchanged

### Architecture

```
Application
   +--> FastAPI (read-only)
   +--> optional M9 BroadcastOrchestrator (LSGC_RUN_ORCHESTRATOR)
PostgreSQL remains canonical. In-process GameEventHub notifies after
successful writes. Failures/slowness there cannot block persist.
```

No Redis, Kafka, Celery, GraphQL, ORM, or split API/realtime services.
No new event table. Catch-up is derived from `games`,
`confirmed_score_changes`, and matchup columns.

Concurrent API access uses `psycopg_pool` (`PooledGameRepository`: one
checkout per call). M9 may keep one serialized connection for workers.

### Semantics

- Game id = durable UUID. Also `platform=youtube` + `external_broadcast_id`.
- Score: `UNCONFIRMED` | `TRUSTED`. Never FINAL.
- Matchup: `UNCONFIRMED` | `CONFIRMED` | `CONFLICT`. Confirmed teams stay.
- Upcoming unconfirmed games return null teams and null scores (not 0-0).
- Ended broadcasts keep TRUSTED scores.
- Timestamps: ISO-8601 UTC (`Z`).
- Health: process alive vs database reachable. YouTube not required.

### Realtime

Transport: **SSE** (one-way server → client). Heartbeat comments are not
state. `sequence` is per-game and monotonic in-process. `cursor` /
`score_revision` identify stale or duplicate events.

Lost-wakeup: subscribe first, then emit `GAME_SNAPSHOT` from the current
row. A revision N snapshot followed by mutation N+1 before subscribe still
yields snapshot N+1.

Slow subscribers: queue max 32 (configurable). Overflow → `RESYNC_REQUIRED`
and disconnect. `put_nowait` never blocks the producer.

Process restart loses ephemeral hub messages. Clients recover from
PostgreSQL.

### Limitations

- No authentication. Local/development trust only. CORS is explicit origins.
- No accounts, owner writes, tracking-enable API, final scores,
  winners, standings, or notifications.
- In-process hub is single-process. Multiple API processes would not share
  ephemeral notifications (durable catch-up still works).
- SSE `sequence` resets on process restart; `cursor` and score revisions
  remain durable.

## M12 status: PASS

Goal: first viewer-facing scoreboard and game pages over M11 REST + SSE.

- **HOME SCOREBOARD: VERIFIED** — live / upcoming / ended from `GET /api/games`
- **GAME DETAIL: VERIFIED** — direct `/games/:id`, timeline, show/hide embed
- **SSE UPDATES: VERIFIED** — home card 28-14 → 35-14 and detail 35-14 → 56-21
  without reload against `tools/m12_demo_api.py`; stale/duplicate/RESYNC/unmount
  covered by Vitest
- **PRODUCTION BUILD: VERIFIED** — `npm run build` (tsc + vite)
- **FRONTEND TESTS: VERIFIED** — 32 passed
- **BROWSER VALIDATION: VERIFIED** — Vite `localhost:5173` + demo API
  `127.0.0.1:8000`; desktop and ~390px layouts; keyboard-focusable links/buttons

The browser is a consumer. Opening or closing pages does not start, stop, or
own tracking. Embed URLs are only `https://www.youtube.com/embed/{id}`.

Home SSE strategy: one EventSource per LIVE game; cleanup on unmount or when
the game leaves LIVE. Feed membership polls every 60s and on focus.

### Limitations

- No accounts, owner tools, calibration UI, standings, or FINAL semantics.
- Team logos are not loaded from the internet.
- Demo API seed is not production data.
- Controlled real disruption/recovery remains unverified from M4.

## M12.5A status: PASS

Real Google consent completed through the local app. Authorized channel
`UCz3q0f-dMPNIyQhntPTE7gQ` / NFL Super Season Full Videos. Existing Arcade
calibration matched automatically (READY). Stored Fernet-encrypted refresh
authorization survived backend restart and still called
`channels.list mine=true` and `liveBroadcasts.list mine=true`. Official
`liveBroadcasts.list` accepts exactly one of `mine` / `broadcastStatus` /
`id`; combining `mine` with `broadcastStatus` is `incompatibleParameters`.

## M12.5A notes (architecture)

Product path (primary):

Connect YouTube → Google OAuth (youtube.readonly) →
`channels.list mine=true` → persist exact channel + encrypted refresh →
calibration lookup by exact channel ID → authenticated
`liveBroadcasts.list mine=true` for that connection.

Three layers stay distinct: **application** OAuth client ID/secret,
**user** Google consent, **per-channel** visual calibration.

This is YouTube account connection only. There are still no application
accounts, passwords, or sessions. `owner_user_id` is reserved.

Brand Accounts: Google's OAuth picker plus `mine=true` expose the one
currently selected channel, not every channel a Google login can access.

Local disconnect clears stored credentials and best-effort revokes at
`https://oauth2.googleapis.com/revoke`. That is not the same as Google
showing the app as fully removed if revoke fails.

Official sources used:

- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps
- https://developers.google.com/youtube/v3/docs/channels/list
- https://developers.google.com/youtube/v3/docs/liveBroadcasts/list

PKCE is not implemented: the official confidential web-server guide does
not require it for this flow.

Real Google consent has **not** been completed. Do not treat M12.5A as PASS.

## Live product path: NOT YET PASSING

The intended chain is:

real scheduled YouTube stream → Data API discovery → LIVE → M9 worker →
M4 frames → M3/M6/M10 → M7 → M8 persist → M11 SSE → M12 home page update.

This has **not** been observed as one run. M12.5 preflight stopped because
production orchestration previously required a manual channel ID and API
key. M12.5A inserts OAuth connection first. Do not resume the real-live
M12.5 test until OAuth is authorized.

## Next action

Complete one real Google OAuth consent against the local M12 app, then
prove stored authorization can call authenticated `liveBroadcasts.list`.
Do not resume the real-live M12.5 stream test yet.

Explicitly **not** implied by M12 PASS: official/final/corrected score,
owner mutation APIs, application authentication, title-derived matchup,
32-team visual coverage, text-mode OCR, quarter/clock OCR, automatic
new-game detection, football increment rules, multi-host locking,
distributed event brokers, or a real YouTube → home-page update.

