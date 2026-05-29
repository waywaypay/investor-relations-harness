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
```

> In a remote/container dev environment, the server binds to `0.0.0.0` (see
> `vite.config.ts`); use your platform's port forwarding to reach `:5173`.

## What's wired to what

This is a **standalone faithful port** of the prototype — it runs entirely on
client-side data (`src/data/`), so every surface works visually without a
backend. The figure-verification path mirrors the Python engine's behaviour
closely enough for the live-edit experience (`src/lib/verify.ts`).

The **API seam** lives in `src/api/client.ts`. Components depend on that typed
interface, not on the transport, so pointing the verification path at the real
FastAPI backend (`attest serve`) later is a localized change: implement
`AttestClient` with `fetch()` against `VITE_ATTEST_API` and swap it in. The
backend today only covers figure verification; narrative, consensus, and
calendar remain client-side until those services exist (see the design doc's v2/v3).

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
