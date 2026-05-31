// API client seam.
//
// The app runs on client-side data by default. This module is the boundary where
// the FastAPI verification backend (the `attest` package's /verify endpoint) wires
// in: components depend on the `AttestClient` interface, not on the transport, so
// swapping the offline client for the live one is a one-line change in the store.

import type { VerdictState } from "../types";

export interface VerifyResult {
  verdict: VerdictState;
  reason: string;
  sourceValue: string | null;
}

export interface AttestClient {
  /** Verify a figure's displayed text against its bound source. */
  verifyFigure(figureId: string, text: string): Promise<VerifyResult>;
}

/** Offline default. The mock UI verifies locally (src/lib/verify.ts); this throws
 *  so a misconfigured live path fails loudly rather than silently. */
export const offlineClient: AttestClient = {
  async verifyFigure() {
    throw new Error(
      "offlineClient: set VITE_ATTEST_API to the FastAPI backend to enable live verification"
    );
  },
};

export const apiBaseUrl: string | undefined = import.meta.env.VITE_ATTEST_API;

// --- live client -----------------------------------------------------------

const TENANT = "meridian";

// UI figure id -> the backend's canonical (metric, period, entity) scope, matching
// what attest.demo ingests for the Meridian close pack.
const SCOPE: Record<string, { metric: string; period: string; entity: string }> = {
  rev: { metric: "total_revenue", period: "FY2026-Q1", entity: "MRDN" },
  gaapeps: { metric: "gaap_diluted_eps", period: "FY2026-Q1", entity: "MRDN" },
  nongaapeps: { metric: "non_gaap_diluted_eps", period: "FY2026-Q1", entity: "MRDN" },
  cloudrev: { metric: "cloud_revenue", period: "FY2026-Q1", entity: "MRDN:Cloud" },
  cloudgrowth: { metric: "cloud_growth_yoy", period: "FY2026-Q1", entity: "MRDN:Cloud" },
  ocf: { metric: "operating_cash_flow", period: "FY2026-Q1", entity: "MRDN" },
  buyback: { metric: "share_repurchases", period: "FY2026-Q1", entity: "MRDN" },
  guidance: { metric: "q2_revenue_guidance", period: "FY2026-Q2", entity: "MRDN" },
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
  };
}

/** The client the app uses: live when VITE_ATTEST_API is set, offline otherwise. */
export const client: AttestClient = apiBaseUrl
  ? createLiveClient(apiBaseUrl)
  : offlineClient;
