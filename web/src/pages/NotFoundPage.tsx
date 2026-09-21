import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <main className="page">
      <h1>Page not found</h1>
      <p className="muted">That route is not part of the scoreboard.</p>
      <Link to="/">Back to scoreboard</Link>
    </main>
  );
}
