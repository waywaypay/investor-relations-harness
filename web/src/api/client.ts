// API client seam.
//
// The app runs on client-side data by default. This module is the boundary where
// the FastAPI verification backend (the `attest` package's /verify endpoint) wires
// in: components depend on the `AttestClient` interface, not on the transport, so
// swapping the offline client for the live one is a one-line change in the store.

import type { DocKind, VerdictState } from "../types";

export interface VerifyResult {
  verdict: VerdictState;
  reason: string;
  sourceValue: string | null;
}

/** A figure claim the engine proposed for an uploaded draft. */
export interface AnalyzeClaim {
  claim_id: string;
  metric: string;
  period: string;
  entity: string;
  displayed_text: string;
  span: [number, number] | null;
}

/** The deterministic disposition of one proposed claim. */
export interface AnalyzeVerdict {
  claim_id: string;
  metric: string;
  period: string;
  displayed_text: string;
  verdict: string; // backend vocabulary: traced | needs_review | conflict | untraced
  reason: string;
  source_value: string | null;
}

/** The full result of uploading/pasting a draft and running it through the spine. */
export interface AnalyzeResult {
  document_id: string;
  title: string;
  kind: string;
  entity: string;
  period: string | null;
  text: string;
  claims: AnalyzeClaim[];
  verdicts: AnalyzeVerdict[];
  warnings: string[];
}

/** Inputs for an upload: a picked file *or* pasted text, plus light metadata. */
export interface AnalyzeInput {
  file?: File;
  text?: string;
  title?: string;
  kind: DocKind;
  entity?: string;
  period?: string;
}

/** Inputs for filing a prior disclosure (past release / transcript / deck) as the
 *  reference corpus a later draft is checked against. */
export interface DisclosureInput {
  file?: File;
  text?: string;
  entity?: string;
  period?: string;
  label?: string;
}

/** The result of ingesting a prior disclosure: how many figures became reference facts. */
export interface DisclosureResult {
  source: string;
  ingested: number;
  skipped: number;
}

export interface AttestClient {
  /** Verify a figure's displayed text against its bound source. */
  verifyFigure(figureId: string, text: string): Promise<VerifyResult>;
  /** Upload/paste a draft, returning the engine's claims + verdicts for rendering. */
  analyzeDocument(input: AnalyzeInput): Promise<AnalyzeResult>;
  /** File a prior disclosure as the reference corpus for consistency checks. */
  ingestDisclosure(input: DisclosureInput): Promise<DisclosureResult>;
}

/** Offline default. The mock UI verifies locally (src/lib/verify.ts); these throw
 *  so a misconfigured live path fails loudly rather than silently — the store
 *  catches the analyze failure and falls back to client-side figure detection. */
export const offlineClient: AttestClient = {
  async verifyFigure() {
    throw new Error(
      "offlineClient: set VITE_ATTEST_API to the FastAPI backend to enable live verification"
    );
  },
  async analyzeDocument() {
    throw new Error(
      "offlineClient: set VITE_ATTEST_API to the FastAPI backend to enable live analysis"
    );
  },
  async ingestDisclosure() {
    throw new Error(
      "offlineClient: set VITE_ATTEST_API to the FastAPI backend to file a prior disclosure"
    );
  },
};

export const apiBaseUrl: string =
  import.meta.env.VITE_ATTEST_API ??
  (typeof window !== "undefined" ? window.location.origin : "");

// --- live client -----------------------------------------------------------

const TENANT = "atlas";

// UI figure id -> the backend's canonical (metric, period, entity) scope for the
// reference close pack.
const SCOPE: Record<string, { metric: string; period: string; entity: string }> = {
  rev: { metric: "total_revenue", period: "FY2026-Q1", entity: "ATLS" },
  gaapeps: { metric: "gaap_diluted_eps", period: "FY2026-Q1", entity: "ATLS" },
  nongaapeps: { metric: "non_gaap_diluted_eps", period: "FY2026-Q1", entity: "ATLS" },
  cloudrev: { metric: "cloud_revenue", period: "FY2026-Q1", entity: "ATLS:Cloud" },
  cloudgrowth: { metric: "cloud_growth_yoy", period: "FY2026-Q1", entity: "ATLS:Cloud" },
  ocf: { metric: "operating_cash_flow", period: "FY2026-Q1", entity: "ATLS" },
  buyback: { metric: "share_repurchases", period: "FY2026-Q1", entity: "ATLS" },
  guidance: { metric: "q2_revenue_guidance", period: "FY2026-Q2", entity: "ATLS" },
};

