// API client for the FastAPI verification backend.
//
// The app is served by the backend (`attest serve`) and talks to it on the same
// origin, so the base URL defaults to "" (relative). Set VITE_ATTEST_API to point
// the Vite dev server (:5173) at a backend running elsewhere (e.g. :8000).

import type { AnalyzeResult, FactRow, UploadInput } from "../types";

// Single-tenant workspace. The bundled demo filings ingest under this tenant, so
// "Load demo filed numbers" and uploaded drafts tie out against the same store.
export const TENANT = "meridian";

const RAW = (import.meta.env.VITE_ATTEST_API as string | undefined) ?? "";
export const apiBaseUrl = RAW.replace(/\/$/, "");

function url(path: string): string {
  return `${apiBaseUrl}${path}`;
}

async function asError(res: Response): Promise<Error> {
  let detail = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") detail = body.detail;
    else if (body) detail = JSON.stringify(body);
  } catch {
    /* non-JSON body */
  }
  return new Error(detail);
}

export interface IngestReport {
  source: string;
  ingested: number;
  skipped: number;
  skipped_tags: string[];
}

export interface GuidanceInput {
  text: string;
  entity: string;
  accession: string;
  base_period?: string;
  as_of?: string;
  label?: string;
}

export interface AttestClient {
  /** Upload (file) or paste (text) a draft; the backend extracts, proposes
   *  figure claims, and runs the deterministic engine over them. */
  analyze(input: UploadInput): Promise<AnalyzeResult>;
  /** Ingest the bundled Meridian filing so uploads have filed sources to trace. */
  ingestDemo(): Promise<IngestReport>;
  /** Ingest a tenant's own filed XBRL facts (the instance JSON). */
  ingestXbrl(instance: unknown): Promise<IngestReport>;
  /** Ingest forward guidance from 8-K EX-99.1 prose. */
  ingestGuidance(input: GuidanceInput): Promise<IngestReport>;
  /** The filed facts currently in the store. */
  listFacts(): Promise<FactRow[]>;
}

function tenantPath(suffix: string): string {
  return `/tenants/${encodeURIComponent(TENANT)}${suffix}`;
}

export const client: AttestClient = {
  async analyze(input) {
    const form = new FormData();
    if (input.file) form.append("file", input.file);
    if (input.text) form.append("text", input.text);
    if (input.title) form.append("title", input.title);
    form.append("kind", input.kind);
    if (input.entity) form.append("entity", input.entity);
    if (input.period) form.append("period", input.period);
    const res = await fetch(url(tenantPath("/analyze")), { method: "POST", body: form });
    if (!res.ok) throw await asError(res);
    return (await res.json()) as AnalyzeResult;
  },

  async ingestDemo() {
    const res = await fetch(url(tenantPath("/ingest/demo")), { method: "POST" });
    if (!res.ok) throw await asError(res);
    return (await res.json()) as IngestReport;
  },

  async ingestXbrl(instance) {
    const res = await fetch(url(tenantPath("/ingest/xbrl")), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(instance),
    });
    if (!res.ok) throw await asError(res);
    return (await res.json()) as IngestReport;
  },

  async ingestGuidance(input) {
    const res = await fetch(url(tenantPath("/ingest/guidance")), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
    if (!res.ok) throw await asError(res);
    return (await res.json()) as IngestReport;
  },

  async listFacts() {
    const res = await fetch(url(tenantPath("/facts")));
    if (!res.ok) throw await asError(res);
    return (await res.json()) as FactRow[];
  },
};
