// Domain types for the Attest workspace. Mirrors the backend's verdict vocabulary
// where they overlap; the UI carries extra presentational fields (badge, page).

export type VerdictState = "v" | "r" | "f" | "u"; // traced | review | conflict | untraced

export interface FigureField {
  label: string;
  value: string;
  tone?: "" | "bad" | "good";
}

export interface Figure {
  id: string;
  v: string; // canonical headline value (modal title)
  lbl: string;
  st: VerdictState;
  badge: string;
  tag: string; // superscript tag shown in-line
  cur: string; // current displayed text (mutable via edits)
  filed: string | null; // canonical filed value used to verify edits; null = no filed source
  editedFrom?: string | null;
  snip: string; // short HTML snippet (unused in modal but kept for parity)
  cite: string;
  page: string; // HTML source excerpt
  reason: string; // HTML reason
  fields: FigureField[];
}

export type NarrativeState = "ok" | "warn" | "conflict";
export type NarrativeKind =
  | "onmessage"
  | "wording"
  | "narrative"
  | "forwardlooking";

export interface HistoryRow {
  p: string;
  q: string;
  m: string;
  s: "consistent" | "drift" | "contradict" | "baseline";
}

export interface NarrativeHistory {
  worst: "drift" | "contradict";
  verdict: string;
  rows: HistoryRow[];
}

export interface Narrative {
  id: string;
  phrase: string;
  cur: string;
  kind: NarrativeKind;
  st: NarrativeState;
  tag: string;
  title: string;
  against: string;
  compare: string; // HTML
  detail: string;
  suggestion: string | null;
  applyLabel?: string;
  history?: NarrativeHistory;
}

export interface Commitment {
  id: string;
  period: string;
  status: "open" | "done";
  text: string;
  detail: string;
}

// Inline document tokens.
export type Inline =
  | { kind: "text"; html: string } // raw inline markup (e.g. cue spans)
  | { kind: "fig"; id: string }
  | { kind: "nar"; id: string };

export type Block =
  | { kind: "eyebrow"; text: string }
  | { kind: "h1"; text: string }
  | { kind: "dek"; text: string }
  | { kind: "hr" }
  | { kind: "h2"; text: string }
  | { kind: "narbar" } // placeholder; rendered as the live narrative summary
  | { kind: "speaker"; text: string }
  | { kind: "p"; parts: Inline[] }
  | { kind: "qa"; tag: string; q: string; a: Inline[] };

export type DocKind = "release" | "script" | "qa";

export interface DocMeta {
  id: DocKind;
  name: string;
  kind: string;
  icon: string; // svg markup
  blocks: Block[];
}

export interface TrendPoint {
  l: string;
  v: number;
}
export interface TrendSeries {
  u: "m" | "eps" | "pct";
  q: TrendPoint[];
  y: TrendPoint[];
  fwd?: { lo: number; hi: number; l: string };
}
