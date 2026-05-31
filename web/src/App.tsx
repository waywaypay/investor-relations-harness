import { useEffect, useRef, useState } from "react";
import { StoreProvider, useStore } from "./store";
import { TopBar } from "./components/TopBar";
import { Sidebar } from "./components/Sidebar";
import { DocumentView } from "./components/DocumentView";
import { FigureModal } from "./components/FigureModal";
import { UploadModal } from "./components/UploadModal";
import { SourcesModal } from "./components/SourcesModal";
import { Popover, type PopTarget } from "./components/Popover";

function EmptyState({ onOpenUpload, onOpenSources }: { onOpenUpload: () => void; onOpenSources: () => void }) {
  const store = useStore();
  return (
    <div className="empty-state">
      <div className="es-card">
        <div className="es-eyebrow">Get started</div>
        <h1>Upload a disclosure draft</h1>
        <p>
          Attest reads your earnings release, prepared remarks, or Q&amp;A, finds every figure, and
          ties each one out against your filed sources — traced, flagged, or untraced. Nothing here
          is preloaded; every document is one you add.
        </p>
        <div className="es-actions">
          <button className="btn go" onClick={onOpenUpload}>
            + Upload a document
          </button>
          <button className="btn" onClick={onOpenSources}>
            {store.facts.length > 0
              ? `Sources · ${store.facts.length} filed facts`
              : "Set up filed sources"}
          </button>
        </div>
        {store.facts.length === 0 && (
          <div className="es-hint">
            Tip: load the bundled demo filing under <b>Sources</b> so your first upload has numbers to
            trace against.
          </div>
        )}
      </div>
    </div>
  );
}

function Workspace() {
  const store = useStore();
  const [filter, setFilter] = useState("all");
  const [pop, setPop] = useState<PopTarget>(null);
  const [figClaim, setFigClaim] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const popHideTimer = useRef<number | undefined>(undefined);

  // Escape closes any modal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setFigClaim(null);
        setUploadOpen(false);
        setSourcesOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const openFig = (claimId: string) => {
    setPop(null);
    setFigClaim(claimId);
  };

  const doc = store.active;
  const verdict = doc && figClaim ? doc.verdicts[figClaim] : null;
  const claim = doc && figClaim ? doc.claims.find((c) => c.claim_id === figClaim) : null;

  return (
    <>
      <TopBar
        filter={filter}
        setFilter={setFilter}
        onOpenUpload={() => setUploadOpen(true)}
        onOpenSources={() => setSourcesOpen(true)}
      />
      <div className="layout">
        <Sidebar onOpenUpload={() => setUploadOpen(true)} onOpenSources={() => setSourcesOpen(true)} />
        <div className="stage">
          {doc ? (
            <div style={{ width: "100%", maxWidth: 680 }}>
              <DocumentView
                key={doc.localId}
                doc={doc}
                filter={filter}
                setPop={setPop}
                onFigureClick={openFig}
              />
            </div>
          ) : (
            <EmptyState
              onOpenUpload={() => setUploadOpen(true)}
              onOpenSources={() => setSourcesOpen(true)}
            />
          )}
        </div>
      </div>

      <Popover
        target={pop}
        onEnter={() => window.clearTimeout(popHideTimer.current)}
        onLeave={() => {
          popHideTimer.current = window.setTimeout(() => setPop(null), 120);
        }}
      />

      {verdict && claim && (
        <FigureModal verdict={verdict} claim={claim} onClose={() => setFigClaim(null)} />
      )}
      {uploadOpen && <UploadModal onClose={() => setUploadOpen(false)} />}
      {sourcesOpen && <SourcesModal onClose={() => setSourcesOpen(false)} />}

      <div className={`toast ${store.toast ? "show" : ""}`}>{store.toast}</div>
    </>
  );
}

export function App() {
  return (
    <StoreProvider>
      <Workspace />
    </StoreProvider>
  );
}
