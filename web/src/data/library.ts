// The workspace document library.
//
// The bundled Meridian close pack (release / script / Q&A) is seeded as `demo`
// documents so the workspace is explorable the moment it loads. Anything a user
// uploads is appended as an `upload` document with the same shape (see
// lib/buildDoc.ts), and the two are rendered and managed uniformly.

import type { LibraryDoc } from "../types";
import { DOCS } from "./documents";

// The period the bundled close pack belongs to. Uploads infer their own period
// from the backend (falling back to this when the offline path can't).
export const DEMO_PERIOD = "Q1 FY2026";

// Map the hand-authored demo documents into library entries.
export const DEMO_LIBRARY: LibraryDoc[] = DOCS.map((d) => ({
  id: d.id,
  kind: d.id,
  name: d.name,
  subtitle: d.kind,
  icon: d.icon,
  source: "demo",
  period: DEMO_PERIOD,
  addedAt: "2026-04-21T00:00:00.000Z",
  blocks: d.blocks,
}));
