# Attest — web workspace

A React + TypeScript port of the Attest disclosure-drafting workspace: the
editor, figure-verification popovers/modals, narrative & commitment checks,
Street consensus, and the earnings calendar.

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
consensus build, calendar, the script's narrative bar).

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
                documents as typed blocks, consensus/calendar)
  components/   TopBar, Sidebar, DocumentView, FigureModal, NarrativeModal,
                CommitmentModal, Popover, TrendChart, Consensus, Calendar
  lib/          verify (client-side echo of the tie-out logic), icons
  api/          the client seam for the FastAPI backend
  store.tsx     central state (figures/narratives/commitments) + actions
  types.ts      domain + document-block types
```
