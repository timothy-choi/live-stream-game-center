import { lifecycleLabel } from "../display";

interface Props {
  lifecycle: string;
}

export function StatusBadge({ lifecycle }: Props) {
  const kind = lifecycle.toLowerCase();
  return (
    <span className={`badge badge-${kind}`}>
      {lifecycleLabel(lifecycle)}
    </span>
  );
}
