import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { FIGURES } from "./data/figures";
import { COMMITMENTS, NARRATIVES } from "./data/narratives";
import type { Commitment, Figure, Narrative, VerdictState } from "./types";
import { evaluateEdit } from "./lib/verify";

type FigureMap = Record<string, Figure>;
type NarrativeMap = Record<string, Narrative>;

interface Store {
  figures: FigureMap;
  narratives: NarrativeMap;
  commitments: Commitment[];
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
}

const StoreContext = createContext<Store | null>(null);

// Deep-clone the seed data so edits never mutate the source modules (and a reload
// resets cleanly).
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [figures, setFigures] = useState<FigureMap>(() => clone(FIGURES));
  const [narratives, setNarratives] = useState<NarrativeMap>(() => clone(NARRATIVES));
  const [commitments, setCommitments] = useState<Commitment[]>(() => clone(COMMITMENTS));
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | undefined>(undefined);

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
      setFigures((prev) => {
        const next = { ...prev[id], cur: value.trim() || prev[id].cur };
        const verdict = evaluateEdit(next);
        return {
          ...prev,
          [id]: { ...next, st: verdict.st, tag: verdict.tag, editedFrom: verdict.editedFrom },
        };
      });
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
      const patch: Partial<Figure> = { st: "v", badge: "verified", tag: "✓" };
      if (id === "cloudgrowth") patch.cur = "29%";
      patchFigure(id, patch);
      showToast(
        id === "cloudgrowth"
          ? "Corrected to 29% and re-traced to the FY2025 10-K — updated in the release, script, and Q&A."
          : "Approved with safe-harbor language attached. Logged to the audit trail across all three documents."
      );
    },
    [patchFigure, showToast]
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

  const value = useMemo<Store>(
    () => ({
      figures, narratives, commitments, toast,
      showToast, editFigure, bindFigure, resolveFigure, restoreFigure,
      removeFigure, addFigure, resolveNarrative, addressCommitment,
    }),
    [
      figures, narratives, commitments, toast, showToast, editFigure, bindFigure,
      resolveFigure, restoreFigure, removeFigure, addFigure, resolveNarrative, addressCommitment,
    ]
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): Store {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore must be used within StoreProvider");
  return ctx;
}
