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
import { DocumentsManager } from "./components/DocumentsManager";
import { Popover, type PopTarget } from "./components/Popover";
import type { DocKind, LibraryDoc } from "./types";

type View = string; // a library doc id, or "consensus" | "calendar"

function Workspace() {
  const store = useStore();
  // Open on the first document the workspace actually has — a returning user's
  // upload, or a seeded sample in tests. With an empty library this falls through
  // to the empty state rather than pointing at a document that isn't there.
  const [view, setView] = useState<View>(() => store.library[0]?.id ?? "");
  const [filter, setFilter] = useState("all");
  const [pop, setPop] = useState<PopTarget>(null);
  const [figModal, setFigModal] = useState<string | null>(null);
  const [narModal, setNarModal] = useState<string | null>(null);
  const [commitModal, setCommitModal] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  // When set, the upload modal files a new version of this document instead of
  // creating a fresh one.
  const [uploadTarget, setUploadTarget] = useState<LibraryDoc | null>(null);
  // Whether a fresh upload opens as a draft to verify or a prior disclosure.
  const [uploadRole, setUploadRole] = useState<"draft" | "reference">("draft");
  // Which source tab the reference modal opens on.
  const [uploadSource, setUploadSource] = useState<"edgar" | "historical" | "file" | undefined>(undefined);
  // When opening historical search from a category, scope it to that doc type.
  const [uploadDocKind, setUploadDocKind] = useState<DocKind | null>(null);
  // The documents manager is a full view in the stage (view === "manager"), not a
  // modal — scoped to a category and/or focused on a document via these.
  const [managerFocus, setManagerFocus] = useState<string | null>(null);
  const [managerKind, setManagerKind] = useState<DocKind | null>(null);
  const popHideTimer = useRef<number | undefined>(undefined);

  const isManager = view === "manager";
  const isDoc = view !== "consensus" && view !== "calendar" && !isManager;
  const activeDoc = isDoc ? store.library.find((d) => d.id === view) ?? null : null;

  // Reset the figure filter when leaving a document.
  useEffect(() => {
    if (!isDoc) setFilter("all");
  }, [isDoc]);

  // Escape closes any modal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setFigModal(null); setNarModal(null); setCommitModal(false);
        setUploadOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // Opening a modal dismisses the hover popover.
  const openFig = (id: string) => { setPop(null); setFigModal(id); };
  const openNar = (id: string) => { setPop(null); setNarModal(id); };

  // Upload entry points: a fresh document (as a draft or a prior disclosure), or a
  // new version of an existing one.
  const openUploadNew = (role: "draft" | "reference" = "draft", source?: "edgar" | "historical" | "file", kind?: DocKind) => {
    setUploadTarget(null); setUploadRole(role); setUploadSource(source); setUploadDocKind(kind ?? null); setUploadOpen(true);
  };
  const openUploadVersion = (doc: LibraryDoc) => { setUploadTarget(doc); setUploadOpen(true); };
  // Open the manager as a stage view — optionally focused on a document, or
  // scoped to a category.
  const openManager = (focus?: string, kind?: DocKind) => {
    setManagerFocus(focus ?? null); setManagerKind(kind ?? null); setView("manager");
  };

  return (
    <>
      <TopBar activeDoc={activeDoc?.id ?? null} filter={filter} setFilter={setFilter} />
      <div className="layout">
        <Sidebar view={view} setView={setView} onUpload={() => openUploadNew()} onManage={openManager} onOpenReference={(kind) => openUploadNew("reference", "historical", kind)} />
        <div className="stage">
          {activeDoc && (
            <div style={{ width: "100%" }}>
              <DocumentView
                key={activeDoc.id}
                doc={activeDoc}
                filter={filter}
                setPop={setPop}
                onFigureClick={openFig}
                onNarrativeClick={openNar}
                onCommitmentClick={() => setCommitModal(true)}
                onUploadVersion={() => openUploadVersion(activeDoc)}
                onManageVersions={() => openManager(activeDoc.id)}
              />
            </div>
          )}
          {isDoc && !activeDoc && (
            <div className="stage-empty">
              <div className="stage-empty-card">
                <h2>No documents yet</h2>
                <p>
                  Upload a press release, call transcript, or Q&amp;A draft and
                  Attest detects every figure, ties each one out against your filed
                  sources, and runs the disclosure checks.
                </p>
                <button className="btn go" onClick={() => openUploadNew()}>
                  + Add a document
                </button>
              </div>
            </div>
          )}
          {view === "consensus" && <Consensus />}
          {view === "calendar" && <Calendar />}
          {isManager && (
            <DocumentsManager
              focusDocId={managerFocus}
              focusKind={managerKind}
              onOpen={(id) => setView(id)}
              onUploadNew={openUploadNew}
              onUploadVersion={openUploadVersion}
            />
          )}
        </div>
      </div>

      <Popover
        target={pop}
        onEnter={() => window.clearTimeout(popHideTimer.current)}
        onLeave={() => { popHideTimer.current = window.setTimeout(() => setPop(null), 120); }}
      />

      {uploadOpen && (
        <UploadModal
          target={uploadTarget}
          initialRole={uploadRole}
          initialSource={uploadSource}
          initialDocTypes={uploadDocKind === "release" ? ["release"] : uploadDocKind === "script" ? ["transcript"] : undefined}
          onClose={() => { setUploadOpen(false); setUploadTarget(null); setUploadRole("draft"); setUploadSource(undefined); setUploadDocKind(null); }}
          onUploaded={(id) => setView(id)}
        />
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

export function App({ seedDemo = false }: { seedDemo?: boolean } = {}) {
  return (
    <StoreProvider seedDemo={seedDemo}>
      <Workspace />
    </StoreProvider>
  );
}
