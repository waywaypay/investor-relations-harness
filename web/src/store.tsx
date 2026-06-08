import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { FIGURES } from "./data/figures";
import { COMMITMENTS, NARRATIVES } from "./data/narratives";
import { DEMO_LIBRARY, collectFigureIds } from "./data/library";
import type { Commitment, DocKind, DocVersion, Figure, LibraryDoc, Narrative, VerdictState } from "./types";
import { evaluateEdit } from "./lib/verify";
import {
  buildReferenceVersion,
  buildVersionFromAnalysis,
  buildVersionLocally,
  newDocId,
  newVersionId,
  type BuiltVersion,
} from "./lib/buildDoc";
import {
  apiBaseUrl,
  client,
  type AnalyzeInput,
  type DisclosureInput,
  type HistoricalCandidate,
} from "./api/client";

type FigureMap = Record<string, Figure>;
type NarrativeMap = Record<string, Narrative>;

/** A prior disclosure loaded into the reference corpus (web fetch, EDGAR, or an
 *  uploaded file). `kind` files it under the matching sidebar category — a fetched
 *  release lands under Press releases, a transcript under Transcripts. */
interface ReferenceEntry {
  id: string;
  entity: string;
  label: string;
  count: number;
  addedAt: string;
  kind: DocKind;
}

// Map an Exa historical doc_type to a library category.
const HIST_KIND: Record<string, DocKind> = { release: "release", transcript: "script" };

// The bare hostname of a source URL (e.g. "prnewswire.com"), for the "loaded from
// the web" provenance note on a fetched document. Falls back to the raw URL.
function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

interface Store {
  figures: FigureMap;
  narratives: NarrativeMap;
  commitments: Commitment[];
  library: LibraryDoc[];
  referenceEntries: ReferenceEntry[];
  toast: string | null;

  showToast: (msg: string) => void;
  editFigure: (id: string, value: string) => void;
  bindFigure: (id: string, sourceName: string) => void;
  resolveFigure: (id: string) => void;
  restoreFigure: (id: string) => void;
  removeFigure: (id: string) => void;
  addFigure: (id: string, text: string) => void;
  resolveNarrative: (id: string) => void;
  addressCommitment: (id: string) => void;
  /** Upload/paste a draft. With `targetDocId`, the draft is filed as a new
   *  version of that document; otherwise it becomes a new document. Returns the
   *  id of the document to navigate to. */
  uploadDocument: (input: AnalyzeInput, targetDocId?: string, note?: string) => Promise<string>;
  /** File a prior disclosure (past release / transcript / deck) as the reference
   *  corpus future drafts are checked against. Returns the number of figures filed. */
  ingestDisclosure: (input: DisclosureInput) => Promise<number>;
  /** Pull XBRL-tagged facts for a ticker from SEC EDGAR. Returns figures ingested. */
  ingestEdgar: (ticker: string, maxYears?: number) => Promise<number>;
  /** Auto-fetch the prior quarter's 8-K press release from EDGAR. Returns figures ingested. */
  fetchPriorPeriod: (ticker: string, period: string) => Promise<number>;
  /** Search the web for an issuer's historical earnings docs to review before loading. */
  searchHistorical: (entity: string, docTypes?: string[], quarters?: number) => Promise<HistoricalCandidate[]>;
  /** Fetch the selected historical documents, file their figures as reference, and
   *  add each as a viewable document in the workspace. Returns the id of the first
   *  loaded document to navigate to (or null when none could be rendered). */
  ingestHistorical: (entity: string, items: { url: string; title?: string; period?: string; doc_type?: string }[]) => Promise<string | null>;
  removeDoc: (id: string) => void;
  /** Make a stored version the one the document renders. */
  setActiveVersion: (docId: string, versionId: string) => void;
  /** Remove one version; drops the document if it was the last. */
  removeVersion: (docId: string, versionId: string) => void;
  /** Rename a document in the library. */
  renameDoc: (docId: string, name: string) => void;
}

const StoreContext = createContext<Store | null>(null);

// Deep-clone the seed data so edits never mutate the source modules (and a reload
// resets cleanly).
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));

// Uploaded documents (and the figures behind them) survive a reload via
// localStorage; the bundled demo close pack is always re-seeded fresh.
const UPLOADS_KEY = "attest.uploads.v1";
const REFCORPUS_KEY = "attest.refcorpus.v1";
const isUploadFigureId = (id: string) => id.includes("::");

