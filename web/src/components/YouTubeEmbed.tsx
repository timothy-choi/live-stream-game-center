import { useState } from "react";
import { youtubeEmbedUrl } from "../api/youtube";

interface Props {
  externalBroadcastId: string;
}

export function YouTubeEmbed({ externalBroadcastId }: Props) {
  const [open, setOpen] = useState(false);
  const src = youtubeEmbedUrl(externalBroadcastId);

  if (!src) {
    return (
      <section className="panel">
        <h2>Broadcast</h2>
        <p className="muted">This game has no embeddable YouTube broadcast id.</p>
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Broadcast</h2>
        <button type="button" className="text-button" onClick={() => setOpen((value) => !value)}>
          {open ? "Hide stream" : "Show stream"}
        </button>
      </div>
      <p className="muted">
        The player is for viewing only. Hiding it or leaving this page does not stop backend
        tracking.
      </p>
      {open ? (
        <div className="embed-frame">
          <iframe
            title="YouTube broadcast"
            src={src}
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
          />
        </div>
      ) : null}
    </section>
  );
}
