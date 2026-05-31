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
import { Popover, type PopTarget } from "./components/Popover";
import type { DocKind } from "./types";

type View = DocKind | "consensus" | "calendar";

function Workspace() {
  const store = useStore();
  const [view, setView] = useState<View>("release");
  const [filter, setFilter] = useState("all");
  const [pop, setPop] = useState<PopTarget>(null);
  const [figModal, setFigModal] = useState<string | null>(null);
  const [narModal, setNarModal] = useState<string | null>(null);
  const [commitModal, setCommitModal] = useState(false);
  const popHideTimer = useRef<number | undefined>(undefined);

  // Reset the figure filter when leaving a document.
  useEffect(() => {
    if (view === "consensus" || view === "calendar") setFilter("all");
  }, [view]);

  // Escape closes any modal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setFigModal(null); setNarModal(null); setCommitModal(false); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const isDoc = view !== "consensus" && view !== "calendar";
  const activeDoc = isDoc ? (view as DocKind) : null;

  // Opening a modal dismisses the hover popover.
  const openFig = (id: string) => { setPop(null); setFigModal(id); };
  const openNar = (id: string) => { setPop(null); setNarModal(id); };

  return (
    <>
      <TopBar activeDoc={activeDoc} filter={filter} setFilter={setFilter} />
      <div className="layout">
        <Sidebar view={view} setView={setView} />
        <div className="stage">
          {isDoc && (
            <div style={{ width: "100%", maxWidth: 680 }}>
              <DocumentView
                key={view}
                docId={view as DocKind}
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
