const YOUTUBE_ID = /^[A-Za-z0-9_-]{6,20}$/;

export function youtubeEmbedUrl(externalBroadcastId: string): string | null {
  const id = externalBroadcastId.trim();
  if (!YOUTUBE_ID.test(id)) {
    return null;
  }
  return `https://www.youtube.com/embed/${id}`;
}
