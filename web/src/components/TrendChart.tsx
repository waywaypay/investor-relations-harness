import { TRENDS } from "../data/trends";
import type { CUnit } from "../data/workspace";

function fmtV(v: number, u: string): string {
  if (u === "pct") return `${Math.round(v)}%`;
  if (u === "eps") return `$${v.toFixed(2)}`;
  return v >= 1000 ? `$${(v / 1000).toFixed(2)}B` : `$${Math.round(v)}M`;
}

// Pure-SVG bar chart, ported from the prototype's buildChart().
export function TrendChart({ figureId, mode }: { figureId: string; mode: "q" | "y" }) {
  const tr = TRENDS[figureId];
  if (!tr) return <p className="trend-meta">No historical series available.</p>;

  const pts = (mode === "y" ? tr.y : tr.q).map((p) => ({ l: p.l, v: p.v }));
  const fwd = mode === "q" && tr.fwd ? tr.fwd : null;
  const u = tr.u as CUnit;

  const allv = pts.map((p) => p.v);
  if (fwd) allv.push(fwd.hi, fwd.lo);
  const maxv = Math.max(...allv);
  const minv = 0;

  const W = 520, H = 240, padT = 28, padB = 38, padL = 16, padR = 16;
  const plotH = H - padT - padB;
  const n = pts.length + (fwd ? 1 : 0);
  const slot = (W - padL - padR) / n;
  const bw = Math.min(48, slot * 0.58);

  const bars = pts.map((p, i) => {
    const cx = padL + slot * i + slot / 2;
    const h = (plotH * (p.v - minv)) / (maxv - minv || 1);
    const y = padT + plotH - h;
    const cur = i === pts.length - 1 && !fwd;
    return (
      <g key={p.l}>
        <rect className="bar" x={cx - bw / 2} y={y} width={bw} height={h} rx={3}
          fill={cur ? "#235C3F" : "#D9C9AE"} />
        <text x={cx} y={y - 7} textAnchor="middle" fontFamily="Hanken Grotesk,sans-serif"
          fontSize={11} fontWeight={700} fill={cur ? "#235C3F" : "#6E6A62"}>
          {fmtV(p.v, u)}
        </text>
        <text x={cx} y={padT + plotH + 17} textAnchor="middle" fontFamily="Hanken Grotesk,sans-serif"
          fontSize={10} fill="#9A958B">
          {p.l}
        </text>
      </g>
    );
  });

  let fwdBar = null;
  if (fwd) {
    const cx2 = padL + slot * pts.length + slot / 2;
    const yhi = padT + plotH - (plotH * (fwd.hi - minv)) / (maxv - minv);
    const ylo = padT + plotH - (plotH * (fwd.lo - minv)) / (maxv - minv);
    fwdBar = (
      <g>
        <rect x={cx2 - bw / 2} y={yhi} width={bw} height={ylo - yhi} rx={3} fill="#F8EFDC"
          stroke="#9A6A14" strokeWidth={1.4} strokeDasharray="4 3" />
        <text x={cx2} y={yhi - 7} textAnchor="middle" fontFamily="Hanken Grotesk,sans-serif"
          fontSize={10} fontWeight={700} fill="#9A6A14">guide</text>
        <text x={cx2} y={padT + plotH + 17} textAnchor="middle" fontFamily="Hanken Grotesk,sans-serif"
          fontSize={10} fill="#9A6A14">{fwd.l}</text>
      </g>
    );
  }

  const a = pts[pts.length - 2];
  const b = pts[pts.length - 1];
  let meta: JSX.Element | null = null;
  if (a && b) {
    if (u === "pct") {
      const dd = b.v - a.v;
      meta = (
        <>
          <b className={dd < 0 ? "down" : ""}>{dd >= 0 ? "+" : ""}{dd.toFixed(0)} pts</b>{" "}
          {mode === "y" ? "year over year" : "vs prior quarter"} ({b.l} vs {a.l}).
        </>
      );
    } else {
      const d = ((b.v - a.v) / a.v) * 100;
      meta = (
        <>
          <b className={d < 0 ? "down" : ""}>{d >= 0 ? "+" : ""}{d.toFixed(mode === "y" ? 0 : 1)}%</b>{" "}
          {mode === "y" ? "year over year" : "vs prior quarter"} ({b.l} vs {a.l}).
        </>
      );
    }
  }

  return (
    <>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
        <line x1={padL} y1={padT + plotH} x2={W - padR} y2={padT + plotH} stroke="#E4DFD4" strokeWidth={1} />
        {bars}
        {fwdBar}
      </svg>
      <div className="trend-meta">
        {meta}
        {fwd && (
          <> Next quarter guided to{" "}
            <b style={{ color: "#9A6A14" }}>{fmtV(fwd.lo, u)}–{fmtV(fwd.hi, u)}</b>.
          </>
        )}
      </div>
    </>
  );
}
