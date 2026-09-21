import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, vi } from "vitest";
import { liveGame, timeline, upcomingGame } from "../test/fixtures";
import { GameDetailPage } from "./GameDetailPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockDetail(game = liveGame) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/score-events")) {
        if (game.score.revision === 0) {
          return jsonResponse({
            game_id: game.id,
            events: [],
            total: 0,
            limit: 100,
            offset: 0,
          });
        }
        return jsonResponse(timeline);
      }
      if (url.includes("/api/games/")) {
        return jsonResponse(game);
      }
      return jsonResponse({ detail: "missing" }, 404);
    }),
  );
  vi.stubGlobal(
    "EventSource",
    class {
      addEventListener() {}
      close() {}
    },
  );
}

function renderDetail(id = liveGame.id) {
  return render(
    <MemoryRouter initialEntries={[`/games/${id}`]}>
      <Routes>
        <Route path="/games/:gameId" element={<GameDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

test("detail shows confirmed teams and trusted score", async () => {
  mockDetail();
  renderDetail();
  expect(await screen.findByText("San Francisco 49ers")).toBeInTheDocument();
  expect(screen.getByText("Green Bay Packers")).toBeInTheDocument();
  expect(screen.getByText("21")).toBeInTheDocument();
  expect(screen.getByText("14")).toBeInTheDocument();
});

test("unconfirmed detail does not fabricate a score", async () => {
  mockDetail(upcomingGame);
  renderDetail(upcomingGame.id);
  expect((await screen.findAllByText("Unknown")).length).toBeGreaterThan(0);
  expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  expect(screen.queryByText("0 – 0")).not.toBeInTheDocument();
});

test("timeline lists confirmed revisions", async () => {
  mockDetail();
  renderDetail();
  expect(await screen.findByText("Start")).toBeInTheDocument();
  expect(screen.getByText("0 – 0")).toBeInTheDocument();
  expect(screen.getByText("21 – 14")).toBeInTheDocument();
});

test("embed show/hide uses broadcast id only", async () => {
  mockDetail();
  const user = userEvent.setup();
  renderDetail();
  const toggle = await screen.findByRole("button", { name: "Show stream" });
  await user.click(toggle);
  const frame = screen.getByTitle("YouTube broadcast");
  expect(frame).toHaveAttribute("src", "https://www.youtube.com/embed/hBblAZD-ApY");
  await user.click(screen.getByRole("button", { name: "Hide stream" }));
  expect(screen.queryByTitle("YouTube broadcast")).not.toBeInTheDocument();
});

test("unknown game shows not found", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse({ detail: "game not found" }, 404)),
  );
  vi.stubGlobal(
    "EventSource",
    class {
      addEventListener() {}
      close() {}
    },
  );
  renderDetail("00000000-0000-0000-0000-000000000000");
  expect(await screen.findByText("Game not found")).toBeInTheDocument();
});