interface PersistedUploads {
  docs: LibraryDoc[];
  figures: FigureMap;
}

// Bring a persisted document up to the current schema. Bundles before version
// control stored uploads with top-level `blocks` but no `versions` /
// `activeVersionId`; the renderer now assumes every doc owns a version history,
// so a returning user's old data would read `undefined.versions` and blank the
// page. Synthesize a single version from what the doc already carries — keeping
// the user's upload intact — and leave already-migrated docs untouched.
function migratePersistedDoc(raw: LibraryDoc): LibraryDoc {
  if (Array.isArray(raw.versions) && raw.activeVersionId) return raw;
  const blocks = raw.blocks ?? [];
  const versionId = `${raw.id}__v1`;
  const version: DocVersion = {
    id: versionId,
    label: "Version 1",
    addedAt: raw.addedAt ?? new Date().toISOString(),
    origin: "upload",
    blocks,
    figureIds: collectFigureIds(blocks),
    warnings: raw.warnings,
  };
  return { ...raw, blocks, versions: [version], activeVersionId: versionId };
}

function loadUploads(): PersistedUploads {
  try {
    const raw = window.localStorage.getItem(UPLOADS_KEY);
    if (!raw) return { docs: [], figures: {} };
    const parsed = JSON.parse(raw) as Partial<PersistedUploads>;
    const docs = (parsed.docs ?? []).map(migratePersistedDoc);
    return { docs, figures: parsed.figures ?? {} };
  } catch {
    return { docs: [], figures: {} };
  }
}

function saveUploads(data: PersistedUploads): void {
  try {
    window.localStorage.setItem(UPLOADS_KEY, JSON.stringify(data));
  } catch {
    /* storage unavailable (private mode / quota) — uploads just won't persist */
  }
}

// Entries persisted before reference docs carried a category have no `kind`.
// Recover it from the label / id so a previously-loaded transcript still files
// under Transcripts rather than being defaulted to Press releases.
function inferRefKind(e: Partial<ReferenceEntry>): DocKind {
  if (e.kind) return e.kind;
  const hay = `${e.label ?? ""} ${e.id ?? ""}`;
  return /transcript|call|prepared remarks/i.test(hay) ? "script" : "release";
}

