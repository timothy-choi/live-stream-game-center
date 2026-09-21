import { expect, test, vi } from "vitest";
import { mergeGame } from "../api/merge";
import { conflictGame, gameEvent, liveGame, upcomingGame } from "../test/fixtures";
import { MockEventSource } from "../test/mockEventSource";
import { subscribeToGame } from "./subscribe";

test("GAME_SNAPSHOT populates game", () => {
  let current = null as typeof liveGame | null;
  const source = new MockEventSource("sse");
  subscribeToGame(
    liveGame.id,
    {
      getCurrent: () => current,
      onGame: (game) => {
        current = game;
      },
    },
    { eventSourceFactory: () => source },
  );
  source.emit("GAME_SNAPSHOT", JSON.stringify(gameEvent("GAME_SNAPSHOT", liveGame)));
  expect(current?.score.left).toBe(21);
});

test("SCORE_CONFIRMED updates score", () => {
  let current: typeof liveGame = {
    ...liveGame,
    score: { ...liveGame.score, left: 14, right: 14, revision: 2 },
  };
  const source = new MockEventSource("sse");
  subscribeToGame(
    liveGame.id,
    {
      getCurrent: () => current,
      onGame: (game) => {
        current = game;
      },
    },
    { eventSourceFactory: () => source },
  );
  source.emit("SCORE_CONFIRMED", JSON.stringify(gameEvent("SCORE_CONFIRMED", liveGame, 2)));
  expect(current.score).toEqual({ status: "TRUSTED", left: 21, right: 14, revision: 3 });
});

test("MATCHUP_CONFIRMED updates teams", () => {
  let current = upcomingGame;
  const source = new MockEventSource("sse");
  subscribeToGame(
    upcomingGame.id,
    {
      getCurrent: () => current,
      onGame: (game) => {
        current = game;
      },
    },
    { eventSourceFactory: () => source },
  );
  const confirmed = {
    ...upcomingGame,
    matchup: liveGame.matchup,
    cursor: "0:1:0:UPCOMING:TRACKING:1",
  };
  source.emit("MATCHUP_CONFIRMED", JSON.stringify(gameEvent("MATCHUP_CONFIRMED", confirmed)));
  expect(current.matchup.left_team?.id).toBe("SF");
});

test("MATCHUP_CONFLICT preserves confirmed teams", () => {
  const merged = mergeGame(liveGame, conflictGame);
  expect(merged.matchup.left_team?.id).toBe("SF");
  expect(merged.matchup.right_team?.id).toBe("GB");
  expect(merged.matchup.status).toBe("CONFLICT");
});

test("lifecycle change updates UI state", () => {
  const liveToEnded = { ...liveGame, platform_lifecycle: "ENDED" as const, cursor: "3:1:0:ENDED:TRACKING:1" };
  const merged = mergeGame(liveGame, liveToEnded);
  expect(merged.platform_lifecycle).toBe("ENDED");
  expect(merged.score.left).toBe(21);
});

test("tracking status change updates", () => {
  const interrupted = {
    ...liveGame,
    tracking_status: "ACQUISITION_ENDED" as const,
    cursor: "3:1:0:LIVE:ACQUISITION_ENDED:1",
  };
  expect(mergeGame(liveGame, interrupted).tracking_status).toBe("ACQUISITION_ENDED");
});

test("stale score revision cannot roll backward", () => {
  const stale = {
    ...liveGame,
    score: { status: "TRUSTED" as const, left: 7, right: 0, revision: 1 },
  };
  const merged = mergeGame(liveGame, stale);
  expect(merged.score.revision).toBe(3);
  expect(merged.score.left).toBe(21);
});

test("duplicate event is harmless", () => {
  const once = mergeGame(liveGame, liveGame);
  const twice = mergeGame(once, liveGame);
  expect(twice.score).toEqual(liveGame.score);
});

test("RESYNC_REQUIRED refetches snapshot", async () => {
  const source = new MockEventSource("sse");
  const fetchImpl = vi.fn(async () =>
    new Response(JSON.stringify({ ...liveGame, score: { ...liveGame.score, left: 28, revision: 4 } }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  let current = liveGame;
  subscribeToGame(
    liveGame.id,
    {
      getCurrent: () => current,
      onGame: (game) => {
        current = game;
      },
    },
    { fetchImpl: fetchImpl as unknown as typeof fetch, eventSourceFactory: () => source },
  );
  source.emit("RESYNC_REQUIRED", JSON.stringify({ type: "RESYNC_REQUIRED", payload: {} }));
  await vi.waitFor(() => {
    expect(current.score.left).toBe(28);
  });
  expect(JSON.stringify(fetchImpl.mock.calls)).toContain(`/api/games/${liveGame.id}`);
});

test("reconnect keeps last known state until fresh state arrives", () => {
  let current = liveGame;
  const source = new MockEventSource("sse");
  subscribeToGame(
    liveGame.id,
    {
      getCurrent: () => current,
      onGame: (game) => {
        current = game;
      },
    },
    { eventSourceFactory: () => source },
  );
  source.emit("error");
  expect(current.score.left).toBe(21);
});

test("unmount closes subscription", () => {
  const source = new MockEventSource("sse");
  const sub = subscribeToGame(
    liveGame.id,
    { getCurrent: () => liveGame, onGame: () => undefined },
    { eventSourceFactory: () => source },
  );
  sub.close();
  expect(source.closed).toBe(true);
});

test("two games update independently", () => {
  let a = liveGame;
  let b = { ...upcomingGame };
  const sourceA = new MockEventSource("a");
  const sourceB = new MockEventSource("b");
  subscribeToGame(
    a.id,
    {
      getCurrent: () => a,
      onGame: (game) => {
        a = game;
      },
    },
    { eventSourceFactory: () => sourceA },
  );
  subscribeToGame(
    b.id,
    {
      getCurrent: () => b,
      onGame: (game) => {
        b = game;
      },
    },
    { eventSourceFactory: () => sourceB },
  );
  sourceA.emit(
    "SCORE_CONFIRMED",
    JSON.stringify(
      gameEvent("SCORE_CONFIRMED", {
        ...liveGame,
        score: { status: "TRUSTED", left: 35, right: 14, revision: 4 },
      }),
    ),
  );
  expect(a.score.left).toBe(35);
  expect(b.score.status).toBe("UNCONFIRMED");
});
