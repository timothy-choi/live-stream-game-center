import { expect, test } from "vitest";
import { youtubeEmbedUrl } from "./youtube";

test("builds embed from broadcast id only", () => {
  expect(youtubeEmbedUrl("hBblAZD-ApY")).toBe("https://www.youtube.com/embed/hBblAZD-ApY");
});

test("rejects signed or media URLs", () => {
  expect(youtubeEmbedUrl("https://cdn.example/video.m3u8?sig=abc")).toBeNull();
  expect(youtubeEmbedUrl("https://www.youtube.com/watch?v=hBblAZD-ApY")).toBeNull();
});
