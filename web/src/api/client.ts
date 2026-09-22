import { ApiError, NotFoundError } from "./types";

export function apiBaseUrl(): string {
  const raw = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").trim();
  return raw.replace(/\/$/, "");
}

export function feedRefreshMs(): number {
  const raw = Number(import.meta.env.VITE_FEED_REFRESH_S ?? "60");
  if (!Number.isFinite(raw) || raw < 5) {
    return 60_000;
  }
  return raw * 1000;
}

async function apiRequest<T>(
  path: string,
  method: string,
  init: RequestInit = {},
  fetchImpl: typeof fetch = fetch,
): Promise<T> {
  const url = `${apiBaseUrl()}${path}`;
  let response: Response;
  try {
    response = await fetchImpl(url, {
      ...init,
      method,
      headers: { Accept: "application/json", ...(init.headers || {}) },
    });
  } catch {
    throw new ApiError(0, "API unavailable");
  }
  if (response.status === 404) {
    throw new NotFoundError();
  }
  if (!response.ok) {
    throw new ApiError(response.status, "API request failed");
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, "Malformed API response");
  }
}

export async function apiGet<T>(
  path: string,
  init: RequestInit = {},
  fetchImpl: typeof fetch = fetch,
): Promise<T> {
  return apiRequest<T>(path, "GET", init, fetchImpl);
}

export async function apiPost<T>(
  path: string,
  init: RequestInit = {},
  fetchImpl: typeof fetch = fetch,
): Promise<T> {
  return apiRequest<T>(path, "POST", init, fetchImpl);
}

export function eventsUrl(gameId: string): string {
  return `${apiBaseUrl()}/api/games/${encodeURIComponent(gameId)}/events`;
}

export function youtubeOAuthStartUrl(): string {
  return `${apiBaseUrl()}/api/youtube/oauth/start`;
}
