import { useEffect, useState } from "react";
import { connectYoutubeHref, disconnectYoutubeConnection, listYoutubeConnections } from "../api/youtube";
import type { YoutubeConnectionSummary } from "../api/types";

const SECRET_FIELDS = ["access_token", "refresh_token", "client_secret", "encryption"];

function rejectSecrets(value: unknown): void {
  const raw = JSON.stringify(value).toLowerCase();
  for (const field of SECRET_FIELDS) {
    if (raw.includes(field)) {
      throw new Error("refusing to render credential fields");
    }
  }
}

export function YouTubeConnectionPanel() {
  const [connections, setConnections] = useState<YoutubeConnectionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      const body = await listYoutubeConnections();
      rejectSecrets(body);
      setConnections(body.connections);
      setError(null);
    } catch {
      setError("YouTube connection status is unavailable.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const current = connections[0] ?? null;

  return (
    <section className="panel youtube-panel" aria-labelledby="youtube-connection-heading">
      <div className="panel-head">
        <h2 id="youtube-connection-heading">YouTube channel</h2>
      </div>
      {loading ? <p className="muted">Checking YouTube connection…</p> : null}
      {error ? <p className="banner banner-warn">{error}</p> : null}
      {!loading && !current ? (
        <div>
          <p className="muted">No YouTube channel is connected. The browser does not start tracking.</p>
          <a className="text-button" href={connectYoutubeHref()}>
            Connect YouTube
          </a>
        </div>
      ) : null}
      {current ? (
        <div>
          <p>
            <strong>YouTube connected</strong>
            {current.status === "NEEDS_REAUTHORIZATION" ? " — needs reauthorization" : null}
          </p>
          <p className="card-meta">{current.youtube_channel_title || "Untitled channel"}</p>
          <p className="card-meta">Channel ID {current.youtube_channel_id}</p>
          <p>
            Calibration:{" "}
            {current.calibration_status === "READY" ? "Ready" : "Needs calibration"}
          </p>
          <button
            type="button"
            className="text-button"
            onClick={() => {
              void disconnectYoutubeConnection(current.id).then((body) => {
                rejectSecrets(body);
                setConnections(body.connections);
              }).catch(() => {
                setError("Could not disconnect YouTube.");
              });
            }}
          >
            Disconnect
          </button>
        </div>
      ) : null}
    </section>
  );
}
