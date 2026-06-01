import { useEffect, useRef, useState } from "react";
import { StoreProvider, useStore } from "./store";
import { TopBar } from "./components/TopBar";
import { Sidebar } from "./components/Sidebar";
import { DocumentView } from "./components/DocumentView";
import { Consensus } from "./components/Consensus";
import { Calendar } from "./components/Calendar";
import { FigureModal } from "./components/FigureModal";
import { NarrativeModal } from "./components/NarrativeModal";
import { CommitmentModal } from "./components/CommitmentModal";
import { UploadModal } from "./components/UploadModal";
import { Popover, type PopTarget } from "./components/Popover";

type View = string; // a library doc id, or "consensus" | "calendar"

function Workspace() {
  const store = useStore();
  const [view, setView] = useState<View>("release");
  const [filter, setFilter] = useState("all");
  const [pop, setPop] = useState<PopTarget>(null);
  const [figModal, setFigModal] = useState<string | null>(null);
  const [narModal, setNarModal] = useState<string | null>(null);
  const [commitModal, setCommitModal] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const popHideTimer = useRef<number | undefined>(undefined);

  const isDoc = view !== "consensus" && view !== "calendar";
  const activeDoc = isDoc ? store.library.find((d) => d.id === view) ?? null : null;

  // Reset the figure filter when leaving a document.
  useEffect(() => {
    if (!isDoc) setFilter("all");
  }, [isDoc]);

  // Escape closes any modal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setFigModal(null); setNarModal(null); setCommitModal(false); setUploadOpen(false); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // Opening a modal dismisses the hover popover.
  const openFig = (id: string) => { setPop(null); setFigModal(id); };
  const openNar = (id: string) => { setPop(null); setNarModal(id); };

  return (
    <>
      <TopBar activeDoc={activeDoc?.id ?? null} filter={filter} setFilter={setFilter} />
      <div className="layout">
        <Sidebar view={view} setView={setView} onUpload={() => setUploadOpen(true)} />
        <div className="stage">
          {activeDoc && (
            <div style={{ width: "100%", maxWidth: 680 }}>
              <DocumentView
                key={activeDoc.id}
                doc={activeDoc}
                filter={filter}
                setPop={setPop}
                onFigureClick={openFig}
                onNarrativeClick={openNar}
                onCommitmentClick={() => setCommitModal(true)}
              />
            </div>
          )}
          {view === "consensus" && <Consensus />}
          {view === "calendar" && <Calendar />}
        </div>
      </div>

      <Popover
        target={pop}
        onEnter={() => window.clearTimeout(popHideTimer.current)}
        onLeave={() => { popHideTimer.current = window.setTimeout(() => setPop(null), 120); }}
      />

      {uploadOpen && (
        <UploadModal onClose={() => setUploadOpen(false)} onUploaded={(id) => setView(id)} />
      )}

      {figModal && store.figures[figModal] && (
        <FigureModal fig={store.figures[figModal]} onClose={() => setFigModal(null)} />
      )}
      {narModal && store.narratives[narModal] && (
        <NarrativeModal nar={store.narratives[narModal]} onClose={() => setNarModal(null)} />
      )}
      {commitModal && store.commitments[0] && (
        <CommitmentModal commit={store.commitments[0]} onClose={() => setCommitModal(false)} />
      )}

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
