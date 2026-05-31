import { useRef, useState } from "react";
import { useStore } from "../store";
import { Scrim } from "./FigureModal";
import { DOC_KINDS, type DocKind } from "../types";

export function UploadModal({ onClose }: { onClose: () => void }) {
  const store = useStore();
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<DocKind>("release");
  const [entity, setEntity] = useState("");
  const [period, setPeriod] = useState("");
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const canSubmit = mode === "file" ? !!file : text.trim().length > 0;

  const submit = async () => {
    if (!canSubmit || store.busy) return;
    const ok = await store.analyzeUpload({
      file: mode === "file" ? file ?? undefined : undefined,
      text: mode === "paste" ? text : undefined,
      title: title.trim() || undefined,
      kind,
      entity: entity.trim() || undefined,
      period: period.trim() || undefined,
    });
    if (ok) onClose();
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDrag(false);
    const f = e.dataTransfer.files?.[0];
    if (f) {
      setFile(f);
      setMode("file");
      if (!title) setTitle(f.name);
    }
  };

  return (
    <Scrim onClose={onClose}>
      <div className="mbar">
        <span className="badge">UPLOAD</span>
        <div>
          <div className="ttl">Add a document</div>
          <div className="sub">Upload or paste a draft — it’s analyzed against your filed sources</div>
        </div>
        <button className="x" onClick={onClose}>
          ×
        </button>
      </div>
      <div className="mbody">
        <div className="detail" style={{ width: "100%" }}>
          <div className="seg-tabs">
            <button className={mode === "file" ? "active" : ""} onClick={() => setMode("file")}>
              Upload file
            </button>
            <button className={mode === "paste" ? "active" : ""} onClick={() => setMode("paste")}>
              Paste text
            </button>
          </div>

          {mode === "file" ? (
            <div
              className={`dropzone ${drag ? "drag" : ""}`}
              onClick={() => inputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDrag(true);
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={onDrop}
            >
              <input
                ref={inputRef}
                type="file"
                accept=".txt,.md,.html,.htm,.pdf,.docx,text/plain"
                style={{ display: "none" }}
                onChange={(e) => {
                  const f = e.target.files?.[0] ?? null;
                  setFile(f);
                  if (f && !title) setTitle(f.name);
                }}
              />
              {file ? (
                <div className="dz-file">
                  <b>{file.name}</b>
                  <span>{(file.size / 1024).toFixed(1)} KB · click to replace</span>
                </div>
              ) : (
                <div className="dz-empty">
                  <b>Drop a draft here, or click to browse</b>
                  <span>.txt, .md, .html, .pdf, or .docx</span>
                </div>
              )}
            </div>
          ) : (
            <textarea
              className="paste-area"
              placeholder="Paste the press release, prepared remarks, or Q&A text…"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          )}

          <div className="form-grid">
            <label>
              Title
              <input
                type="text"
                value={title}
                placeholder="e.g. Q1 FY2026 Earnings Release"
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <label>
              Document type
              <select value={kind} onChange={(e) => setKind(e.target.value as DocKind)}>
                {DOC_KINDS.map((k) => (
                  <option key={k.value} value={k.value}>
                    {k.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Entity <span className="opt">(optional)</span>
              <input
                type="text"
                value={entity}
                placeholder="inferred from sources"
                onChange={(e) => setEntity(e.target.value)}
              />
            </label>
            <label>
              Period <span className="opt">(optional)</span>
              <input
                type="text"
                value={period}
                placeholder="e.g. FY2026-Q1"
                onChange={(e) => setPeriod(e.target.value)}
              />
            </label>
          </div>

          <div className="acts">
            <button className="btn go" disabled={!canSubmit || store.busy} onClick={submit}>
              {store.busy ? "Analyzing…" : "Analyze document"}
            </button>
            <button className="btn" onClick={onClose}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </Scrim>
  );
}
