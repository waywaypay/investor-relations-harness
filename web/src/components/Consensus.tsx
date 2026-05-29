import { useState } from "react";
import { ANALYSTS, CMETRICS, type CUnit } from "../data/workspace";

const comma = (n: number) => Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
function fmtC(v: number, u: CUnit) {
  if (u === "eps") return `$${v.toFixed(2)}`;
  if (u === "pct") return `${v.toFixed(1)}%`;
  return `$${comma(v)}M`;
}

export function Consensus() {
  const [ingested, setIngested] = useState<number[]>([]);
  const [drag, setDrag] = useState(false);

  const ingest = () => {
    if (ingested.length < ANALYSTS.length) setIngested((p) => [...p, p.length]);
  };

  const consensusFor = (key: keyof (typeof ANALYSTS)[number]) => {
    const vals = ingested.map((i) => ANALYSTS[i][key] as number);
    if (!vals.length) return null;
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    const s = [...vals].sort((a, b) => a - b);
    return { mean, lo: s[0], hi: s[s.length - 1], n: vals.length };
  };

  return (
    <div className="cwrap">
      <div className="cv-head">
        <h1>Street consensus</h1>
        <div className="dek">
          Drop your sell-side models and Attest parses each analyst’s estimates, builds the
          consensus, and checks your numbers against it.
        </div>
      </div>
      <div className={`dropzone ${drag ? "drag" : ""}`} onClick={ingest}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); ingest(); }}>
        <div className="dz-ic">↑</div>
        <div className="dz-t">Drop sell-side models (.xlsx)</div>
        <div className="dz-s">or click to add — consensus recomputes with each model</div>
      </div>
      <div className="analysts">
        {ingested.map((i) => (
          <span className="achip" key={i}>{ANALYSTS[i].firm} <b>parsed ✓</b></span>
        ))}
      </div>
      {ingested.length < 2 ? (
        <div className="cempty">
          Add at least two analyst models to build a consensus. ({ingested.length} loaded)
        </div>
      ) : (
        <>
          <table className="ctbl">
            <tbody>
              <tr className="chead">
                <td>Metric</td><td>Consensus</td><td>Range</td><td>#</td>
                <td>Reported / guide</td><td>vs Street</td>
              </tr>
              {CMETRICS.map((m) => {
                const c = consensusFor(m.key)!;
                const delta = ((m.actual - c.mean) / c.mean) * 100;
                let cls: string, lab: string;
                if (m.isGuide) {
                  cls = delta > 0.3 ? "v" : delta < -0.3 ? "f" : "n";
                  lab = delta > 0.3 ? "above" : delta < -0.3 ? "below" : "in line";
                } else {
                  cls = delta > 0.3 ? "v" : delta < -0.3 ? "f" : "n";
                  lab = delta > 0.3 ? "beat" : delta < -0.3 ? "miss" : "in line";
                }
                const dtxt = Math.abs(delta) >= 0.1 ? ` ${delta >= 0 ? "+" : ""}${delta.toFixed(1)}%` : "";
                return (
                  <tr key={m.key}>
                    <td className="cm">{m.label}</td>
                    <td className="cnum">{fmtC(c.mean, m.unit)}</td>
                    <td className="crange">{fmtC(c.lo, m.unit)}–{fmtC(c.hi, m.unit)}</td>
                    <td className="cn">{c.n}</td>
                    <td className="cnum">{m.actualLabel}</td>
                    <td><span className={`cbadge ${cls}`}>{lab}{dtxt}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="cnote">
            Consensus rebuilds automatically as models are added. Reported figures pull from the
            same traced sources as your release — so beats and misses are computed against verified
            numbers, not re-keyed ones.
          </div>
        </>
      )}
    </div>
  );
}
