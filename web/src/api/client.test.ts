import { afterEach, expect, test, vi } from "vitest";
import { getGame, listGames, getScoreEvents } from "./games";
import { NotFoundError, ApiError } from "./types";
import { liveGame, timeline } from "../test/fixtures";

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("parses game list", async () => {
  const fetchImpl = vi.fn(async () => jsonResponse({ games: [liveGame], total: 1, limit: 50, offset: 0 }));
  const body = await listGames(fetchImpl);
  expect(body.games[0].id).toBe(liveGame.id);
  expect(JSON.stringify(fetchImpl.mock.calls)).toContain("/api/games?limit=50");
});

test("parses game detail", async () => {
  const fetchImpl = vi.fn(async () => jsonResponse(liveGame));
  const game = await getGame(liveGame.id, fetchImpl);
  expect(game.score.left).toBe(21);
});

test("parses timeline", async () => {
  const fetchImpl = vi.fn(async () => jsonResponse(timeline));
  const body = await getScoreEvents(liveGame.id, fetchImpl);
  expect(body.events.map((item) => item.revision)).toEqual([1, 2, 3]);
});

test("404 becomes NotFoundError", async () => {
  const fetchImpl = vi.fn(async () => jsonResponse({ detail: "game not found" }, 404));
  await expect(getGame(liveGame.id, fetchImpl)).rejects.toBeInstanceOf(NotFoundError);
});

test("server error", async () => {
  const fetchImpl = vi.fn(async () => jsonResponse({ detail: "internal server error" }, 500));
  await expect(getGame(liveGame.id, fetchImpl)).rejects.toBeInstanceOf(ApiError);
});

test("malformed json", async () => {
  const fetchImpl = vi.fn(async () => new Response("not-json", { status: 200 }));
  await expect(getGame(liveGame.id, fetchImpl)).rejects.toThrow("Malformed API response");
});
