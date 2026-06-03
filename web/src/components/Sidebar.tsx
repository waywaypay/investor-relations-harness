import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import type { Block, Figure, LibraryDoc, VerdictState } from "../types";

type View = string; // a library doc id, or "consensus" | "calendar"

function figureIds(blocks: Block[]): string[] {
  const ids: string[] = [];
  for (const b of blocks) {
    if (b.kind === "p") b.parts.forEach((p) => p.kind === "fig" && ids.push(p.id));
    if (b.kind === "qa") b.a.forEach((p) => p.kind === "fig" && ids.push(p.id));
  }
  return ids;
}

function counts(ids: string[], figs: Record<string, Figure>) {
  let v = 0, r = 0, f = 0, t = 0;
  ids.forEach((id) => {
    const fig = figs[id];
    if (!fig) return;
    t++;
    const st: VerdictState = fig.st;
    if (st === "v") v++; else if (st === "r") r++; else f++;
  });
  return { v, r, f, t };
}

const ICON_CONSENSUS =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18"/><rect x="5" y="11" width="3" height="7"/><rect x="10.5" y="7" width="3" height="11"/><rect x="16" y="13" width="3" height="5"/></svg>';
const ICON_CALENDAR =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>';

const fmtDate = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

// Anchored position for the open row-actions menu (fixed, so it escapes the
// sidebar's own scroll/overflow).
type MenuState = { id: string; top: number; right: number } | null;

