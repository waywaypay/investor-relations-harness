import { useState } from "react";
import { useStore } from "../store";
import type { DocKind, DocVersion, Figure, LibraryDoc } from "../types";

// Plain-language category labels, matching the sidebar.
const KIND_LABEL: Record<DocKind, string> = {
  release: "Earnings releases",
  script: "Call scripts",
  qa: "Analyst Q&A",
  other: "Other documents",
};

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
    <div className={`dmrow ${expanded ? "open" : ""}`}>
      <div className="dmrow-h">
        <span className="dmrow-ic" dangerouslySetInnerHTML={{ __html: doc.icon }} />
        <input
          className="dmname"
          value={name}
          aria-label={`Rename ${doc.name}`}
          onChange={(e) => setName(e.target.value)}
          onBlur={commitName}
          onKeyDown={(e) => { if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur(); }}
        />
        {/* Kind/source at rest; on hover the row reveals its actions in the same slot. */}
        <div className="dmrow-meta">
          <span className={`dmtag ${isDemo ? "demo" : "up"}`}>{isDemo ? "Sample" : "Uploaded"}</span>
          <span className="dmrow-sub">{doc.subtitle}</span>
        </div>
        <div className="dmrow-acts">
          <button className="dmlink" onClick={onOpen}>Open</button>
          <button className="dmlink" onClick={onUploadVersion}>+ Version</button>
          <button
            className="dmlink danger"
            aria-label="Delete document"
            onClick={() => store.removeDoc(doc.id)}
          >
            Delete
          </button>
        </div>
        <button
          className="dmrow-vtoggle"
          onClick={onToggle}
          aria-expanded={expanded}
          title={`${doc.versions.length} version${doc.versions.length > 1 ? "s" : ""}`}
        >
          {doc.versions.length}
          <span className="caret" aria-hidden="true">{expanded ? "▾" : "▸"}</span>
        </button>
      </div>

      {expanded && (
        <div className="dmvers">
          {doc.versions.map((v) => (
            <VersionRow key={v.id} doc={doc} version={v} onOpen={onOpen} />
          ))}
        </div>
      )}
    </div>
  );
}

export function DocumentsManager({
  onOpen,
  onUploadNew,
  onUploadVersion,
  focusDocId,
  focusKind = null,
}: {
  onOpen: (docId: string) => void;
  onUploadNew: (role?: "draft" | "reference") => void;
  onUploadVersion: (doc: LibraryDoc) => void;
  focusDocId?: string | null;
  /** When set, the manager is scoped to one document category. */
  focusKind?: DocKind | null;
}) {
  const store = useStore();
  const [expanded, setExpanded] = useState<string | null>(focusDocId ?? null);

  // When scoped to a category, show only that kind.
  const docs = focusKind ? store.library.filter((d) => d.kind === focusKind) : store.library;

  // Group by period, preserving first-seen order.
  const periods: string[] = [];
  for (const d of docs) if (!periods.includes(d.period)) periods.push(d.period);

  const scoped = focusKind != null;

  return (
    <div className="mgr">
      <div className="mgr-head">
        <div className="ttl">{scoped ? KIND_LABEL[focusKind!] : "Manage documents"}</div>
        <div className="sub">
          {scoped
            ? "Past filings, transcripts, and versions for this company — add more below."
            : "Every draft, its versions, and its tie-out coverage in one place."}
        </div>
      </div>

      <div className="dmbody">
        <div className="dmtoolbar">
          <span className="dmcount">
            {docs.length} document{docs.length === 1 ? "" : "s"}
          </span>
          <div className="dmtoolbar-acts">
            <button className="btn" onClick={() => onUploadNew("reference")}>
              + Upload past transcript
            </button>
            <button className="btn go" onClick={() => onUploadNew("draft")}>+ New draft</button>
          </div>
        </div>

        {periods.map((period) => (
          <div key={period} className="dmgroup">
            <div className="dmgroup-h">{period}</div>
            {docs
              .filter((d) => d.period === period)
              .map((d) => (
                <DocCard
                  key={d.id}
                  doc={d}
                  expanded={expanded === d.id}
                  onToggle={() => setExpanded((cur) => (cur === d.id ? null : d.id))}
                  onOpen={() => onOpen(d.id)}
                  onUploadVersion={() => onUploadVersion(d)}
                />
              ))}
          </div>
        ))}

        {docs.length === 0 && (
          <div className="dmempty">
            {scoped
              ? "Nothing here yet — upload a past transcript or a new draft to get started."
              : "No documents yet. Upload a release, script, or Q&A to start tying it out."}
          </div>
        )}
      </div>
    </div>
  );
}
