import { useStore } from "../store";
import type { UploadedDoc } from "../types";

function counts(doc: UploadedDoc) {
  let v = 0,
    r = 0,
    f = 0,
    u = 0,
    t = 0;
  for (const vd of Object.values(doc.verdicts)) {
    t++;
    if (vd.verdict === "traced") v++;
    else if (vd.verdict === "needs_review") r++;
    else if (vd.verdict === "conflict") f++;
    else u++;
  }
  return { v, r, f, u, t };
}

export function Sidebar({
  onOpenUpload,
  onOpenSources,
}: {
  onOpenUpload: () => void;
  onOpenSources: () => void;
}) {
  const store = useStore();
  return (
    <aside className="sidebar">
      <div className="sb-head">Disclosure workspace</div>
      <div className="sb-sub">Upload · verify · trace</div>

      <button className="upload-cta" onClick={onOpenUpload}>
        + Upload document
      </button>

      <div className="sb-cap">Your documents</div>
      <div className="doclist">
        {store.documents.length === 0 && (
          <div className="sb-empty">No documents yet. Upload a draft to begin.</div>
        )}
        {store.documents.map((d) => {
          const c = counts(d);
          const tot = c.t || 1;
          return (
            <button
              key={d.localId}
              className={`docitem ${store.activeId === d.localId ? "active" : ""}`}
              onClick={() => store.setActive(d.localId)}
            >
              <div className="di-top">
                <div className="di-main">
                  <div className="di-name">{d.title}</div>
                  <div className="di-kind">{d.kind}</div>
                </div>
                <span
                  className="di-remove"
                  title="Remove"
                  onClick={(e) => {
                    e.stopPropagation();
                    store.removeDocument(d.localId);
                  }}
                >
                  ×
                </span>
              </div>
              <div className="covbar">
                <i className="cv" style={{ width: `${(c.v / tot) * 100}%` }} />
                <i className="cr" style={{ width: `${(c.r / tot) * 100}%` }} />
                <i className="cf" style={{ width: `${((c.f + c.u) / tot) * 100}%` }} />
              </div>
              <div className="covnums">
                <span className="d">
                  <span className="sq" style={{ background: "var(--verified)" }} />
                  {c.v} traced
                </span>
                {c.r > 0 && (
                  <span className="d">
                    <span className="sq" style={{ background: "var(--review)" }} />
                    {c.r}
                  </span>
                )}
                {c.f + c.u > 0 && (
                  <span className="d">
                    <span className="sq" style={{ background: "var(--flag)" }} />
                    {c.f + c.u}
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>

      <div className="sb-cap">Sources</div>
      <button className="docitem simple" onClick={onOpenSources}>
        <div className="di-top">
          <div className="di-main">
            <div className="di-name">Filed sources</div>
            <div className="di-kind">
              {store.facts.length} fact{store.facts.length === 1 ? "" : "s"} in the store
            </div>
          </div>
        </div>
      </button>
      {store.facts.length === 0 && (
        <div className="sb-note">
          No filed sources yet. <b>Load demo numbers</b> or ingest your own XBRL so uploaded figures
          can trace.
        </div>
      )}
    </aside>
  );
}
