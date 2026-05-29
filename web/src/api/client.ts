// API client seam.
//
// The app runs entirely on client-side data today. This module is where the
// FastAPI verification backend (the `attest` package's /verify and /ingest
// endpoints) wires in later — swap these stubs for fetch() calls and the rest of
// the UI is unaffected, because components depend on this interface, not on the
// transport.

import type { VerdictState } from "../types";

export interface VerifyResult {
  verdict: VerdictState;
  reason: string;
  sourceValue: string | null;
}

export interface AttestClient {
  /** Verify a figure's displayed text against the bound source. */
  verifyFigure(metric: string, period: string, text: string): Promise<VerifyResult>;
}

/** Default no-op client. The mock UI does verification locally; this exists so
 *  the seam is real and typed, ready to point at `VITE_ATTEST_API`. */
export const offlineClient: AttestClient = {
  async verifyFigure() {
    throw new Error(
      "offlineClient: wire VITE_ATTEST_API to the FastAPI backend to enable live verification"
    );
  },
};

export const apiBaseUrl: string | undefined = import.meta.env.VITE_ATTEST_API;
