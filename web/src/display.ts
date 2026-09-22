import type { GameSummary, TeamSummary } from "./api/types";

export function teamLabel(team: TeamSummary | null, fallback = "TBD"): string {
  if (!team) {
    return fallback;
  }
  return team.abbreviation || team.id;
}

export function teamName(team: TeamSummary | null, fallback = "Unknown"): string {
  if (!team) {
    return fallback;
  }
  return team.display_name || team.abbreviation || team.id;
}

export function scoreLabel(value: number | null, status: string): string {
  if (status !== "TRUSTED" || value === null) {
    return "—";
  }
  return String(value);
}

export function formatUtc(value: string | null): string | null {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(date);
}

export function trackingHint(game: GameSummary): string | null {
  if (game.completion_status === "FINALIZING") {
    if (game.recovery_status === "RECOVERING") {
      return "Recovering missed scoring...";
    }
    return "VERIFYING FINAL SCORE";
  }
  if (!game.tracking_enabled) {
    return "Tracking off";
  }
  if (game.platform_lifecycle === "LIVE" && game.tracking_status === "ACQUISITION_ENDED") {
    return "Tracking interrupted";
  }
  if (
    game.platform_lifecycle === "LIVE" &&
    game.tracking_status === "TRACKING" &&
    game.score.status === "UNCONFIRMED"
  ) {
    return "Waiting for score";
  }
  if (game.platform_lifecycle === "LIVE" && game.tracking_status === "TRACKING") {
    return "Tracking";
  }
  return null;
}

export function lifecycleLabel(lifecycle: string): string {
  if (lifecycle === "LIVE") {
    return "LIVE";
  }
  if (lifecycle === "UPCOMING") {
    return "UPCOMING";
  }
  if (lifecycle === "ENDED") {
    return "ENDED";
  }
  if (lifecycle === "FINALIZING") {
    return "FINALIZING";
  }
  if (lifecycle === "FINAL") {
    return "FINAL";
  }
  return lifecycle;
}

export function badgeKind(game: GameSummary): string {
  if (game.completion_status === "FINAL") {
    return "FINAL";
  }
  if (game.completion_status === "FINALIZING") {
    return "FINALIZING";
  }
  return game.platform_lifecycle;
}

export function endedUnverifiedNote(game: GameSummary): string | null {
  if (game.completion_status === "FINAL" || game.completion_status === "FINALIZING") {
    return null;
  }
  if (game.platform_lifecycle === "ENDED" && game.score.status === "TRUSTED") {
    return "Latest tracked score";
  }
  return null;
}
