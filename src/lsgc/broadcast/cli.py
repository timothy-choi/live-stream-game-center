"""Developer CLI for broadcast discovery and local tracking orchestration.

    python -m lsgc.broadcast sync
    python -m lsgc.broadcast run

Does not start a web server. Tracking is backend-owned.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from lsgc.broadcast.calibration import FilesystemCalibrationLookup
from lsgc.broadcast.config import load_runtime_config
from lsgc.broadcast.eligibility import evaluate_eligibility
from lsgc.broadcast.errors import BroadcastError
from lsgc.broadcast.orchestrator import orchestrator_from_config
from lsgc.broadcast.sanitize import sanitize_control_plane_text
from lsgc.broadcast.sync import ChannelSynchronizer
from lsgc.persist.config import database_url
from lsgc.youtube_oauth.catalog import merge_sync_channels
from lsgc.persist.migrate import apply_migrations, connect
from lsgc.persist.repository import PostgresGameRepository

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lsgc.broadcast",
        description=(
            "Discover YouTube broadcasts for configured channels and "
            "orchestrate local tracking workers. No REST/UI."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="one-shot metadata synchronization")
    sub.add_parser(
        "run",
        help="reconciliation loop: sync + ensure tracking workers",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            return _sync()
        if args.command == "run":
            return _run()
    except BroadcastError as exc:
        print(f"error: {sanitize_control_plane_text(str(exc))}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_USAGE


def _sync() -> int:
    config = load_runtime_config()
    dsn = database_url()
    with connect(dsn) as conn:
        apply_migrations(conn)
        conn.commit()
        store = PostgresGameRepository(conn)
        catalog, channels, lookup = _hybrid_catalog(config, store)
        if not channels:
            print("no connected YouTube channels", flush=True)
            return EXIT_OK
        sync = ChannelSynchronizer(catalog, store)
        for channel in channels:
            report = sync.sync_channel(channel.channel_id)
            _print_report(report)
            if not report.ok:
                return EXIT_ERROR
            games = store.list_games_for_channel(channel.channel_id)
            for game in games:
                decision = evaluate_eligibility(
                    game,
                    expected_channel_id=channel.channel_id,
                    calibration=lookup,
                )
                scheduled = (
                    game.scheduled_start_at.isoformat()
                    if game.scheduled_start_at
                    else "-"
                )
                print(
                    f"  {game.external_broadcast_id}  "
                    f"lifecycle={game.platform_lifecycle.value}  "
                    f"scheduled={scheduled}  "
                    f"tracking_enabled={game.tracking_enabled}  "
                    f"eligibility={decision.reason.value}",
                    flush=True,
                )
    return EXIT_OK


def _hybrid_catalog(config, store):
    from lsgc.youtube_oauth.catalog import HybridBroadcastCatalog, merge_sync_channels
    from lsgc.youtube_oauth.service import maybe_connection_service

    lookup = FilesystemCalibrationLookup(config.calibration_dir)
    service = maybe_connection_service(store, lookup)
    catalog = HybridBroadcastCatalog(
        store=store,
        service=service,
        developer_api_key=config.api_key,
        developer_access_token=config.access_token,
    )
    return catalog, merge_sync_channels(store, config.channels), lookup


def _run() -> int:
    config = load_runtime_config()
    dsn = database_url()
    stop = threading.Event()

    def _handle_signal(signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    with connect(dsn) as conn:
        apply_migrations(conn)
        conn.commit()
        store = PostgresGameRepository(conn)
        catalog, channels, lookup = _hybrid_catalog(config, store)
        orchestrator = orchestrator_from_config(
            config,
            store=store,
            catalog=catalog,
            calibration=lookup,
            channel_provider=lambda: merge_sync_channels(store, config.channels),
        )
        print(
            f"channels={','.join(ch.channel_id for ch in channels) or '-'} "
            f"interval_s={config.sync_interval_s:g} "
            f"(ctrl+c to stop)",
            flush=True,
        )
        orchestrator.run_forever(stop)
        status = orchestrator.status
        print(
            f"stopped sync_ok={status.last_sync_ok} "
            f"live={status.live_count} workers={status.active_worker_count}",
            flush=True,
        )
    return EXIT_OK


def _print_report(report) -> None:
    state = "ok" if report.ok else "ERROR"
    error = f" error={report.error}" if report.error else ""
    print(
        f"channel={report.channel_id} sync={state} "
        f"discovered={report.discovered} upserted={report.upserted} "
        f"rejected={report.rejected_wrong_channel} refreshed={report.refreshed} "
        f"live={len(report.live_ids)} upcoming={len(report.upcoming_ids)} "
        f"ended={len(report.ended_ids)}{error}",
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
