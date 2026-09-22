import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { YouTubeConnectedPage } from "./YouTubeConnectedPage";

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderPage(search: string) {
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
  render(
    <MemoryRouter initialEntries={[`/youtube/connected${search}`]}>
      <Routes>
        <Route path="/youtube/connected" element={<YouTubeConnectedPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

test("success callback shows connected result without tokens", async () => {
  renderPage("?ok=1");
  expect((await screen.findAllByText("YouTube connected")).length).toBeGreaterThan(0);
  expect(screen.getByText(/Tokens stay on the server/)).toBeInTheDocument();
  expect(await screen.findByText("Arcade Channel")).toBeInTheDocument();
  expect(screen.queryByText(/access_token/i)).not.toBeInTheDocument();
});

test("denied callback shows a safe error", async () => {
  renderPage("?error=denied");
  expect(await screen.findByText("YouTube authorization was denied.")).toBeInTheDocument();
});
