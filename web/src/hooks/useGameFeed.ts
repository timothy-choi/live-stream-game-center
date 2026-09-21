import { useEffect, useMemo, useRef, useState } from "react";
import { feedRefreshMs } from "../api/client";
import { listGames } from "../api/games";
import { mergeGame } from "../api/merge";
import type { GameSummary } from "../api/types";
import { subscribeToGame, type ConnectionStatus } from "../realtime/subscribe";

function mergeFeed(current: GameSummary[], incoming: GameSummary[]): GameSummary[] {
  const byId = new Map(current.map((game) => [game.id, game]));
  return incoming.map((game) => mergeGame(byId.get(game.id) ?? null, game));
}

export function useGameFeed() {
  const [games, setGames] = useState<GameSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const gamesRef = useRef<GameSummary[]>([]);
  gamesRef.current = games;

  useEffect(() => {
    let cancelled = false;

    const refresh = async () => {
      try {
        const response = await listGames();
        if (cancelled) {
          return;
        }
        setGames((current) => mergeFeed(current, response.games));
        setError(null);
      } catch {
        if (!cancelled) {
          setError("Unable to load games. Tracking is still running on the backend.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void refresh();
    const interval = window.setInterval(() => {
      void refresh();
    }, feedRefreshMs());
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        void refresh();
      }
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const liveKey = useMemo(
    () =>
      games
        .filter((game) => game.platform_lifecycle === "LIVE")
        .map((game) => game.id)
        .sort()
        .join("|"),
    [games],
  );

  useEffect(() => {
    if (!liveKey) {
      return;
    }
    const liveIds = liveKey.split("|");
    const subscriptions = liveIds.map((gameId) =>
      subscribeToGame(gameId, {
        getCurrent: () => gamesRef.current.find((game) => game.id === gameId) ?? null,
        onGame: (incoming) => {
          setGames((current) =>
            current.map((game) => (game.id === incoming.id ? incoming : game)),
          );
        },
        onStatus: setStatus,
      }),
    );
    return () => {
      for (const subscription of subscriptions) {
        subscription.close();
      }
    };
  }, [liveKey]);

  return { games, loading, error, status };
}
