import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      return walk(path);
    }
    if (entry.name.includes(".test.")) {
      return [];
    }
    return path.endsWith(".ts") || path.endsWith(".tsx") ? [path] : [];
  });
}

test("frontend only uses M11 read endpoints", () => {
  const files = walk(join(process.cwd(), "src")).filter((path) => !path.includes("/test/"));
  const blob = files.map((path) => readFileSync(path, "utf8")).join("\n");
  expect(blob).not.toMatch(/ensure_tracking|LiveFrameSession|\bocr\b|yt-dlp|signed media/i);
  expect(blob).not.toMatch(/method:\s*["'](PUT|PATCH|DELETE)["']/);
  expect(blob).toContain("/api/games");
  expect(blob).toContain("/score-events");
  expect(blob).toContain("/events");
  expect(blob).toContain("/api/youtube/connections");
  expect(blob).toContain("/api/youtube/oauth/start");
});
