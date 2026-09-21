"""Idempotent channel broadcast synchronization.

Identity is the exact YouTube broadcast/video id. Titles and timestamps are
mutable metadata. A missing search result is not ENDED. An API failure must
not mutate durable rows.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from lsgc.broadcast.errors import YouTubeApiError
from lsgc.broadcast.models import (
    PLATFORM_YOUTUBE,
    DiscoveredBroadcast,
    PlatformLifecycle,
    SyncReport,
)
from lsgc.broadcast.sanitize import sanitize_control_plane_text
from lsgc.broadcast.youtube import BroadcastCatalog
from lsgc.persist.models import Game


class BroadcastStore(Protocol):
    def list_games_for_channel(self, channel_id: str) -> tuple[Game, ...]: ...

    def get_game_by_external_id(
        self, platform: str, external_broadcast_id: str
    ) -> Game | None: ...

    def upsert_broadcast_metadata(
        self,
        *,
        platform: str,
        external_broadcast_id: str,
        channel_id: str,
        title: str | None,
        platform_lifecycle: PlatformLifecycle,
        scheduled_start_at: datetime | None,
        actual_start_at: datetime | None,
        actual_end_at: datetime | None,
        synced_at: datetime,
    ) -> Game: ...


ClockFn = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class ChannelSynchronizer:
    def __init__(
        self,
        catalog: BroadcastCatalog,
        store: BroadcastStore,
        *,
        clock: ClockFn = _utc_now,
    ) -> None:
        self._catalog = catalog
        self._store = store
        self._clock = clock

    def sync_channel(self, channel_id: str) -> SyncReport:
        wanted = (channel_id or "").strip()
        if not wanted:
            return SyncReport(
                ok=False,
                channel_id="",
                discovered=0,
                upserted=0,
                rejected_wrong_channel=0,
                refreshed=0,
                skipped_unknown=0,
                error="channel_id must not be empty",
                synced_at=None,
                live_ids=(),
                upcoming_ids=(),
                ended_ids=(),
            )
        try:
            discovered = self._catalog.list_channel_broadcasts(wanted)
        except YouTubeApiError as exc:
            return SyncReport(
                ok=False,
                channel_id=wanted,
                discovered=0,
                upserted=0,
                rejected_wrong_channel=0,
                refreshed=0,
                skipped_unknown=0,
                error=sanitize_control_plane_text(str(exc)),
                synced_at=None,
                live_ids=(),
                upcoming_ids=(),
                ended_ids=(),
            )

        now = self._clock()
        upserted = 0
        rejected = 0
        skipped_unknown = 0
        accepted: list[DiscoveredBroadcast] = []
        for record in discovered:
            if record.channel_id != wanted:
                rejected += 1
                continue
            if record.lifecycle is PlatformLifecycle.UNKNOWN:
                skipped_unknown += 1
                continue
            existing = self._store.get_game_by_external_id(
                PLATFORM_YOUTUBE, record.broadcast_id
            )
            if (
                existing is not None
                and existing.channel_id
                and existing.channel_id != wanted
            ):
                rejected += 1
                continue
            self._upsert(record, now)
            upserted += 1
            accepted.append(record)

        refreshed = 0
        discovered_ids = {item.broadcast_id for item in accepted}
        stale_ids = [
            game.external_broadcast_id
            for game in self._store.list_games_for_channel(wanted)
            if game.platform_lifecycle
            in {PlatformLifecycle.LIVE, PlatformLifecycle.UPCOMING}
            and game.external_broadcast_id not in discovered_ids
        ]
        if stale_ids:
            try:
                extras = self._catalog.refresh_videos(stale_ids)
            except YouTubeApiError as exc:
                return SyncReport(
                    ok=False,
                    channel_id=wanted,
                    discovered=len(discovered),
                    upserted=upserted,
                    rejected_wrong_channel=rejected,
                    refreshed=0,
                    skipped_unknown=skipped_unknown,
                    error=sanitize_control_plane_text(str(exc)),
                    synced_at=now,
                    live_ids=_ids(accepted, PlatformLifecycle.LIVE),
                    upcoming_ids=_ids(accepted, PlatformLifecycle.UPCOMING),
                    ended_ids=_ids(accepted, PlatformLifecycle.ENDED),
                )
            for record in extras:
                if record.channel_id != wanted:
                    rejected += 1
                    continue
                if record.lifecycle is PlatformLifecycle.UNKNOWN:
                    skipped_unknown += 1
                    continue
                self._upsert(record, now)
                refreshed += 1
                accepted.append(record)

        return SyncReport(
            ok=True,
            channel_id=wanted,
            discovered=len(discovered),
            upserted=upserted,
            rejected_wrong_channel=rejected,
            refreshed=refreshed,
            skipped_unknown=skipped_unknown,
            error=None,
            synced_at=now,
            live_ids=_ids(accepted, PlatformLifecycle.LIVE),
            upcoming_ids=_ids(accepted, PlatformLifecycle.UPCOMING),
            ended_ids=_ids(accepted, PlatformLifecycle.ENDED),
        )

    def _upsert(self, record: DiscoveredBroadcast, synced_at: datetime) -> Game:
        return self._store.upsert_broadcast_metadata(
            platform=PLATFORM_YOUTUBE,
            external_broadcast_id=record.broadcast_id,
            channel_id=record.channel_id,
            title=record.title,
            platform_lifecycle=record.lifecycle,
            scheduled_start_at=record.scheduled_start,
            actual_start_at=record.actual_start,
            actual_end_at=record.actual_end,
            synced_at=synced_at,
        )


def _ids(
    records: Sequence[DiscoveredBroadcast], lifecycle: PlatformLifecycle
) -> tuple[str, ...]:
    return tuple(
        item.broadcast_id for item in records if item.lifecycle is lifecycle
    )