export function Sidebar({
  view,
  setView,
  onUpload,
  onManage,
}: {
  view: View;
  setView: (v: View) => void;
  onUpload: () => void;
  /** Open the documents manager — focused on a document when an id is passed. */
  onManage: (focusDocId?: string) => void;
}) {
  const store = useStore();
  const [menu, setMenu] = useState<MenuState>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);

  // Group documents by close-pack period, newest period last, preserving order.
  const periods: string[] = [];
  for (const d of store.library) if (!periods.includes(d.period)) periods.push(d.period);

  const closeMenu = () => setMenu(null);

  // Dismiss the row menu on outside click or Escape.
  useEffect(() => {
    if (!menu) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest?.(".rowmenu") || t.closest?.(".di-menu-btn")) return;
      closeMenu();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeMenu(); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const toggleMenu = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (menu?.id === id) { closeMenu(); return; }
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenu({ id, top: r.bottom + 6, right: Math.max(12, window.innerWidth - r.right) });
  };

  const startRename = (doc: LibraryDoc) => {
    setRenameVal(doc.name);
    setRenaming(doc.id);
    closeMenu();
  };
  const commitRename = (doc: LibraryDoc) => {
    const clean = renameVal.trim();
    if (clean && clean !== doc.name) store.renameDoc(doc.id, clean);
    setRenaming(null);
  };

  const deleteDoc = (doc: LibraryDoc) => {
    const remaining = store.library.filter((d) => d.id !== doc.id);
    store.removeDoc(doc.id);
    if (view === doc.id) setView(remaining[0]?.id ?? "consensus");
    closeMenu();
  };

  const menuDoc = menu ? store.library.find((d) => d.id === menu.id) ?? null : null;

  return (
    <aside className="sidebar">
      <div className="sb-head">Workspace</div>
      <div className="sb-sub">Meridian Systems · investor relations</div>

      <div className="sb-cap-row">
        <span className="sb-cap">Documents</span>
        <button className="sb-upload" onClick={onUpload}>+ Upload</button>
      </div>

      {periods.map((period) => (
        <div key={period} className="docgroup">
          <div className="docgroup-h">{period}</div>
          <div className="doclist">
            {store.library
              .filter((d) => d.period === period)
              .map((d) => {
                const c = counts(figureIds(d.blocks), store.figures);
                const tot = c.t || 1;
                const cov =
                  c.t > 0 ? (
                    <>
                      <div className="covbar">
                        <i className="cv" style={{ width: `${(c.v / tot) * 100}%` }} />
                        <i className="cr" style={{ width: `${(c.r / tot) * 100}%` }} />
                        <i className="cf" style={{ width: `${(c.f / tot) * 100}%` }} />
                      </div>
                      <div className="covnums">
                        <span className="d"><span className="sq" style={{ background: "var(--verified)" }} />{c.v} traced</span>
                        {c.r > 0 && <span className="d"><span className="sq" style={{ background: "var(--review)" }} />{c.r}</span>}
                        {c.f > 0 && <span className="d"><span className="sq" style={{ background: "var(--flag)" }} />{c.f}</span>}
                      </div>
                    </>
                  ) : null;

                // Inline rename: the row becomes an editable field in place (no
                // modal), committing on blur / Enter, cancelling on Escape.
                if (renaming === d.id) {
                  return (
                    <div key={d.id} className={`docitem ${view === d.id ? "active" : ""}`}>
                      <div className="di-top">
                        <span className="di-ic" dangerouslySetInnerHTML={{ __html: d.icon }} />
                        <div className="di-main">
                          <input
                            className="di-rename"
                            autoFocus
                            value={renameVal}
                            aria-label={`Rename ${d.name}`}
                            onChange={(e) => setRenameVal(e.target.value)}
                            onBlur={() => commitRename(d)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur();
                              else if (e.key === "Escape") setRenaming(null);
                            }}
                          />
                          <div className="di-kind">{d.subtitle}</div>
                        </div>
                      </div>
                      {cov}
                    </div>
                  );
                }

                return (
                  <div key={d.id} className={`docitem ${view === d.id ? "active" : ""}`}>
                    <button
                      className="di-nav"
                      aria-current={view === d.id ? "true" : undefined}
                      onClick={() => setView(d.id)}
                    >
                      <div className="di-top">
                        <span className="di-ic" dangerouslySetInnerHTML={{ __html: d.icon }} />
                        <div className="di-main">
                          <div className="di-name">{d.name}</div>
                          <div className="di-kind">
                            {d.subtitle}
                            {d.source === "upload" && (
                              <span className="di-when"> · {fmtDate(d.addedAt)}</span>
                            )}
                            {d.versions.length > 1 && (
                              <span className="di-vers" title={`${d.versions.length} versions`}>
                                {" "}· v{d.versions.length}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                      {cov}
                    </button>
                    <button
                      className="di-menu-btn"
                      aria-label={`Actions for ${d.name}`}
                      aria-haspopup="menu"
                      aria-expanded={menu?.id === d.id}
                      title="Document actions"
                      onClick={(e) => toggleMenu(e, d.id)}
                    >
                      ⋯
                    </button>
                  </div>
                );
              })}
          </div>
        </div>
      ))}

      <button className="sb-manageall" onClick={() => onManage()}>
        Manage all documents →
      </button>

      <div className="sb-cap">Workspace</div>
      <div className="doclist">
        <NavItem id="consensus" name="Street consensus" kind="Sell-side models" icon={ICON_CONSENSUS} view={view} setView={setView} />
        <NavItem id="calendar" name="Calendar & tasks" kind="Q2 FY26 runbook" icon={ICON_CALENDAR} view={view} setView={setView} />
      </div>

      {menuDoc && menu && (
        <div
          className="rowmenu"
          ref={menuRef}
          role="menu"
          aria-label={`Actions for ${menuDoc.name}`}
          style={{ top: menu.top, right: menu.right }}
        >
          <button role="menuitem" onClick={() => startRename(menuDoc)}>Rename</button>
          <button role="menuitem" onClick={() => { onManage(menuDoc.id); closeMenu(); }}>
            Version history
          </button>
          <div className="rm-sep" />
          <button role="menuitem" className="danger" onClick={() => deleteDoc(menuDoc)}>
            {menuDoc.versions.length > 1 ? "Delete document" : "Delete"}
          </button>
        </div>
      )}
    </aside>
  );
}

function NavItem({ id, name, kind, icon, view, setView }: {
  id: View; name: string; kind: string; icon: string; view: View; setView: (v: View) => void;
}) {
  return (
    <button className={`docitem simple ${view === id ? "active" : ""}`} onClick={() => setView(id)}>
      <div className="di-top">
        <span className="di-ic" dangerouslySetInnerHTML={{ __html: icon }} />
        <div>
          <div className="di-name">{name}</div>
          <div className="di-kind">{kind}</div>
        </div>
      </div>
    </button>
  );
}
