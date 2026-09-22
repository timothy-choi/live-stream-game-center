import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { YouTubeConnectionPanel } from "./YouTubeConnectionPanel";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("disconnected state shows Connect YouTube", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify({ connections: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  render(<YouTubeConnectionPanel />);
  expect(await screen.findByRole("link", { name: "Connect YouTube" })).toBeInTheDocument();
});

test("connected ready calibration does not show tokens", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({
          connections: [
            {
              id: "conn-1",
              provider: "youtube",
              youtube_channel_id: "UCz3q0f-dMPNIyQhntPTE7gQ",
              youtube_channel_title: "Arcade Channel",
              status: "CONNECTED",
              calibration_status: "READY",
              connected_at: "2026-09-21T20:00:00Z",
              last_refreshed_at: null,
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  render(<YouTubeConnectionPanel />);
  expect(await screen.findByText("YouTube connected")).toBeInTheDocument();
  expect(screen.getByText(/Calibration:\s+Ready/)).toBeInTheDocument();
  expect(screen.queryByText(/access_token/i)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
});
