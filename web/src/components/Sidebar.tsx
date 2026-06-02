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

export function Sidebar({
  view,
  setView,
  onUpload,
  onManage,
}: {
  view: View;
  setView: (v: View) => void;
  onUpload: () => void;
  onManage: () => void;
}) {
  const store = useStore();

  // Group documents by close-pack period, newest period last, preserving order.
  const periods: string[] = [];
  for (const d of store.library) if (!periods.includes(d.period)) periods.push(d.period);

  const removeDoc = (e: React.MouseEvent, doc: LibraryDoc) => {
    e.stopPropagation();
    const remaining = store.library.filter((d) => d.id !== doc.id);
    store.removeDoc(doc.id);
    if (view === doc.id) setView(remaining[0]?.id ?? "consensus");
  };

  return (
    <aside className="sidebar">
      <div className="sb-head">Workspace</div>
      <div className="sb-sub">Meridian Systems · investor relations</div>

      <div className="sb-cap-row">
        <span className="sb-cap">Documents</span>
        <span className="sb-cap-acts">
          <button className="sb-manage" onClick={onManage}>Manage</button>
          <button className="sb-upload" onClick={onUpload}>+ Upload</button>
        </span>
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
                return (
                  <button key={d.id} className={`docitem ${view === d.id ? "active" : ""}`}
                    onClick={() => setView(d.id)}>
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
                              · v{d.versions.length}
                            </span>
                          )}
                        </div>
                      </div>
                      {d.source === "upload" && (
                        <span
                          className="di-x"
                          role="button"
                          aria-label={`Remove ${d.name}`}
                          title="Remove from workspace"
                          onClick={(e) => removeDoc(e, d)}
                        >
                          ×
                        </span>
                      )}
                    </div>
                    {c.t > 0 && (
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
                    )}
                  </button>
                );
              })}
          </div>
        </div>
      ))}

      <div className="sb-cap">Workspace</div>
      <div className="doclist">
        <NavItem id="consensus" name="Street consensus" kind="Sell-side models" icon={ICON_CONSENSUS} view={view} setView={setView} />
        <NavItem id="calendar" name="Calendar & tasks" kind="Q2 FY26 runbook" icon={ICON_CALENDAR} view={view} setView={setView} />
      </div>
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