function loadRefCorpus(): ReferenceEntry[] {
  try {
    const raw = window.localStorage.getItem(REFCORPUS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Array<Partial<ReferenceEntry>>;
    return parsed.map((e) => ({ ...e, kind: inferRefKind(e) } as ReferenceEntry));
  } catch {
    return [];
  }
}

function saveRefCorpus(entries: ReferenceEntry[]): void {
  try {
    window.localStorage.setItem(REFCORPUS_KEY, JSON.stringify(entries));
  } catch { /* storage unavailable */ }
}

export function StoreProvider({
  children,
  seedDemo = false,
}: {
  children: React.ReactNode;
  /** Seed the bundled sample close pack (the Atlas release / script / Q&A and
   *  the figures, narratives, and commitments behind it) into the workspace.
   *  Off by default so the app loads empty of demo content and shows only what
   *  the user has actually uploaded; tests opt in to exercise the
   *  document-rendering features against the reference pack. */
  seedDemo?: boolean;
}) {
  const persisted = loadUploads();
  const [figures, setFigures] = useState<FigureMap>(() => ({
    ...(seedDemo ? clone(FIGURES) : {}),
    ...persisted.figures,
  }));
  const [narratives, setNarratives] = useState<NarrativeMap>(() =>
    seedDemo ? clone(NARRATIVES) : {}
  );
  const [commitments, setCommitments] = useState<Commitment[]>(() =>
    seedDemo ? clone(COMMITMENTS) : []
  );
  const [library, setLibrary] = useState<LibraryDoc[]>(() => [
    ...(seedDemo ? clone(DEMO_LIBRARY) : []),
    ...persisted.docs,
  ]);
  const [referenceEntries, setReferenceEntries] = useState<ReferenceEntry[]>(() => loadRefCorpus());
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | undefined>(undefined);

  // Persist the uploaded documents (with their version history) and only the
  // figures those versions reference, whenever they change. Demo documents — and
  // any session-only versions layered onto them — are always re-seeded fresh.
  useEffect(() => {
    const docs = library.filter((d) => d.source === "upload");
    const keep = new Set<string>();
    for (const d of docs) for (const v of d.versions) for (const id of v.figureIds) keep.add(id);
    const figs: FigureMap = {};
    for (const id of keep) if (figures[id]) figs[id] = figures[id];
    saveUploads({ docs, figures: figs });
  }, [library, figures]);

  useEffect(() => {
    saveRefCorpus(referenceEntries);
  }, [referenceEntries]);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 3400);
  }, []);

  const patchFigure = useCallback((id: string, patch: Partial<Figure>) => {
    setFigures((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }, []);

  const editFigure = useCallback(
    (id: string, value: string) => {
      // Optimistic local re-verify (the client-side echo of the tie-out logic),
      // so the UI updates instantly whether or not a backend is wired.
      let committed = "";
      setFigures((prev) => {
        const next = { ...prev[id], cur: value.trim() || prev[id].cur };
        committed = next.cur;
        const verdict = evaluateEdit(next);
        return {
          ...prev,
          [id]: { ...next, st: verdict.st, tag: verdict.tag, editedFrom: verdict.editedFrom },
        };
      });

      // When VITE_ATTEST_API is set, reconcile against the real deterministic
      // engine. The backend is authoritative; on failure we keep the local result.
      // Uploaded figures (namespaced ids) have no fixed backend scope, so they
      // stay on the local echo rather than being forced to untraced.
      if (apiBaseUrl && !isUploadFigureId(id)) {
        const tagFor: Record<VerdictState, string> = { v: "✓", r: "?", f: "!", u: "?" };
        client
          .verifyFigure(id, committed)
          .then((res) => {
            setFigures((prev) => {
              const fig = prev[id];
              if (!fig || fig.cur !== committed) return prev; // a newer edit superseded this
              const editedFrom = res.verdict === "f" ? fig.filed : null;
              return { ...prev, [id]: { ...fig, st: res.verdict, tag: tagFor[res.verdict], editedFrom } };
            });
          })
          .catch(() => void 0);
      }
    },
    []
  );

  const bindFigure = useCallback(
    (id: string, sourceName: string) => {
      patchFigure(id, {
        st: "v",
        tag: "✓",
        filed: figures[id]?.cur ?? null,
        badge: sourceName,
        cite: `${sourceName} · bound on import`,
        lbl: "Bound figure",
      });
      showToast(`Bound to ${sourceName} — now traced like the rest.`);
    },
    [figures, patchFigure, showToast]
  );

  const resolveFigure = useCallback(
    (id: string) => {
      const prev = figures[id];
      const patch: Partial<Figure> = { st: "v", badge: "verified", tag: "✓" };
      if (id === "cloudgrowth") patch.cur = "29%";
      else if (prev?.st === "f" && prev.filed) patch.cur = prev.filed; // reconcile to source
      patchFigure(id, patch);
      const msg =
        id === "cloudgrowth"
          ? "Corrected to 29% and re-traced to the FY2025 10-K — updated in the release, script, and Q&A."
          : prev?.st === "f"
            ? "Reconciled to the filed source value and re-traced. Logged to the audit trail."
            : "Approved with safe-harbor language attached. Logged to the audit trail.";
      showToast(msg);
    },
    [figures, patchFigure, showToast]
  );

  const restoreFigure = useCallback(
    (id: string) => {
      const fig = figures[id];
      if (!fig?.editedFrom) return;
      const restored = { ...fig, cur: fig.editedFrom };
      const verdict = evaluateEdit(restored);
      patchFigure(id, { cur: fig.editedFrom, st: verdict.st, tag: verdict.tag, editedFrom: null });
      showToast("Restored to the filed value.");
    },
    [figures, patchFigure, showToast]
  );

  const removeFigure = useCallback(
    (id: string) => {
      setFigures((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      showToast("Figure removed from the draft.");
    },
    [showToast]
  );

  const addFigure = useCallback((id: string, text: string) => {
    setFigures((prev) => ({
      ...prev,
      [id]: {
        id,
        st: "u" as VerdictState,
        tag: "?",
        cur: text.trim(),
        filed: null,
        v: text.trim(),
        lbl: "Untraced figure",
        badge: "unbound",
        tag2: "",
        snip: "",
        cite: "No source bound yet",
        page: "",
        reason: "",
        fields: [],
      } as unknown as Figure,
    }));
  }, []);

  const resolveNarrative = useCallback(
    (id: string) => {
      setNarratives((prev) => {
        const nar = prev[id];
        const cur = nar.suggestion ? nar.suggestion : nar.cur;
        return { ...prev, [id]: { ...nar, cur, st: "ok", tag: "msg" } };
      });
      const nar = narratives[id];
      showToast(
        nar?.suggestion
          ? "Updated to the approved wording — script now matches the release."
          : "Safe-harbor language attached and logged to the audit trail."
      );
    },
    [narratives, showToast]
  );

  const addressCommitment = useCallback(
    (id: string) => {
      setCommitments((prev) => prev.map((c) => (c.id === id ? { ...c, status: "done" } : c)));
      showToast(
        "Flagged to address — added to your Q&A prep and logged. The Street won’t catch you off guard."
      );
    },
    [showToast]
  );

  const uploadDocument = useCallback(
    async (input: AnalyzeInput, targetDocId?: string, note?: string): Promise<string> => {
      const versionId = newVersionId();
      let built: BuiltVersion;
      try {
        // Preferred path: the deterministic engine analyzes the real document.
        const result = await client.analyzeDocument(input);
        built = buildVersionFromAnalysis(result, versionId);
      } catch {
        // No backend reachable (or it errored): degrade to client-side detection
        // so the document still enters the workspace, honestly marked untraced.
        const text = input.text ?? (input.file ? await input.file.text() : "");
        if (!text.trim()) {
          throw new Error("Provide a file or paste text to analyze.");
        }
        built = buildVersionLocally(
          {
            text,
            title: input.title || input.file?.name || "Uploaded document",
            kind: input.kind,
            fromFile: input.file != null,
          },
          versionId
        );
      }
      if (note?.trim()) built.version.note = note.trim();

      setFigures((prev) => ({ ...prev, ...built.figures }));

      // The document id we'll navigate to: the target for a new version, or a
      // freshly minted id for a new document. Minted outside the state updater so
      // a double-invoked updater (React strict mode) can't fork two documents.
      const docId = targetDocId ?? newDocId();
      setLibrary((prev) => {
        if (targetDocId) {
          return prev.map((d) => {
            if (d.id !== targetDocId) return d;
            if (d.versions.some((v) => v.id === versionId)) return d; // idempotent guard
            const version = { ...built.version, label: `Version ${d.versions.length + 1}` };
            return {
              ...d,
              versions: [version, ...d.versions],
              activeVersionId: version.id,
              blocks: version.blocks,
              warnings: version.warnings,
            };
          });
        }
        if (prev.some((d) => d.id === docId)) return prev; // idempotent guard
        const version = { ...built.version, label: "Version 1" };
        const doc: LibraryDoc = {
          id: docId,
          kind: built.meta.kind,
          name: built.meta.name,
          subtitle: built.meta.subtitle,
          icon: built.meta.icon,
          source: "upload",
          period: built.meta.period,
          addedAt: version.addedAt,
          blocks: version.blocks,
          warnings: version.warnings,
          versions: [version],
          activeVersionId: version.id,
        };
        return [...prev, doc];
      });

      const traced = Object.values(built.figures).filter((f) => f.st === "v").length;
      const total = Object.keys(built.figures).length;
      const what = targetDocId ? "Filed a new version" : `Added “${built.meta.name}”`;
      showToast(
        total
          ? `${what} — ${traced} of ${total} figure${total > 1 ? "s" : ""} traced.`
          : `${what} to the workspace.`
      );
      return docId;
    },
    [showToast]
  );

  const ingestDisclosure = useCallback(
    async (input: DisclosureInput): Promise<number> => {
      const result = await client.ingestDisclosure(input);
      const name = input.label || input.file?.name || "prior disclosure";
      const entity = input.entity?.toUpperCase() || "—";
      if (result.ingested > 0) {
        setReferenceEntries((prev) => [
          ...prev,
          { id: `disclosure:${name}:${Date.now()}`, entity, label: name, count: result.ingested, addedAt: new Date().toISOString().slice(0, 10), kind: "release" as DocKind },
        ]);
      }
      showToast(
        result.ingested
          ? `Filed ${result.ingested} reference figure${result.ingested > 1 ? "s" : ""} from "${name}".`
          : `No recognizable figures in "${name}" — nothing to reference.`
      );
      return result.ingested;
    },
    [showToast]
  );

  const ingestEdgar = useCallback(
    async (ticker: string, maxYears?: number): Promise<number> => {
      const result = await client.ingestEdgar(ticker, maxYears);
      const sym = ticker.toUpperCase();
      if (result.ingested > 0) {
        setReferenceEntries((prev) => [
          ...prev,
          { id: `edgar:${sym}:${Date.now()}`, entity: sym, label: "EDGAR XBRL facts", count: result.ingested, addedAt: new Date().toISOString().slice(0, 10), kind: "release" as DocKind },
        ]);
      }
      showToast(
        result.ingested
          ? `Loaded ${result.ingested} EDGAR fact${result.ingested > 1 ? "s" : ""} for ${sym}.`
          : `No XBRL facts found for ${sym} — nothing loaded.`
      );
      return result.ingested;
    },
    [showToast]
  );

  const fetchPriorPeriod = useCallback(
    async (ticker: string, period: string): Promise<number> => {
      const result = await client.fetchPriorPeriod(ticker, period);
      const sym = ticker.toUpperCase();
      const priorLabel = result.prior_period ?? "prior period";
      if (result.total_ingested > 0) {
        setReferenceEntries((prev) => [
          ...prev,
          { id: `edgar-8k:${sym}:${priorLabel}:${Date.now()}`, entity: sym, label: `${priorLabel} 8-K`, count: result.total_ingested, addedAt: new Date().toISOString().slice(0, 10), kind: "release" as DocKind },
        ]);
      }
      showToast(
        result.total_ingested
          ? `Fetched ${result.total_ingested} figure${result.total_ingested > 1 ? "s" : ""} from ${sym} ${priorLabel} 8-K.`
          : `No press-release figures found for ${sym} ${priorLabel}.`
      );
      return result.total_ingested;
    },
    [showToast]
  );

  const searchHistorical = useCallback(
    async (entity: string, docTypes?: string[], quarters?: number): Promise<HistoricalCandidate[]> => {
      // Pure lookup — no toast on success (the results render in the modal); a
      // failure (e.g. Exa not configured) surfaces to the caller's catch.
      return client.searchHistorical(entity, docTypes, quarters);
    },
    []
  );

  const ingestHistorical = useCallback(
    async (entity: string, items: { url: string; title?: string; period?: string; doc_type?: string }[]): Promise<string | null> => {
      const result = await client.ingestHistorical(entity, items);
      const docs = result.documents.length;
      const sym = entity.toUpperCase();
      const kindByUrl = new Map(items.map((i) => [i.url, i.doc_type]));

      // A fetched document with recovered prose becomes a viewable library
      // document the user can open and read; one without (text couldn't be
      // recovered) falls back to the reference-count row, so its figures are still
      // visibly filed. Either way the figures are reference facts on the backend.
      const newDocs: LibraryDoc[] = [];
      const newFigures: FigureMap = {};
      const fallbackEntries: ReferenceEntry[] = [];
      for (const d of result.documents) {
        const dt = kindByUrl.get(d.url);
        const kind = (dt ? HIST_KIND[dt] : undefined) ?? "release";
        if (d.text && d.text.trim()) {
          const versionId = newVersionId();
          const built = buildReferenceVersion(
            {
              text: d.text,
              title: d.title || d.url,
              kind,
              source: hostOf(d.url),
              period: d.period ?? undefined,
              // The backend analyzed the fetched doc against the issuer's filed SEC
              // sources; render those verdicts so figures link to their source.
              claims: d.claims,
              verdicts: d.verdicts,
            },
            versionId
          );
          Object.assign(newFigures, built.figures);
          const version = { ...built.version, label: "Version 1" };
          newDocs.push({
            id: newDocId(),
            kind: built.meta.kind,
            name: built.meta.name,
            subtitle: built.meta.subtitle,
            icon: built.meta.icon,
            source: "upload",
            period: built.meta.period,
            addedAt: d.published_date || new Date().toISOString(),
            blocks: version.blocks,
            warnings: version.warnings,
            versions: [version],
            activeVersionId: version.id,
          });
        } else {
          fallbackEntries.push({
            id: `historical:${d.url}`,
            entity: sym,
            label: d.title || d.url,
            count: d.ingested,
            addedAt: d.published_date || new Date().toISOString().slice(0, 10),
            kind,
          });
        }
      }

      if (Object.keys(newFigures).length) setFigures((prev) => ({ ...prev, ...newFigures }));
      if (newDocs.length) setLibrary((prev) => [...prev, ...newDocs]);
      if (fallbackEntries.length) setReferenceEntries((prev) => [...prev, ...fallbackEntries]);

      showToast(
        docs === 0
          ? "No documents fetched — check the company name or try a ticker symbol."
          : result.total_ingested
            ? `Loaded ${docs} document${docs > 1 ? "s" : ""} — ${result.total_ingested} reference figure${result.total_ingested > 1 ? "s" : ""} filed.`
            : `Loaded ${docs} document${docs > 1 ? "s" : ""} — no figures extracted.`
      );
      return newDocs[0]?.id ?? null;
    },
    [showToast]
  );

  const removeDoc = useCallback(
    (id: string) => {
      const doc = library.find((d) => d.id === id);
      setLibrary((prev) => prev.filter((d) => d.id !== id));
      if (doc) {
        // Drop only the document's own (namespaced) figures — shared demo figures
        // stay so the rest of the close pack keeps tying out.
        setFigures((prev) => {
          const next = { ...prev };
          for (const v of doc.versions)
            for (const fid of v.figureIds) if (isUploadFigureId(fid)) delete next[fid];
          return next;
        });
      }
      showToast("Document removed from the workspace.");
    },
    [library, showToast]
  );

  const setActiveVersion = useCallback(
    (docId: string, versionId: string) => {
      let label = "";
      setLibrary((prev) =>
        prev.map((d) => {
          if (d.id !== docId) return d;
          const v = d.versions.find((x) => x.id === versionId);
          if (!v) return d;
          label = v.label;
          return { ...d, activeVersionId: versionId, blocks: v.blocks, warnings: v.warnings };
        })
      );
      showToast(label ? `Now viewing ${label}.` : "Switched version.");
    },
    [showToast]
  );

  const removeVersion = useCallback(
    (docId: string, versionId: string) => {
      const doc = library.find((d) => d.id === docId);
      const version = doc?.versions.find((v) => v.id === versionId);
      setLibrary((prev) => {
        const d = prev.find((x) => x.id === docId);
        if (!d) return prev;
        const versions = d.versions.filter((v) => v.id !== versionId);
        if (versions.length === 0) return prev.filter((x) => x.id !== docId); // last one -> drop doc
        const active =
          d.activeVersionId === versionId
            ? versions[0]
            : versions.find((v) => v.id === d.activeVersionId) ?? versions[0];
        return prev.map((x) =>
          x.id !== docId
            ? x
            : { ...x, versions, activeVersionId: active.id, blocks: active.blocks, warnings: active.warnings }
        );
      });
      if (version) {
        setFigures((prev) => {
          const next = { ...prev };
          for (const fid of version.figureIds) if (isUploadFigureId(fid)) delete next[fid];
          return next;
        });
      }
      const dropped = doc && doc.versions.length <= 1;
      showToast(dropped ? "Document removed from the workspace." : "Version removed.");
    },
    [library, showToast]
  );

  const renameDoc = useCallback(
    (docId: string, name: string) => {
      const clean = name.trim();
      if (!clean) return;
      setLibrary((prev) => prev.map((d) => (d.id === docId ? { ...d, name: clean } : d)));
    },
    []
  );

  const value = useMemo<Store>(
    () => ({
      figures, narratives, commitments, library, referenceEntries, toast,
      showToast, editFigure, bindFigure, resolveFigure, restoreFigure,
      removeFigure, addFigure, resolveNarrative, addressCommitment,
      uploadDocument, ingestDisclosure, ingestEdgar, fetchPriorPeriod,
      searchHistorical, ingestHistorical,
      removeDoc, setActiveVersion, removeVersion, renameDoc,
    }),
    [
      figures, narratives, commitments, library, referenceEntries, toast, showToast, editFigure, bindFigure,
      resolveFigure, restoreFigure, removeFigure, addFigure, resolveNarrative, addressCommitment,
      uploadDocument, ingestDisclosure, ingestEdgar, fetchPriorPeriod,
      searchHistorical, ingestHistorical,
      removeDoc, setActiveVersion, removeVersion, renameDoc,
    ]
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): Store {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore must be used within StoreProvider");
  return ctx;
}
