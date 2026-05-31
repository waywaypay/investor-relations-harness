// Shared inline SVGs, ported from the prototype.

export const Tick = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2}>
    <path d="M20 6L9 17l-5-5" />
  </svg>
);

export const Warn = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2}>
    <path d="M12 9v4M12 17h.01M10.3 3.9L2 18a1.9 1.9 0 001.7 2.9h16.6A1.9 1.9 0 0022 18L13.7 3.9a1.9 1.9 0 00-3.4 0z" />
  </svg>
);

export const Eye = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2}>
    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" />
    <circle cx="12" cy="12" r="2.5" />
  </svg>
);

export function StatusIcon({ st }: { st: "v" | "r" | "f" }) {
  if (st === "v") return <Tick />;
  if (st === "f") return <Warn />;
  return <Eye />;
}
