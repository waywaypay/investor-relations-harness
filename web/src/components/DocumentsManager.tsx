import { useState } from "react";
import { useStore } from "../store";
import { Scrim } from "./FigureModal";
import type { DocVersion, Figure, LibraryDoc } from "../types";

// Coverage for one version: how many of its figures are traced / need review /
// conflict, against the live figure map.
function coverage(figureIds: string[], figs: Record<string, Figure>) {
  let v = 0, r = 0, f = 0, t = 0;
  for (const id of figureIds) {
    const fig = figs[id];
    if (!fig) continue;
    t++;
    if (fig.st === "v") v++;
    else if (fig.st === "r") r++;
    else f++;
  }
  return { v, r, f, t };
}

const fmtWhen = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
};

function VersionRow({
  doc,
  version,
  onOpen,
}: {
  doc: LibraryDoc;
  version: DocVersion;
  onOpen: () => void;
}) {
  const store = useStore();
  const active = doc.activeVersionId === version.id;
  const c = coverage(version.figureIds, store.figures);
  const onlyVersion = doc.versions.length === 1;

  return (
    <div className={`dmver ${active ? "active" : ""}`}>
      <div className="dmver-main">
        <div className="dmver-top">
          <span className="dmver-lbl">{version.label}</span>
          {active && <span className="dmver-badge">Active</span>}
          {version.origin === "demo" && <span className="dmver-src">as filed</span>}
        </div>
        <div className="dmver-meta">
          {fmtWhen(version.addedAt)}
          {c.t > 0 && (
            <>
              {" · "}
              <span className="dmc v">{c.v} traced</span>
              {c.r > 0 && <span className="dmc r"> · {c.r} review</span>}
              {c.f > 0 && <span className="dmc f"> · {c.f} conflict</span>}
              {` · ${c.t} total`}
            </>
          )}
        </div>
        {version.note && <div className="dmver-note">“{version.note}”</div>}
      </div>
      <div className="dmver-acts">
        {active ? (
          <button className="dmlink" onClick={onOpen}>Open</button>
        ) : (
          <button
            className="dmlink"
            onClick={() => store.setActiveVersion(doc.id, version.id)}
          >
            {version.addedAt < (doc.versions.find((v) => v.id === doc.activeVersionId)?.addedAt ?? "")
              ? "Restore"
              : "Make active"}
          </button>
        )}
        <button
          className="dmlink danger"
          title={onlyVersion ? "Remove the document" : "Delete this version"}
          onClick={() => store.removeVersion(doc.id, version.id)}
        >
          Delete
        </button>
      </div>
    </div>
  );
}

function DocCard({
  doc,
  expanded,
  onToggle,
  onOpen,
  onUploadVersion,
}: {
  doc: LibraryDoc;
  expanded: boolean;
  onToggle: () => void;
  onOpen: () => void;
  onUploadVersion: () => void;
}) {
  const store = useStore();
  const [name, setName] = useState(doc.name);
  const isDemo = doc.source === "demo";

  const commitName = () => {
    const clean = name.trim();
    if (clean && clean !== doc.name) store.renameDoc(doc.id, clean);
    else setName(doc.name);
  };

  return (
    <div className="dmcard">
      <div className="dmcard-h">
        <span className="dmcard-ic" dangerouslySetInnerHTML={{ __html: doc.icon }} />
        <div className="dmcard-id">
          <input
            className="dmname"
            value={name}
            aria-label={`Rename ${doc.name}`}
            onChange={(e) => setName(e.target.value)}
            onBlur={commitName}
            onKeyDown={(e) => { if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur(); }}
          />
          <div className="dmcard-sub">
            <span className={`dmtag ${isDemo ? "demo" : "up"}`}>{isDemo ? "Sample" : "Uploaded"}</span>
            {doc.subtitle}
          </div>
        </div>
        <button className="dmlink" onClick={onToggle}>
          {doc.versions.length} version{doc.versions.length > 1 ? "s" : ""} {expanded ? "▾" : "▸"}
        </button>
      </div>

      {expanded && (
        <div className="dmvers">
          {doc.versions.map((v) => (
            <VersionRow key={v.id} doc={doc} version={v} onOpen={onOpen} />
          ))}
        </div>
      )}

      <div className="dmcard-acts">
        <button className="btn" onClick={onUploadVersion}>+ Upload new version</button>
        <button className="btn" onClick={onOpen}>Open</button>
        <button
          className="btn danger"
          onClick={() => store.removeDoc(doc.id)}
        >
          Delete document
        </button>
      </div>
      {isDemo && (
        <div className="dmnote">
          Sample document. New versions you file on it stay for this session; upload your own
          document to keep its version history across reloads.
        </div>
      )}
    </div>
  );
}

export function DocumentsManager({
  onClose,
  onOpen,
  onUploadNew,
  onUploadVersion,
  focusDocId,
}: {
  onClose: () => void;
  onOpen: (docId: string) => void;
  onUploadNew: () => void;
  onUploadVersion: (doc: LibraryDoc) => void;
  focusDocId?: string | null;
}) {
  const store = useStore();
  const [expanded, setExpanded] = useState<string | null>(focusDocId ?? null);

  // Group by period, preserving first-seen order.
  const periods: string[] = [];
  for (const d of store.library) if (!periods.includes(d.period)) periods.push(d.period);

  return (
    <Scrim onClose={onClose} modalClass="docmgr">
      <div className="mbar">
        <span className="badge">DOCUMENTS</span>
        <div>
          <div className="ttl">Manage documents</div>
          <div className="sub">Every draft, its versions, and its tie-out coverage in one place.</div>
        </div>
        <button className="x" onClick={onClose} aria-label="Close">×</button>
      </div>

      <div className="dmbody">
        <div className="dmtoolbar">
          <span className="dmcount">
            {store.library.length} document{store.library.length === 1 ? "" : "s"}
          </span>
          <button className="btn go" onClick={onUploadNew}>+ Upload document</button>
        </div>

        {periods.map((period) => (
          <div key={period} className="dmgroup">
            <div className="dmgroup-h">{period}</div>
            {store.library
              .filter((d) => d.period === period)
              .map((d) => (
                <DocCard
                  key={d.id}
                  doc={d}
                  expanded={expanded === d.id}
                  onToggle={() => setExpanded((cur) => (cur === d.id ? null : d.id))}
                  onOpen={() => { onOpen(d.id); onClose(); }}
                  onUploadVersion={() => onUploadVersion(d)}
                />
              ))}
          </div>
        ))}

        {store.library.length === 0 && (
          <div className="dmempty">
            No documents yet. Upload a release, script, or Q&A to start tying it out.
          </div>
        )}
      </div>
    </Scrim>
  );
}
