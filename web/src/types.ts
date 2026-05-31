// Types for the Attest workspace.
//
// These mirror the FastAPI backend's wire shapes (see src/attest/api/schemas.py
// and src/attest/domain/verdicts.py). The app no longer ships any seed
// documents — every document on screen is one the user uploaded and the backend
// analyzed.

// The four deterministic dispositions a figure can resolve to.
export type Verdict = "traced" | "needs_review" | "conflict" | "untraced";

// Short single-letter state used for the figure styling classes (.fig.v/.r/.f/.u).
export type VerdictState = "v" | "r" | "f" | "u";

export const VERDICT_STATE: Record<Verdict, VerdictState> = {
  traced: "v",
  needs_review: "r",
  conflict: "f",
  untraced: "u",
};

export const VERDICT_LABEL: Record<Verdict, string> = {
  traced: "Traced",
  needs_review: "Needs review",
  conflict: "Conflict",
  untraced: "Untraced",
};

export interface Provenance {
  source: string;
  ref?: string | null;
  as_of?: string | null;
  confidence?: string;
}

// A candidate figure the edge proposed from the prose.
export interface ApiClaim {
  claim_id: string;
  document_id: string;
  entity: string;
  metric: string;
  period: string;
  displayed_text: string;
  span: [number, number] | null;
  detect_confidence: string;
}

// The deterministic disposition of a single claim.
export interface ApiVerdict {
  claim_id: string;
  document_id: string;
  entity: string;
  metric: string;
  period: string;
  displayed_text: string;
  verdict: Verdict;
  reason: string;
  provenance: Provenance | null;
  source_value: string | null;
  as_of: string | null;
}

export type RuleSeverity = "block" | "warn" | "info";

export interface RuleFinding {
  rule: string;
  severity: RuleSeverity;
  document_id: string | null;
  metric: string | null;
  message: string;
  detail: string;
}

// The /analyze response: verification of an uploaded draft, enriched for render.
export interface AnalyzeResult {
  document_id: string;
  verdicts: ApiVerdict[];
  findings: RuleFinding[];
  counts: Record<string, number>;
  publishable: boolean;
  title: string;
  kind: string;
  entity: string;
  period: string | null;
  text: string;
  claims: ApiClaim[];
  warnings: string[];
}

// A filed fact in the tenant's store (GET /facts).
export interface FactRow {
  entity: string;
  metric: string;
  period: string;
  value: unknown;
  provenance?: Provenance | null;
  [k: string]: unknown;
}

export type DocKind = "release" | "script" | "qa" | "other";

export const DOC_KINDS: { value: DocKind; label: string }[] = [
  { value: "release", label: "Earnings release" },
  { value: "script", label: "Prepared remarks" },
  { value: "qa", label: "Q&A prep" },
  { value: "other", label: "Other document" },
];

// A document the user uploaded and the backend analyzed, as held in the store.
// `localId` is unique per upload (the backend keys document_id by kind, which
// collides when you upload two releases).
export interface UploadedDoc {
  localId: string;
  title: string;
  kind: string;
  entity: string;
  period: string | null;
  text: string;
  claims: ApiClaim[];
  verdicts: Record<string, ApiVerdict>; // by claim_id
  findings: RuleFinding[];
  counts: Record<string, number>;
  publishable: boolean;
  warnings: string[];
  uploadedAt: number;
  // The original input kept so the document can be re-analyzed when sources change.
  sourceText: string;
}

export interface UploadInput {
  file?: File;
  text?: string;
  title?: string;
  kind: DocKind;
  entity?: string;
  period?: string;
}
