import { useRef, useState } from "react";
import { useStore } from "../store";
import { Scrim } from "./FigureModal";
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
}: {
  onClose: () => void;
  onUploaded: (docId: string) => void;
  /** When set, the upload is filed as a new version of this document. */
  target?: LibraryDoc | null;
}) {
  const store = useStore();
  const isVersion = target != null;
  const [kind, setKind] = useState<DocKind>(target?.kind ?? "script");
  const [title, setTitle] = useState(target?.name ?? "");
  const [ticker, setTicker] = useState("");
  const [note, setNote] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const pickFile = (f: File | null) => {
    setFile(f);
    setError(null);
    if (f && !title) setTitle(f.name.replace(/\.[^.]+$/, ""));
  };

  const canSubmit = !busy && (file != null || text.trim().length > 0);

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
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
      setError(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  };

  const nextVersionLabel = target ? `Version ${target.versions.length + 1}` : null;

  return (
    <Scrim onClose={onClose} modalClass="upmodal">
      <div className="mbar">
        <span className="badge">{isVersion ? "NEW VERSION" : "UPLOAD"}</span>
        <div>
          <div className="ttl">{isVersion ? `New version of “${target!.name}”` : "Add a document"}</div>
          <div className="sub">
            {isVersion
              ? `Files as ${nextVersionLabel} — your earlier drafts and their tie-outs are kept.`
              : "Upload a release, script, or Q&A — every figure ties out to your filed sources."}
          </div>
        </div>
        <button className="x" onClick={onClose} aria-label="Close">×</button>
      </div>

      <div className="upbody">
        {!isVersion && (
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
            placeholder="Detected from the document — set it only to override"
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            autoCapitalize="characters"
            spellCheck={false}
          />
        </label>

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
        <div className="upfield">
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
        </div>

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

        {error && <div className="uperr">{error}</div>}

        <div className="upacts">
          <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn go" onClick={submit} disabled={!canSubmit}>
            {busy ? "Analyzing…" : isVersion ? "Analyze & file version" : "Analyze & add"}
          </button>
        </div>
      </div>
    </Scrim>
  );
}
