import { Link, useSearchParams } from "react-router-dom";
import { YouTubeConnectionPanel } from "../components/YouTubeConnectionPanel";

const ERROR_COPY: Record<string, string> = {
  denied: "YouTube authorization was denied.",
  invalid_state: "The authorization request could not be verified. Try Connect YouTube again.",
  expired_state: "The authorization request expired. Try Connect YouTube again.",
  replayed_state: "That authorization request was already used.",
  exchange_failed: "Google did not complete token exchange.",
  no_channel: "The authorized Google account has no usable YouTube channel.",
  persist_failed: "The connection could not be saved.",
  not_configured: "This server is not configured for YouTube OAuth yet.",
  oauth_failed: "YouTube connection failed.",
};

export function YouTubeConnectedPage() {
  const [params] = useSearchParams();
  const error = params.get("error");
  const ok = params.get("ok") === "1";

  return (
    <main className="page">
      <p className="crumb">
        <Link to="/">Back to scoreboard</Link>
      </p>
      <h1>{error ? "YouTube connection" : "YouTube connected"}</h1>
      {error ? (
        <p className="banner banner-warn">{ERROR_COPY[error] || ERROR_COPY.oauth_failed}</p>
      ) : null}
      {ok && !error ? (
        <p className="muted">Google authorization finished. Tokens stay on the server.</p>
      ) : null}
      <YouTubeConnectionPanel />
    </main>
  );
}
