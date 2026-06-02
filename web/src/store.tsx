import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { FIGURES } from "./data/figures";
import { COMMITMENTS, NARRATIVES } from "./data/narratives";
import { DEMO_LIBRARY } from "./data/library";
import type { Commitment, Figure, LibraryDoc, Narrative, VerdictState } from "./types";
import { evaluateEdit } from "./lib/verify";
import {
  buildVersionFromAnalysis,
  buildVersionLocally,
  newDocId,
  newVersionId,
  type BuiltVersion,
} from "./lib/buildDoc";
import { apiBaseUrl, client, type AnalyzeInput } from "./api/client";

type FigureMap = Record<string, Figure>;
type NarrativeMap = Record<string, Narrative>;

interface Store {
  figures: FigureMap;
  narratives: NarrativeMap;
  commitments: Commitment[];
  library: LibraryDoc[];
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
const isUploadFigureId = (id: string) => id.includes("::");

interface PersistedUploads {
  docs: LibraryDoc[];
  figures: FigureMap;
}

function loadUploads(): PersistedUploads {
  try {
    const raw = window.localStorage.getItem(UPLOADS_KEY);
    if (!raw) return { docs: [], figures: {} };
    const parsed = JSON.parse(raw) as Partial<PersistedUploads>;
    return { docs: parsed.docs ?? [], figures: parsed.figures ?? {} };
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

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const persisted = loadUploads();
  const [figures, setFigures] = useState<FigureMap>(() => ({
    ...clone(FIGURES),
    ...persisted.figures,
  }));
  const [narratives, setNarratives] = useState<NarrativeMap>(() => clone(NARRATIVES));
  const [commitments, setCommitments] = useState<Commitment[]>(() => clone(COMMITMENTS));
  const [library, setLibrary] = useState<LibraryDoc[]>(() => [
    ...clone(DEMO_LIBRARY),
    ...persisted.docs,
  ]);
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
      figures, narratives, commitments, library, toast,
      showToast, editFigure, bindFigure, resolveFigure, restoreFigure,
      removeFigure, addFigure, resolveNarrative, addressCommitment,
      uploadDocument, removeDoc, setActiveVersion, removeVersion, renameDoc,
    }),
    [
      figures, narratives, commitments, library, toast, showToast, editFigure, bindFigure,
      resolveFigure, restoreFigure, removeFigure, addFigure, resolveNarrative, addressCommitment,
      uploadDocument, removeDoc, setActiveVersion, removeVersion, renameDoc,
    ]
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): Store {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore must be used within StoreProvider");
  return ctx;
}
