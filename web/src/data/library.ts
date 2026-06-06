// The workspace document library.
//
// The bundled reference close pack (release / script / Q&A) is seeded as
// documents so the workspace is explorable the moment it loads. Anything a user
// uploads is appended as an `upload` document with the same shape (see
// lib/buildDoc.ts), and the two are rendered and managed uniformly.

import type { Block, Inline, LibraryDoc } from "../types";
import { DOCS } from "./documents";

// The period the bundled close pack belongs to. Uploads infer their own period
// from the backend (falling back to this when the offline path can't).
export const DEMO_PERIOD = "Q1 FY2026";

const DEMO_SEEDED_AT = "2026-04-21T00:00:00.000Z";

// The figure ids a set of blocks renders, in document order. Used to scope a
// version's coverage counts (and, for uploads, which figures to clean up).
export function collectFigureIds(blocks: Block[]): string[] {
  const ids: string[] = [];
  const fromInline = (parts: Inline[]) =>
    parts.forEach((p) => p.kind === "fig" && ids.push(p.id));
  for (const b of blocks) {
    if (b.kind === "p") fromInline(b.parts);
    if (b.kind === "qa") fromInline(b.a);
  }
  return ids;
}

// Map the hand-authored reference documents into library entries. Each document
// seeds a single "As filed" version so it lives in the version model uniformly;
// its figures use the shared global ids (rev, gaapeps, …), so they are never
// pruned when a document is removed in-session.
export const DEMO_LIBRARY: LibraryDoc[] = DOCS.map((d) => {
  const versionId = `${d.id}__v1`;
  return {
    id: d.id,
    kind: d.id,
    name: d.name,
    subtitle: d.kind,
    icon: d.icon,
    source: "demo",
    period: DEMO_PERIOD,
    addedAt: DEMO_SEEDED_AT,
    blocks: d.blocks,
    versions: [
      {
        id: versionId,
        label: "As filed",
        addedAt: DEMO_SEEDED_AT,
        origin: "demo",
        blocks: d.blocks,
        figureIds: collectFigureIds(d.blocks),
      },
    ],
    activeVersionId: versionId,
  };
});
