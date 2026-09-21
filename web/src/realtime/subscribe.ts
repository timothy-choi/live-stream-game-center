import { eventsUrl } from "../api/client";
import { getGame } from "../api/games";
import { eventGame, mergeGame } from "../api/merge";
import type { GameEvent, GameEventType, GameSummary } from "../api/types";

export type ConnectionStatus = "connecting" | "live" | "reconnecting" | "resyncing";

export interface EventSourceLike {
  addEventListener(type: string, listener: (event: Event) => void): void;
  close(): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

const EVENT_TYPES: GameEventType[] = [
  "GAME_SNAPSHOT",
  "SCORE_CONFIRMED",
  "MATCHUP_CONFIRMED",
  "MATCHUP_CONFLICT",
  "PLATFORM_LIFECYCLE_CHANGED",
  "TRACKING_STATUS_CHANGED",
  "RESYNC_REQUIRED",
];

export interface GameSubscription {
  close(): void;
}

export function subscribeToGame(
  gameId: string,
  handlers: {
    onGame: (game: GameSummary) => void;
    onStatus?: (status: ConnectionStatus) => void;
    getCurrent: () => GameSummary | null;
  },
  options: {
    fetchImpl?: typeof fetch;
    eventSourceFactory?: EventSourceFactory;
  } = {},
): GameSubscription {
  const factory =
    options.eventSourceFactory ?? ((url: string) => new EventSource(url) as EventSourceLike);
  const fetchImpl = options.fetchImpl ?? fetch;
  const source = factory(eventsUrl(gameId));
  let closed = false;
  let sawError = false;

  const setStatus = (status: ConnectionStatus) => {
    handlers.onStatus?.(status);
  };

  const applyIncoming = (incoming: GameSummary) => {
    handlers.onGame(mergeGame(handlers.getCurrent(), incoming));
  };

  const resync = async () => {
    if (closed) {
      return;
    }
    setStatus("resyncing");
    try {
      const fresh = await getGame(gameId, fetchImpl);
      if (!closed) {
        applyIncoming(fresh);
        setStatus(sawError ? "reconnecting" : "live");
      }
    } catch {
      if (!closed) {
        setStatus("reconnecting");
      }
    }
  };

  const onPayload = (event: Event) => {
    const data = (event as MessageEvent).data;
    if (closed || typeof data !== "string" || data.length === 0) {
      return;
    }
    let parsed: GameEvent;
    try {
      parsed = JSON.parse(data) as GameEvent;
    } catch {
      return;
    }
    if (parsed.type === "RESYNC_REQUIRED") {
      void resync();
      return;
    }
    const incoming = eventGame(parsed);
    if (incoming) {
      applyIncoming(incoming);
    }
  };

  setStatus("connecting");
  source.addEventListener("open", () => {
    if (closed) {
      return;
    }
    if (sawError) {
      void resync();
    } else {
      setStatus("live");
    }
  });
  source.addEventListener("error", () => {
    if (closed) {
      return;
    }
    sawError = true;
    setStatus("reconnecting");
  });
  source.addEventListener("message", onPayload);
  for (const type of EVENT_TYPES) {
    source.addEventListener(type, onPayload);
  }

  return {
    close() {
      closed = true;
      source.close();
    },
  };
}