// Backend verdict vocabulary -> the UI's single-letter states.
const VERDICT_MAP: Record<string, VerdictState> = {
  traced: "v",
  needs_review: "r",
  conflict: "f",
  untraced: "u",
};

interface ApiVerdict {
  metric: string;
  verdict: string;
  reason: string;
  source_value: string | null;
}

/** Build a client that calls the live FastAPI `/verify` endpoint. Each figure is
 *  wrapped as a one-claim document so a single edit re-verifies through the real
 *  deterministic engine. */
export function createLiveClient(baseUrl: string): AttestClient {
  const base = baseUrl.replace(/\/$/, "");
  return {
    async verifyFigure(figureId, text) {
      const scope = SCOPE[figureId];
      if (!scope) {
        // Unknown (e.g. newly typed) figure has no bound scope -> untraced.
        return { verdict: "u", reason: "No source bound for this figure.", sourceValue: null };
      }
      const doc = {
        id: "live",
        tenant_id: TENANT,
        title: "live verify",
        kind: "other",
        text: "",
        claims: [
          {
            claim_id: figureId,
            document_id: "live",
            entity: scope.entity,
            metric: scope.metric,
            period: scope.period,
            displayed_text: text,
          },
        ],
      };
      const res = await fetch(`${base}/tenants/${TENANT}/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(doc),
      });
      if (!res.ok) throw new Error(`verify failed: HTTP ${res.status}`);
      const body = (await res.json()) as { verdicts: ApiVerdict[] };
      const v = body.verdicts[0];
      return {
        verdict: VERDICT_MAP[v?.verdict] ?? "u",
        reason: v?.reason ?? "",
        sourceValue: v?.source_value ?? null,
      };
    },

    async analyzeDocument(input) {
      // The /analyze endpoint accepts a multipart file *or* a pasted text field,
      // recovers the prose, proposes claims, and runs the full deterministic
      // engine — the same spine the demo close pack flows through.
      const form = new FormData();
      if (input.file) form.append("file", input.file, input.file.name);
      if (input.text) form.append("text", input.text);
      if (input.title) form.append("title", input.title);
      form.append("kind", input.kind);
      if (input.entity) form.append("entity", input.entity);
      if (input.period) form.append("period", input.period);

      const res = await fetch(`${base}/tenants/${TENANT}/analyze`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (body?.detail) detail = body.detail;
        } catch {
          /* non-JSON error body; keep the status */
        }
        throw new Error(`analyze failed: ${detail}`);
      }
      return (await res.json()) as AnalyzeResult;
    },

    async ingestDisclosure(input) {
      // Files a prior disclosure as non-filed "previously disclosed" facts, so a
      // later draft that restates a figure and changed it is flagged as
      // contradicting prior disclosure. Multipart, mirroring analyze.
      const form = new FormData();
      if (input.file) form.append("file", input.file, input.file.name);
      if (input.text) form.append("text", input.text);
      if (input.entity) form.append("entity", input.entity);
      if (input.period) form.append("period", input.period);
      if (input.label) form.append("label", input.label);

      const res = await fetch(`${base}/tenants/${TENANT}/ingest/disclosure`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (body?.detail) detail = body.detail;
        } catch {
          /* non-JSON error body; keep the status */
        }
        throw new Error(`ingest disclosure failed: ${detail}`);
      }
      return (await res.json()) as DisclosureResult;
    },
  };
}

/** The client the app uses. It targets a backend by default — the page's own
 *  origin (so a FastAPI-served bundle or `attest serve` ties out with no config),
 *  or VITE_ATTEST_API for a split deployment. When no backend answers, the store's
 *  analyze/verify calls reject and degrade to the offline, honestly-untraced path,
 *  so a pure static demo still works. `offlineClient` is only the non-browser
 *  (SSR/test) fallback where there is no origin to target. */
export const client: AttestClient = apiBaseUrl
  ? createLiveClient(apiBaseUrl)
  : offlineClient;
