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

export type DocKind = "release" | "script" | "qa" | "other";

export interface DocMeta {
  id: DocKind;
  name: string;
  kind: string;
  icon: string; // svg markup
  blocks: Block[];
}

// Where a document in the workspace library came from.
export type DocSource = "demo" | "upload";

// How a particular version's content arrived.
export type VersionOrigin = "demo" | "upload" | "paste";

// A single saved version of a library document.
//
// Version control lets a user re-upload a draft and keep the prior tie-outs
// intact: each version owns its own rendered blocks, its ingestion warnings, and
// the ids of the figures it renders (figures themselves live in the store's flat
// map, namespaced by version id so versions never collide). The document renders
// whichever version is currently active.
export interface DocVersion {
  id: string; // unique within the library; figures are keyed `${id}::${claim}`
  label: string; // human label, e.g. "Version 3" or "As filed"
  addedAt: string; // ISO timestamp this version was created
  origin: VersionOrigin;
  blocks: Block[];
  figureIds: string[]; // ids into the store's figure map that this version renders
  warnings?: string[]; // honest notes from ingestion (e.g. extraction caveats)
  note?: string; // optional free-text note the user attaches to the version
}

// A single document in the workspace library. The bundled Meridian close pack is
// seeded as `demo` documents; anything a user uploads (or pastes) becomes an
// `upload` document with the same shape, so the renderer treats them uniformly.
//
// Every document carries a version history (`versions`, newest first) and an
// `activeVersionId`. The top-level `blocks`/`warnings` always mirror the active
// version so the renderer (and coverage counts) read a document uniformly without
// knowing about versions.
export interface LibraryDoc {
  id: string; // unique within the library; demo docs reuse their DocKind id
  kind: DocKind;
  name: string;
  subtitle: string; // the small caption under the name (e.g. "Press release · 8-K Ex.99.1")
  icon: string; // svg markup
  source: DocSource;
  period: string; // close-pack grouping, e.g. "Q1 FY2026"
  addedAt: string; // ISO timestamp the document entered the library
  blocks: Block[]; // mirror of the active version's blocks
  warnings?: string[]; // mirror of the active version's warnings
  versions: DocVersion[]; // version history, newest first
  activeVersionId: string;
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
