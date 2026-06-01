// Client-side figure normalization, mirroring the backend's exact-with-tolerance
// matching closely enough for the live-edit experience. (The authoritative
// deterministic engine is the Python `attest` package; this is the UI's local
// echo of it, used only to re-verify inline edits in the mock.)

import type { Figure, VerdictState } from "../types";

const norm = (x: string) => (x || "").toLowerCase().replace(/[\s,$]/g, "");

/** Re-derive a figure's verdict after an inline edit, comparing to its filed value. */
export function evaluateEdit(fig: Figure): {
  st: VerdictState;
  tag: string;
  editedFrom: string | null;
} {
  if (fig.id === "guidance") return { st: "r", tag: "?", editedFrom: null };
  if (fig.filed == null) return { st: "u", tag: "?", editedFrom: null };
  if (norm(fig.cur) === norm(fig.filed)) return { st: "v", tag: "✓", editedFrom: null };
  return { st: "f", tag: "!", editedFrom: fig.filed };
}

const FIGURE_RE =
  /\$\s?\d[\d.,]*\s*(?:billion|million|thousand)?|\b\d{1,3}(?:\.\d+)?\s?%/gi;

/** Detect numeric spans the editor introduced that aren't yet bound to a source. */
export function detectNewFigures(text: string): string[] {
  return (text.match(FIGURE_RE) || []).map((s) => s.trim());
}

export const VERDICT_LABEL: Record<VerdictState, string> = {
  v: "Traced",
  r: "Manual check",
  f: "Conflict",
  u: "Untraced",
};
