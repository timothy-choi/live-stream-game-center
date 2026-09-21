import { useEffect, useState } from "react";
import { getGame, getScoreEvents } from "../api/games";
import { mergeGame } from "../api/merge";
import { NotFoundError } from "../api/types";
import type { GameSummary, ScoreTimelineEvent } from "../api/types";
import { subscribeToGame, type ConnectionStatus } from "../realtime/subscribe";

export function useGameDetail(gameId: string | undefined) {
  const [game, setGame] = useState<GameSummary | null>(null);
  const [timeline, setTimeline] = useState<ScoreTimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");

  useEffect(() => {
    if (!gameId) {
      setNotFound(true);
      setLoading(false);
      return;
    }
    let cancelled = false;
    let current: GameSummary | null = null;

    const load = async () => {
      setLoading(true);
      setNotFound(false);
      try {
        const [snapshot, events] = await Promise.all([
          getGame(gameId),
          getScoreEvents(gameId).catch(() => {
            setTimelineError("Score history is temporarily unavailable.");
            return null;
          }),
        ]);
        if (cancelled) {
          return;
        }
        current = mergeGame(current, snapshot);
        setGame(current);
        if (events) {
          setTimeline(events.events);
          setTimelineError(null);
        }
        setError(null);
      } catch (exc) {
        if (cancelled) {
          return;
        }
        if (exc instanceof NotFoundError) {
          setNotFound(true);
          setGame(null);
        } else {
          setError("Unable to load this game. Last known score is kept if already shown.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void load();
    const subscription = subscribeToGame(gameId, {
      getCurrent: () => current,
      onGame: (incoming) => {
        current = mergeGame(current, incoming);
        setGame(current);
        if (incoming.score.revision > 0) {
          void getScoreEvents(gameId)
            .then((events) => {
              if (!cancelled) {
                setTimeline(events.events);
                setTimelineError(null);
              }
            })
            .catch(() => {
              if (!cancelled) {
                setTimelineError("Score history is temporarily unavailable.");
              }
            });
        }
      },
      onStatus: setStatus,
    });

    return () => {
      cancelled = true;
      subscription.close();
    };
  }, [gameId]);

  return { game, timeline, loading, notFound, error, timelineError, status };
}
