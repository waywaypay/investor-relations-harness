import { useRef, useState } from "react";
import { useStore } from "../store";
import { Scrim } from "./FigureModal";
import { detectTicker } from "../lib/ticker";
import type { HistoricalCandidate } from "../api/client";
import type { DocKind, LibraryDoc } from "../types";

const KIND_OPTIONS: { value: DocKind; label: string; hint: string }[] = [
  { value: "release", label: "Earnings release", hint: "Press release · 8-K Ex.99.1" },
  { value: "script", label: "Prepared remarks", hint: "Earnings call script" },
  { value: "qa", label: "Q&A prep", hint: "Anticipated analyst questions" },
  { value: "other", label: "Other document", hint: "Any draft to tie out" },
];

const ACCEPT = ".txt,.md,.html,.htm,.pdf,.docx,text/plain";

export function UploadModal({
  onClose,
  onUploaded,
  target = null,
  initialRole = "draft",
  initialSource,
  initialDocTypes,
}: {
  onClose: () => void;
  onUploaded: (docId: string) => void;
  /** When set, the upload is filed as a new version of this document. */
  target?: LibraryDoc | null;
  /** Which mode the modal opens in (the manager opens it straight to "reference"). */
  initialRole?: "draft" | "reference";
  /** Which source tab opens first in reference mode. */
  initialSource?: "edgar" | "historical" | "file";
  /** When set, the historical search is pre-scoped to these Exa doc_types. */
  initialDocTypes?: string[];
}) {
  const store = useStore();
  const isVersion = target != null;
  // Which action this modal performs is fixed by the entry point that opened it,
  // not chosen here: the sidebar and "New draft" open it as a draft to verify;
  // "Upload past transcript" opens it as a prior disclosure (the reference corpus
  // later drafts are checked against). A new version is always a draft.
  const isReference = !isVersion && initialRole === "reference";
  const [kind, setKind] = useState<DocKind>(target?.kind ?? "script");
  const [title, setTitle] = useState(target?.name ?? "");
  const [ticker, setTicker] = useState("");
  const [period, setPeriod] = useState("");
  const [note, setNote] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // In reference mode the user picks where the prior disclosure comes from: pull
  // structured facts from EDGAR, search the web for historical docs, or upload a file.
  const [sourceMode, setSourceMode] = useState<"edgar" | "historical" | "file">(
    isReference ? (initialSource ?? "edgar") : "file"
  );
  // Historical (web search) mode: the reviewed candidates and the user's selection.
  const [candidates, setCandidates] = useState<HistoricalCandidate[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [searching, setSearching] = useState(false);
  // The issuer ticker the typed entity resolved to ("Palo Alto Networks" ->
  // "PANW") — shown to the user and handed to the ingest so facts scope to the
  // symbol the SEC tie-out needs.
  const [resolvedEntity, setResolvedEntity] = useState("");
  // How many quarters back the search reaches — one result per period, so this is
  // literally the number of periods returned per type.
  const [quarters, setQuarters] = useState(4);
  const fileInput = useRef<HTMLInputElement>(null);

  const pickFile = (f: File | null) => {
    setFile(f);
    setError(null);
    if (!f) return;
    if (!title) setTitle(f.name.replace(/\.[^.]+$/, ""));
    // Auto-detect the issuer ticker from the document text — the standing promise
    // of the field — without clobbering a value the user already typed. Guarded
    // because not every File exposes .text() (older browsers; jsdom in tests).
    if (typeof f.text === "function") {
      f.text()
        .then((raw) => {
          const sym = detectTicker(raw);
          if (sym) setTicker((cur) => cur || sym);
        })
        .catch(() => void 0);
    }
  };

  const edgarMode = isReference && sourceMode === "edgar";
  const historicalMode = isReference && sourceMode === "historical";
  // The file/paste inputs show for drafts, new versions, and the file source mode.
  const fileMode = !edgarMode && !historicalMode;

  // Group the review results by type so releases and transcripts are never shown
  // interleaved — a combined search lists each under its own subheader; a search
  // scoped to one type (from a category's fetch button) is a single group.
  const candidateGroups = ([
    ["release", "Press releases"],
    ["transcript", "Transcripts"],
  ] as const)
    .map(([dt, label]) => ({ dt, label, items: candidates.filter((c) => c.doc_type === dt) }))
    .filter((g) => g.items.length > 0);

  const chooseMode = (m: "edgar" | "historical" | "file") => {
    setSourceMode(m);
    setError(null);
  };

  const runSearch = async () => {
    const ent = ticker.trim();
    if (!ent || searching) return;
    setSearching(true);
    setError(null);
    try {
      const result = await store.searchHistorical(ent, initialDocTypes, quarters);
      setCandidates(result.candidates);
      setResolvedEntity(result.entity || "");
      setSelected(new Set(result.candidates.map((r) => r.url))); // pre-select all for one-click load
      if (result.candidates.length === 0) setError("No historical documents found for that company.");
    } catch (e) {
      setCandidates([]);
      setSelected(new Set());
      setResolvedEntity("");
      setError(e instanceof Error ? e.message : "Search failed.");
    } finally {
      setSearching(false);
    }
  };

  const toggleSelect = (url: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });

  const canSubmit =
    !busy &&
    (edgarMode
      ? ticker.trim().length > 0
      : historicalMode
        ? selected.size > 0
        : file != null || text.trim().length > 0);

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      if (edgarMode) {
        const sym = ticker.trim().toUpperCase();
        await store.ingestEdgar(sym);
        if (period.trim()) await store.fetchPriorPeriod(sym, period.trim());
        onClose();
        return;
      }
      if (historicalMode) {
        const items = candidates
          .filter((c) => selected.has(c.url))
          .map((c) => ({
            url: c.url,
            title: c.title,
            doc_type: c.doc_type,
            ...(c.period ? { period: c.period } : {}),
          }));
        // Ingest under the resolved ticker (falling back to what was typed), so
        // the loaded figures tie out against the right issuer's filed facts.
        // Open the first loaded document so it appears in the main view rather
        // than only as a count in the sidebar.
        const firstDocId = await store.ingestHistorical((resolvedEntity || ticker).trim(), items);
        if (firstDocId) onUploaded(firstDocId);
        onClose();
        return;
      }
      if (isReference) {
        await store.ingestDisclosure({
          file: file ?? undefined,
          text: file ? undefined : text.trim() || undefined,
          entity: ticker.trim().toUpperCase() || undefined,
          label: title.trim() || file?.name || undefined,
        });
        onClose();
        return;
      }
      const docId = await store.uploadDocument(
        {
          kind,
          title: title.trim() || undefined,
          entity: ticker.trim().toUpperCase() || undefined,
          file: file ?? undefined,
          text: file ? undefined : text.trim() || undefined,
        },
        target?.id,
        note
      );
      onUploaded(docId);
      onClose();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : edgarMode
            ? "EDGAR fetch failed."
            : historicalMode
              ? "Could not load documents."
              : isReference
                ? "Could not file disclosure."
                : "Upload failed."
      );
    } finally {
      setBusy(false);
    }
  };

  const nextVersionLabel = target ? `Version ${target.versions.length + 1}` : null;

  return (
    <Scrim onClose={onClose} modalClass="upmodal">
      <div className="mbar">
        <span className="badge">{isVersion ? "NEW VERSION" : isReference ? "REFERENCE" : "UPLOAD"}</span>
        <div>
          <div className="ttl">
            {isVersion
              ? `New version of “${target!.name}”`
              : isReference
                ? "File a prior disclosure"
                : "Add a document"}
          </div>
          <div className="sub">
            {isVersion
              ? `Files as ${nextVersionLabel} — your earlier drafts and their tie-outs are kept.`
              : isReference
                ? "Its figures become the reference future drafts are checked against — a restated number that changed is flagged."
                : "Upload a release, script, or Q&A — every figure ties out to your filed sources."}
          </div>
        </div>
        <button className="x" onClick={onClose} aria-label="Close">×</button>
      </div>

      <div className="upbody">
        {isReference && (
          <div className="upsrctoggle">
            <button
              type="button"
              className={`upsrc ${sourceMode === "edgar" ? "active" : ""}`}
              onClick={() => chooseMode("edgar")}
            >
              Pull from EDGAR
            </button>
            <button
              type="button"
              className={`upsrc ${sourceMode === "historical" ? "active" : ""}`}
              onClick={() => chooseMode("historical")}
            >
              Fetch historical
            </button>
            <button
              type="button"
              className={`upsrc ${sourceMode === "file" ? "active" : ""}`}
              onClick={() => chooseMode("file")}
            >
              Upload a file
            </button>
          </div>
        )}

        {historicalMode && (
          <>
            <label className="upfield">
              <span className="upcap">Company name or ticker</span>
              <div className="uphsearch">
                <input
                  className="upinput"
                  type="text"
                  placeholder="e.g. PANW or Palo Alto Networks"
                  value={ticker}
                  onChange={(e) => setTicker(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.preventDefault(); runSearch(); }
                  }}
                  spellCheck={false}
                  autoFocus
                />
                <select
                  className="upquarters"
                  aria-label="How many quarters back"
                  value={quarters}
                  onChange={(e) => setQuarters(Number(e.target.value))}
                >
                  {[2, 4, 6, 8, 12].map((n) => (
                    <option key={n} value={n}>Last {n} quarters</option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn"
                  onClick={runSearch}
                  disabled={searching || !ticker.trim()}
                >
                  {searching ? "Searching…" : "Search"}
                </button>
              </div>
              <span className="upcap upopt" style={{ textTransform: "none", letterSpacing: 0 }}>
                {initialDocTypes?.length === 1 && initialDocTypes[0] === "release"
                  ? "Finds historical earnings releases on the web — review and load the ones you want."
                  : initialDocTypes?.length === 1 && initialDocTypes[0] === "transcript"
                  ? "Finds historical call transcripts on the web — review and load the ones you want."
                  : "Finds historical earnings releases & call transcripts on the web — review and load the ones you want."}
                {" "}Loaded as reference (web source, not a filing).
              </span>
            </label>

            {candidates.length > 0 && (
              <div className="uphlist">
                <div className="uphlist-h">
                  <span>
                    {selected.size} of {candidates.length} selected
                    {resolvedEntity && resolvedEntity.toUpperCase() !== ticker.trim().toUpperCase()
                      ? ` · issuer ${resolvedEntity}`
                      : ""}
                  </span>
                  <button
                    type="button"
                    className="uphall"
                    onClick={() =>
                      setSelected(
                        selected.size === candidates.length
                          ? new Set()
                          : new Set(candidates.map((c) => c.url))
                      )
                    }
                  >
                    {selected.size === candidates.length ? "Clear all" : "Select all"}
                  </button>
                </div>
                {candidateGroups.map((g) => (
                  <div key={g.dt} className="uphgroup">
                    {candidateGroups.length > 1 && (
                      <div className="uphgroup-h">
                        {g.label}
                        <span className="uphgroup-n">{g.items.length}</span>
                      </div>
                    )}
                    {g.items.map((c) => (
                      <label key={c.url} className={`uphrow ${selected.has(c.url) ? "on" : ""}`}>
                        <input
                          type="checkbox"
                          checked={selected.has(c.url)}
                          onChange={() => toggleSelect(c.url)}
                        />
                        <span className="uphrow-main">
                          <span className="uphrow-t">{c.title}</span>
                          <span className="uphrow-m">
                            <span className={`uphtag ${c.doc_type}`}>
                              {c.doc_type === "transcript" ? "Transcript" : "Release"}
                            </span>
                            <span className="uphsrc">{c.source}</span>
                          </span>
                          {c.snippet && <span className="uphrow-s">{c.snippet}</span>}
                        </span>
                      </label>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {edgarMode && (
          <>
            <label className="upfield">
              <span className="upcap">Ticker symbol</span>
              <input
                className="upinput"
                type="text"
                placeholder="e.g. PANW — pulls XBRL facts + prior-period 8-K"
                value={ticker}
                onChange={(e) => setTicker(e.target.value)}
                autoCapitalize="characters"
                spellCheck={false}
                autoFocus
              />
            </label>
            <label className="upfield">
              <span className="upcap">
                Current period <span className="upopt">(optional — fetches the prior quarter's 8-K)</span>
              </span>
              <input
                className="upinput"
                type="text"
                placeholder="e.g. FY2026-Q2 — omit to skip press-release fetch"
                value={period}
                onChange={(e) => setPeriod(e.target.value)}
                spellCheck={false}
              />
            </label>
          </>
        )}

        {!isVersion && !isReference && (
          <label className="upfield">
            <span className="upcap">Document type</span>
            <div className="upkinds">
              {KIND_OPTIONS.map((o) => (
                <button
                  key={o.value}
                  type="button"
                  className={`upkind ${kind === o.value ? "active" : ""}`}
                  onClick={() => setKind(o.value)}
                >
                  <span className="upk-l">{o.label}</span>
                  <span className="upk-h">{o.hint}</span>
                </button>
              ))}
            </div>
          </label>
        )}

        {isVersion && (
          <label className="upfield">
            <span className="upcap">What changed <span className="upopt">(optional)</span></span>
            <input
              className="upinput"
              type="text"
              placeholder="e.g. Updated guidance range after the board review"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
        )}

        {/* A <div>, not a <label>: the dropzone already opens the input via its
            onClick, and a wrapping <label> would *also* forward the click to the
            contained <input>, firing the file picker twice (pick a file, hit
            Open, and the picker reappears). */}
        {fileMode && <div className="upfield">
          <span className="upcap">Upload a file</span>
          <div
            className={`dropzone ${drag ? "drag" : ""}`}
            onClick={() => fileInput.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              pickFile(e.dataTransfer.files?.[0] ?? null);
            }}
          >
            <div className="dz-ic">⤓</div>
            <div className="dz-t">{file ? file.name : "Drop your file here, or click to browse"}</div>
            <div className="dz-s">{file ? "Click to choose a different file" : ".txt, .md, .html, .pdf, .docx"}</div>
          </div>
          <input
            ref={fileInput}
            type="file"
            accept={ACCEPT}
            style={{ display: "none" }}
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
        </div>}

        {fileMode && (
          <>
            {/* Revealed only once a file is chosen: the title seeds from the filename
                and the ticker is auto-detected from the document text (override either). */}
            {file && (
              <>
                <label className="upfield">
                  <span className="upcap">Title <span className="upopt">(optional)</span></span>
                  <input
                    className="upinput"
                    type="text"
                    placeholder="e.g. Q2 FY2026 prepared remarks — draft 3"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                  />
                </label>

                <label className="upfield">
                  <span className="upcap">Issuer ticker <span className="upopt">(auto-detected from the text)</span></span>
                  <input
                    className="upinput"
                    type="text"
                    placeholder="e.g. PANW — ties the draft out to filed sources"
                    value={ticker}
                    onChange={(e) => setTicker(e.target.value)}
                    autoCapitalize="characters"
                    spellCheck={false}
                  />
                </label>
              </>
            )}

            <div className="upor">or paste the text</div>

            <label className="upfield">
              <textarea
                className="uptext"
                placeholder="Paste your draft release, script, or Q&A here…"
                value={text}
                onChange={(e) => { setText(e.target.value); if (e.target.value) setFile(null); }}
                disabled={file != null}
              />
            </label>
          </>
        )}

        {error && <div className="uperr">{error}</div>}

        <div className="upacts">
          <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn go" onClick={submit} disabled={!canSubmit}>
            {busy
              ? edgarMode ? "Fetching…" : historicalMode ? "Loading…" : isReference ? "Filing…" : "Analyzing…"
              : edgarMode
                ? "Load from EDGAR"
                : historicalMode
                  ? selected.size ? `Load ${selected.size} selected` : "Load selected"
                  : isReference ? "File as reference" : isVersion ? "Analyze & file version" : "Analyze & add"}
          </button>
        </div>
      </div>
    </Scrim>
  );
}
