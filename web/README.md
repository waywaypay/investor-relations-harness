# Attest — web workspace

A React + TypeScript port of the Attest disclosure-drafting workspace: the
editor, figure-verification popovers/modals, narrative & commitment checks,
Street consensus, and the earnings calendar.

The workspace opens on the bundled Meridian close pack, but it's a **document
library**, not a fixed demo: use **+ Upload** in the sidebar to drop in (or
paste) your own release / script / Q&A. Each upload is run through the engine,
its figures are tied out and highlighted in place, and it's added to the library
grouped by period — so you can keep and switch between multiple/historical
documents, and remove uploads you no longer need. Uploads persist across reloads
(localStorage); the demo close pack is always re-seeded as removable samples.

### Versions & document management

Every document carries a **version history**. The bar above the draft shows the
active version and offers **+ Upload new version** — file a revised draft and the
engine re-ties it out while the prior version (and its tie-outs) is kept. When a
document has more than one version, a dropdown switches between them, and you can
restore an earlier one at any time.

**Manage** (next to **+ Upload**, or **History** on the draft) opens the document
manager: a single place to rename documents, review each version with its tie-out
coverage and an optional "what changed" note, make a version active or delete it,
file a new version, or remove a whole document. (Versions layered onto the demo
samples are session-only; upload your own document to keep its history across
reloads.)

## Run it

```bash
cd web
npm install
npm run dev        # http://localhost:5173
```

Other scripts:

```bash
npm run build      # type-check + production build to dist/
npm run preview    # serve the built bundle
npm run typecheck  # tsc --noEmit
npm run test       # vitest run (unit + component/integration tests)
```

Tests use Vitest + React Testing Library (jsdom). `src/lib/verify.test.ts` covers
the verification echo; `src/App.test.tsx` renders the full workspace and exercises
the key flows (coverage summary, resolving the cloud-growth conflict to 29%,
consensus build, calendar, the script's narrative bar, and uploading / removing a
document from the library).

> In a remote/container dev environment, the server binds to `0.0.0.0` (see
> `vite.config.ts`); use your platform's port forwarding to reach `:5173`.

## What's wired to what

This is a **standalone faithful port** of the prototype — it runs entirely on
client-side data (`src/data/`), so every surface works visually without a
backend. The figure-verification path mirrors the Python engine's behaviour
closely enough for the live-edit experience (`src/lib/verify.ts`).

The **API seam** lives in `src/api/client.ts`. Components depend on that typed
interface, not on the transport. By default the app verifies locally
(`src/lib/verify.ts`); set `VITE_ATTEST_API` to make figure edits reconcile
against the real deterministic engine.

Uploads go through the same seam: `analyzeDocument` POSTs the file/text to the
backend's `/analyze` endpoint, and `src/lib/buildDoc.ts` turns the returned prose
+ claims (with spans) + verdicts into the same `Block`/`Figure` shapes the demo
documents use. With no backend reachable, the upload falls back to client-side
numeric detection — the document still enters the library, honestly marked
untraced. When `attest serve` ships the built bundle it talks to the same origin,
so upload-and-tie-out works out of the box.

### Live verification against the backend

```bash
# 1. run the backend (from the repo root)
attest serve                 # FastAPI on http://127.0.0.1:8000

# 2. seed the Meridian filing into the running API
curl -X POST http://127.0.0.1:8000/tenants/meridian/ingest/xbrl \
  -H 'Content-Type: application/json' \
  -d @src/attest/ingestion/fixtures/meridian_q1_fy2026.json

# 3. point the web app at it
cd web
VITE_ATTEST_API=http://127.0.0.1:8000 npm run dev
```

With `VITE_ATTEST_API` set, editing a figure POSTs a one-claim document to
`/verify` and applies the engine's real verdict (`traced`/`conflict`/…); the
local echo is the optimistic first paint and the fallback if the call fails. The
backend already allows the Vite dev origin via CORS (override with
`ATTEST_CORS_ORIGINS`). Narrative, consensus, and calendar remain client-side
until those services exist (design-doc v2/v3).

## Structure

```
src/
  data/         seed data ported from the prototype (figures, narratives, trends,
                consensus/calendar) + the demo document library (library.ts)
  components/   TopBar, Sidebar, DocumentView, FigureModal, NarrativeModal,
                CommitmentModal, UploadModal, DocumentsManager (version + library
                management), Popover, TrendChart, Consensus, Calendar
  lib/          verify (client-side echo of the tie-out logic), buildDoc
                (assemble an uploaded draft into a renderable, versioned doc), icons
  api/          the client seam for the FastAPI backend (verify + analyze)
  store.tsx     central state (figures/narratives/commitments + document library,
                uploads, persistence) + actions
  types.ts      domain + document-block + library types
```
