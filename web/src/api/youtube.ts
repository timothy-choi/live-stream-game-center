import { apiGet, apiPost, youtubeOAuthStartUrl } from "./client";
import type { YoutubeConnectionListResponse } from "./types";

const YOUTUBE_ID = /^[A-Za-z0-9_-]{6,20}$/;

export function youtubeEmbedUrl(externalBroadcastId: string): string | null {
  const id = externalBroadcastId.trim();
  if (!YOUTUBE_ID.test(id)) {
    return null;
  }
  return `https://www.youtube.com/embed/${id}`;
}

export function listYoutubeConnections(
  fetchImpl?: typeof fetch,
): Promise<YoutubeConnectionListResponse> {
  return apiGet<YoutubeConnectionListResponse>("/api/youtube/connections", {}, fetchImpl);
}

export function disconnectYoutubeConnection(
  connectionId: string,
  fetchImpl?: typeof fetch,
): Promise<YoutubeConnectionListResponse> {
  return apiPost<YoutubeConnectionListResponse>(
    `/api/youtube/connections/${encodeURIComponent(connectionId)}/disconnect`,
    {},
    fetchImpl,
  );
}

export function connectYoutubeHref(): string {
  return youtubeOAuthStartUrl();
}
