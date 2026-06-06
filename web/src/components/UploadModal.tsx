import { useRef, useState } from "react";
import { useStore } from "../store";
import { Scrim } from "./FigureModal";
import { detectTicker } from "../lib/ticker";
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
}: {
  onClose: () => void;
  onUploaded: (docId: string) => void;
  /** When set, the upload is filed as a new version of this document. */
  target?: LibraryDoc | null;
  /** Which mode the modal opens in (the manager opens it straight to "reference"). */
  initialRole?: "draft" | "reference";
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
  // In reference mode the user picks whether to upload a file or pull from EDGAR.
  const [sourceMode, setSourceMode] = useState<"file" | "edgar">(isReference ? "edgar" : "file");
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
  const canSubmit =
    !busy &&
    (edgarMode
      ? ticker.trim().length > 0
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
              onClick={() => setSourceMode("edgar")}
            >
              Pull from EDGAR
            </button>
            <button
              type="button"
              className={`upsrc ${sourceMode === "file" ? "active" : ""}`}
              onClick={() => setSourceMode("file")}
            >
              Upload a file
            </button>
          </div>
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
        {!edgarMode && <div className="upfield">
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

        {!edgarMode && (
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
              ? edgarMode ? "Fetching…" : isReference ? "Filing…" : "Analyzing…"
              : edgarMode ? "Load from EDGAR" : isReference ? "File as reference" : isVersion ? "Analyze & file version" : "Analyze & add"}
          </button>
        </div>
      </div>
    </Scrim>
  );
}
