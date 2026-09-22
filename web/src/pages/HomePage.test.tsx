import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, vi } from "vitest";
import { endedGame, liveGame, upcomingGame } from "../test/fixtures";
import { HomePage } from "./HomePage";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("home renders live upcoming and ended sections from the API", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/api/youtube/connections")) {
        return new Response(JSON.stringify({ connections: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(
        JSON.stringify({
          games: [liveGame, upcomingGame, endedGame],
          total: 3,
          limit: 50,
          offset: 0,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  vi.stubGlobal(
    "EventSource",
    class {
      addEventListener() {}
      close() {}
    },
  );
  render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Live now")).toBeInTheDocument();
  expect(screen.getByText("Upcoming")).toBeInTheDocument();
  expect(screen.getByText("Recently ended")).toBeInTheDocument();
  expect(screen.getAllByText("TBD").length).toBeGreaterThan(0);
  expect(screen.queryByText("FINAL")).not.toBeInTheDocument();
});
