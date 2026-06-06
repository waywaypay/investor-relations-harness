import { useState } from "react";
import { useStore } from "../store";
import type { Block, DocKind, Figure, LibraryDoc } from "../types";

type View = string; // a library doc id, or "consensus" | "calendar"

function figureIds(blocks: Block[]): string[] {
  const ids: string[] = [];
  for (const b of blocks) {
    if (b.kind === "p") b.parts.forEach((p) => p.kind === "fig" && ids.push(p.id));
    if (b.kind === "qa") b.a.forEach((p) => p.kind === "fig" && ids.push(p.id));
  }
  return ids;
}

// A document's worst-case status, shown as one small dot: a conflict outranks a
// review, which outranks all-traced; no figures reads as neutral.
function statusOf(doc: LibraryDoc, figs: Record<string, Figure>): "v" | "r" | "f" | "none" {
  let seen = false, review = false;
  for (const id of figureIds(doc.blocks)) {
    const fig = figs[id];
    if (!fig) continue;
    seen = true;
    if (fig.st === "f" || fig.st === "u") return "f";
    if (fig.st === "r") review = true;
  }
  return !seen ? "none" : review ? "r" : "v";
}

const ICON_RELEASE =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v4h4"/><path d="M9 12h6M9 16h6"/></svg>';
const ICON_SCRIPT =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
const ICON_QA =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M4 5h16v11H8l-4 4z"/></svg>';
const ICON_OTHER =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M7 2h8l4 4v16H7z"/></svg>';
const ICON_CONSENSUS =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18"/><rect x="5" y="11" width="3" height="7"/><rect x="10.5" y="7" width="3" height="11"/><rect x="16" y="13" width="3" height="5"/></svg>';
const ICON_CALENDAR =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>';

// Document categories, in display order. Labels are plain-language and distinct
// from any document name, so a first-time user reads the workspace at a glance.
const KIND_ORDER: DocKind[] = ["release", "script", "qa", "other"];
const KIND_META: Record<DocKind, { label: string; icon: string }> = {
  release: { label: "Press releases", icon: ICON_RELEASE },
  script: { label: "Transcripts", icon: ICON_SCRIPT },
  qa: { label: "Analyst Q&A", icon: ICON_QA },
  other: { label: "Other documents", icon: ICON_OTHER },
};

export function Sidebar({
  view,
  setView,
  onUpload,
  onManage,
}: {
  view: View;
  setView: (v: View) => void;
  onUpload: () => void;
  /** Open the documents manager — focused on a document, or scoped to a category. */
  onManage: (focusDocId?: string, kind?: DocKind) => void;
}) {
  const store = useStore();
  // Categories are collapsed by default — the rail shows just the types; a click
  // on the chevron reveals that category's documents.
  const [openKinds, setOpenKinds] = useState<Set<DocKind>>(new Set());
  const toggleKind = (k: DocKind) =>
    setOpenKinds((prev) => {
      const next = new Set(prev);
      next.has(k) ? next.delete(k) : next.add(k);
      return next;
    });

  // Bucket documents by category, preserving library order within each.
  const byKind: Record<DocKind, LibraryDoc[]> = { release: [], script: [], qa: [], other: [] };
  for (const d of store.library) (byKind[d.kind] ?? byKind.other).push(d);
  const categories = KIND_ORDER;

  return (
    <aside className="sidebar">
      <div className="sb-org">
        <div className="sb-org-name">Atlas Systems</div>
        <div className="sb-org-sub">Investor relations</div>
      </div>

      <button className="sb-add" onClick={onUpload}>+ Add document</button>

      <div className="sb-cap">Documents</div>
      <div className="sb-cats">
        {categories.map((k) => {
          const open = openKinds.has(k);
          return (
            <div className="sb-cat" key={k}>
              <div className="sb-cat-h">
                {/* The name opens the manager scoped to this type (where past filings
                    and transcripts are uploaded); the chevron reveals the files. */}
                <button
                  className="sb-cat-main"
                  onClick={() => onManage(undefined, k)}
                  aria-label={`Manage ${KIND_META[k].label}`}
                >
                  <span className="sb-cat-ic" dangerouslySetInnerHTML={{ __html: KIND_META[k].icon }} />
                  <span className="sb-cat-l">{KIND_META[k].label}</span>
                  <span className="sb-cat-n">{byKind[k].length}</span>
                </button>
                <button
                  className="sb-cat-toggle"
                  onClick={() => toggleKind(k)}
                  aria-expanded={open}
                  aria-label={`${open ? "Collapse" : "Expand"} ${KIND_META[k].label}`}
                >
                  <span className="caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
                </button>
              </div>
              {open && (
                <div className="sb-cat-docs">
                  {byKind[k].map((d) => (
                    <button
                      key={d.id}
                      className={`sb-doc ${view === d.id ? "active" : ""}`}
                      aria-current={view === d.id ? "true" : undefined}
                      onClick={() => setView(d.id)}
                    >
                      <span className={`sb-dot ${statusOf(d, store.figures)}`} aria-hidden="true" />
                      <span className="sb-doc-name">{d.name}</span>
                      {d.versions.length > 1 && (
                        <span className="sb-doc-v" title={`${d.versions.length} versions`}>
                          v{d.versions.length}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="sb-cap">Tools</div>
      <div className="sb-cats">
        <NavItem id="consensus" name="Street consensus" icon={ICON_CONSENSUS} view={view} setView={setView} />
        <NavItem id="calendar" name="Calendar & tasks" icon={ICON_CALENDAR} view={view} setView={setView} />
      </div>
    </aside>
  );
}

function NavItem({ id, name, icon, view, setView }: {
  id: View; name: string; icon: string; view: View; setView: (v: View) => void;
}) {
  return (
    <button className={`sb-doc tool ${view === id ? "active" : ""}`} onClick={() => setView(id)}>
      <span className="sb-doc-ic" dangerouslySetInnerHTML={{ __html: icon }} />
      <span className="sb-doc-name">{name}</span>
    </button>
  );
}
