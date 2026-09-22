import { afterEach, expect, test, vi } from "vitest";
import {
  connectYoutubeHref,
  disconnectYoutubeConnection,
  listYoutubeConnections,
  youtubeEmbedUrl,
} from "./youtube";

test("builds embed from broadcast id only", () => {
  expect(youtubeEmbedUrl("hBblAZD-ApY")).toBe("https://www.youtube.com/embed/hBblAZD-ApY");
});

test("rejects signed or media URLs", () => {
  expect(youtubeEmbedUrl("https://cdn.example/video.m3u8?sig=abc")).toBeNull();
  expect(youtubeEmbedUrl("https://www.youtube.com/watch?v=hBblAZD-ApY")).toBeNull();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("lists connections from the read endpoint only", async () => {
  const fetchImpl = vi.fn(async () =>
    new Response(
      JSON.stringify({
        connections: [
          {
            id: "conn-1",
            provider: "youtube",
            youtube_channel_id: "UCz3q0f-dMPNIyQhntPTE7gQ",
            youtube_channel_title: "PRISM",
            status: "CONNECTED",
            calibration_status: "READY",
            connected_at: "2026-09-21T20:00:00Z",
            last_refreshed_at: null,
          },
        ],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  const body = await listYoutubeConnections(fetchImpl);
  expect(body.connections[0].youtube_channel_id).toBe("UCz3q0f-dMPNIyQhntPTE7gQ");
  expect(JSON.stringify(body).toLowerCase()).not.toContain("access_token");
  expect(JSON.stringify(fetchImpl.mock.calls)).toContain("/api/youtube/connections");
});

test("connect href is the backend start endpoint", () => {
  expect(connectYoutubeHref()).toContain("/api/youtube/oauth/start");
  expect(connectYoutubeHref()).not.toContain("client_secret");
  expect(connectYoutubeHref()).not.toContain("accounts.google.com");
});

test("disconnect posts to the connection id", async () => {
  const fetchImpl = vi.fn(async () =>
    new Response(JSON.stringify({ connections: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  await disconnectYoutubeConnection("conn-1", fetchImpl);
  expect(JSON.stringify(fetchImpl.mock.calls)).toContain("/disconnect");
  expect(JSON.stringify(fetchImpl.mock.calls)).toContain("POST");
});
