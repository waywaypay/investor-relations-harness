import { useRef, useState } from "react";
import { useStore } from "../store";
import { Scrim } from "./FigureModal";

export function SourcesModal({ onClose }: { onClose: () => void }) {
  const store = useStore();
  const [tab, setTab] = useState<"demo" | "xbrl" | "guidance">("demo");
  const xbrlRef = useRef<HTMLInputElement>(null);
  const [xbrlError, setXbrlError] = useState<string | null>(null);

  // guidance form
  const [gText, setGText] = useState("");
  const [gEntity, setGEntity] = useState("");
  const [gAccession, setGAccession] = useState("");
  const [gBasePeriod, setGBasePeriod] = useState("");

  const onXbrlFile = async (file: File) => {
    setXbrlError(null);
    try {
      const instance = JSON.parse(await file.text());
      if (!instance || typeof instance !== "object" || !("facts" in instance)) {
        setXbrlError("That JSON doesn’t look like an XBRL instance (missing a top-level \"facts\").");
        return;
      }
      await store.ingestXbrl(instance);
      onClose();
    } catch (err) {
      setXbrlError(`Could not parse JSON: ${(err as Error).message}`);
    }
  };

  const submitGuidance = async () => {
    if (!gText.trim() || !gEntity.trim() || !gAccession.trim() || store.busy) return;
    await store.ingestGuidance({
      text: gText,
      entity: gEntity.trim(),
      accession: gAccession.trim(),
      base_period: gBasePeriod.trim() || undefined,
    });
    onClose();
  };

  return (
    <Scrim onClose={onClose}>
      <div className="mbar">
        <span className="badge">SOURCES</span>
        <div>
          <div className="ttl">Filed sources</div>
          <div className="sub">
            {store.facts.length} filed fact{store.facts.length === 1 ? "" : "s"} in the store · uploaded drafts tie out against these
          </div>
        </div>
        <button className="x" onClick={onClose}>
          ×
        </button>
      </div>
      <div className="mbody">
        <div className="detail" style={{ width: "100%" }}>
          <div className="seg-tabs">
            <button className={tab === "demo" ? "active" : ""} onClick={() => setTab("demo")}>
              Demo numbers
            </button>
            <button className={tab === "xbrl" ? "active" : ""} onClick={() => setTab("xbrl")}>
              Upload XBRL
            </button>
            <button className={tab === "guidance" ? "active" : ""} onClick={() => setTab("guidance")}>
              Paste guidance
            </button>
          </div>

          {tab === "demo" && (
            <>
              <div className="reason">
                Load the bundled Meridian Systems Q1 FY2026 filing as the filed source. Uploaded
                drafts then trace, conflict, or flag against these numbers — ideal for trying the
                workspace without your own XBRL.
              </div>
              <div className="acts">
                <button className="btn go" disabled={store.busy} onClick={() => store.loadDemoFacts()}>
                  {store.busy ? "Loading…" : "Load demo filed numbers"}
                </button>
              </div>
            </>
          )}

          {tab === "xbrl" && (
            <>
              <div className="reason">
                Ingest your own filed XBRL facts as a JSON instance (a top-level <code>facts</code>{" "}
                array, as produced by the EDGAR/XBRL ingestion fixtures).
              </div>
              <div className="dropzone" onClick={() => xbrlRef.current?.click()}>
                <input
                  ref={xbrlRef}
                  type="file"
                  accept=".json,application/json"
                  style={{ display: "none" }}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void onXbrlFile(f);
                  }}
                />
                <div className="dz-empty">
                  <b>Click to choose an XBRL instance (.json)</b>
                  <span>tied out against on every upload</span>
                </div>
              </div>
              {xbrlError && <div className="warn-row">⚠ {xbrlError}</div>}
            </>
          )}

          {tab === "guidance" && (
            <>
              <div className="reason">
                Extract forward guidance from 8-K Exhibit 99.1 prose. Each figure is cited back to the
                sentence it came from.
              </div>
              <div className="form-grid">
                <label className="span2">
                  EX-99.1 text
                  <textarea
                    className="paste-area short"
                    value={gText}
                    placeholder="Paste the press-release prose containing the outlook…"
                    onChange={(e) => setGText(e.target.value)}
                  />
                </label>
                <label>
                  Entity
                  <input type="text" value={gEntity} placeholder="MRDN" onChange={(e) => setGEntity(e.target.value)} />
                </label>
                <label>
                  Accession
                  <input
                    type="text"
                    value={gAccession}
                    placeholder="0000000000-00-000000"
                    onChange={(e) => setGAccession(e.target.value)}
                  />
                </label>
                <label>
                  Base period <span className="opt">(optional)</span>
                  <input
                    type="text"
                    value={gBasePeriod}
                    placeholder="FY2026-Q1"
                    onChange={(e) => setGBasePeriod(e.target.value)}
                  />
                </label>
              </div>
              <div className="acts">
                <button
                  className="btn go"
                  disabled={!gText.trim() || !gEntity.trim() || !gAccession.trim() || store.busy}
                  onClick={submitGuidance}
                >
                  {store.busy ? "Ingesting…" : "Ingest guidance"}
                </button>
              </div>
            </>
          )}

          {store.facts.length > 0 && (
            <div className="facts-list">
              <div className="panel-cap">In the store</div>
              {store.facts.slice(0, 40).map((f, i) => (
                <div key={i} className="fact-row">
                  <span className="fact-metric">{f.metric}</span>
                  <span className="fact-period mono">{f.period}</span>
                  <span className="fact-entity mono">{f.entity}</span>
                  <span className="fact-value mono">{String(f.value)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Scrim>
  );
}
