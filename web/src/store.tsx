import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { client, type GuidanceInput } from "./api/client";
import type {
  AnalyzeResult,
  ApiVerdict,
  FactRow,
  UploadedDoc,
  UploadInput,
} from "./types";

interface Store {
  documents: UploadedDoc[];
  activeId: string | null;
  facts: FactRow[];
  busy: boolean;
  toast: string | null;

  active: UploadedDoc | null;
  showToast: (msg: string) => void;
  setActive: (localId: string) => void;
  analyzeUpload: (input: UploadInput) => Promise<boolean>;
  removeDocument: (localId: string) => void;
  loadDemoFacts: () => Promise<void>;
  ingestXbrl: (instance: unknown, label?: string) => Promise<void>;
  ingestGuidance: (input: GuidanceInput) => Promise<void>;
  refreshFacts: () => Promise<void>;
}

const StoreContext = createContext<Store | null>(null);

let counter = 0;
const nextId = () => `doc-${Date.now().toString(36)}-${(counter++).toString(36)}`;

function toUploadedDoc(res: AnalyzeResult, sourceText: string, localId?: string): UploadedDoc {
  const verdicts: Record<string, ApiVerdict> = {};
  for (const v of res.verdicts) verdicts[v.claim_id] = v;
  return {
    localId: localId ?? nextId(),
    title: res.title,
    kind: res.kind,
    entity: res.entity,
    period: res.period ?? null,
    text: res.text,
    claims: res.claims,
    verdicts,
    findings: res.findings,
    counts: res.counts,
    publishable: res.publishable,
    warnings: res.warnings,
    uploadedAt: Date.now(),
    sourceText,
  };
}

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [documents, setDocuments] = useState<UploadedDoc[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [facts, setFacts] = useState<FactRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | undefined>(undefined);
  // Mirror of `documents` so async actions can read the latest list without
  // depending on it (avoids stale closures in re-verification).
  const docsRef = useRef<UploadedDoc[]>(documents);
  docsRef.current = documents;

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 4200);
  }, []);

  const refreshFacts = useCallback(async () => {
    try {
      setFacts(await client.listFacts());
    } catch {
      /* backend may be unreachable in offline dev; leave facts as-is */
    }
  }, []);

  // Load whatever's already in the store on mount (e.g. a pre-seeded instance).
  useEffect(() => {
    void refreshFacts();
  }, [refreshFacts]);

  const setActive = useCallback((localId: string) => setActiveId(localId), []);

  const analyzeUpload = useCallback(
    async (input: UploadInput): Promise<boolean> => {
      setBusy(true);
      try {
        const res = await client.analyze(input);
        const sourceText = input.text ?? res.text;
        const doc = toUploadedDoc(res, sourceText);
        setDocuments((prev) => [...prev, doc]);
        setActiveId(doc.localId);
        void refreshFacts();
        const traced = res.counts.traced ?? 0;
        const total = res.verdicts.length;
        showToast(
          total === 0
            ? `Analyzed “${res.title}” — no figures detected.`
            : `Analyzed “${res.title}” — ${traced} of ${total} figure${total > 1 ? "s" : ""} traced.`
        );
        return true;
      } catch (err) {
        showToast(`Upload failed: ${(err as Error).message}`);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [refreshFacts, showToast]
  );

  const removeDocument = useCallback((localId: string) => {
    setDocuments((prev) => {
      const next = prev.filter((d) => d.localId !== localId);
      setActiveId((cur) => (cur === localId ? (next[0]?.localId ?? null) : cur));
      return next;
    });
  }, []);

  // After the filed sources change, re-run every uploaded draft through the engine
  // so verdicts reflect the new facts — keeping the workspace honest.
  const reanalyzeAll = useCallback(async () => {
    const current = docsRef.current;
    if (!current.length) return;
    const updated = await Promise.all(
      current.map(async (doc) => {
        try {
          const res = await client.analyze({
            text: doc.sourceText,
            title: doc.title,
            kind: doc.kind as UploadInput["kind"],
            entity: doc.entity,
            period: doc.period ?? undefined,
          });
          return toUploadedDoc(res, doc.sourceText, doc.localId);
        } catch {
          return doc;
        }
      })
    );
    setDocuments(updated);
  }, []);

  const afterSourceChange = useCallback(
    async (report: { ingested: number; source: string }) => {
      await refreshFacts();
      await reanalyzeAll();
      showToast(
        `Ingested ${report.ingested} filed fact${report.ingested === 1 ? "" : "s"} from ${report.source}. Drafts re-verified.`
      );
    },
    [refreshFacts, reanalyzeAll, showToast]
  );

  const loadDemoFacts = useCallback(async () => {
    setBusy(true);
    try {
      const report = await client.ingestDemo();
      await afterSourceChange(report);
    } catch (err) {
      showToast(`Could not load demo facts: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }, [afterSourceChange, showToast]);

  const ingestXbrl = useCallback(
    async (instance: unknown) => {
      setBusy(true);
      try {
        const report = await client.ingestXbrl(instance);
        await afterSourceChange(report);
      } catch (err) {
        showToast(`XBRL ingest failed: ${(err as Error).message}`);
      } finally {
        setBusy(false);
      }
    },
    [afterSourceChange, showToast]
  );

  const ingestGuidance = useCallback(
    async (input: GuidanceInput) => {
      setBusy(true);
      try {
        const report = await client.ingestGuidance(input);
        await afterSourceChange(report);
      } catch (err) {
        showToast(`Guidance ingest failed: ${(err as Error).message}`);
      } finally {
        setBusy(false);
      }
    },
    [afterSourceChange, showToast]
  );

  const active = useMemo(
    () => documents.find((d) => d.localId === activeId) ?? null,
    [documents, activeId]
  );

  const value = useMemo<Store>(
    () => ({
      documents,
      activeId,
      facts,
      busy,
      toast,
      active,
      showToast,
      setActive,
      analyzeUpload,
      removeDocument,
      loadDemoFacts,
      ingestXbrl,
      ingestGuidance,
      refreshFacts,
    }),
    [
      documents,
      activeId,
      facts,
      busy,
      toast,
      active,
      showToast,
      setActive,
      analyzeUpload,
      removeDocument,
      loadDemoFacts,
      ingestXbrl,
      ingestGuidance,
      refreshFacts,
    ]
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): Store {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore must be used within StoreProvider");
  return ctx;
}
