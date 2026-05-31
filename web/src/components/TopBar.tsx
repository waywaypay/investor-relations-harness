import { useStore } from "../store";
import type { UploadedDoc } from "../types";

interface Props {
  filter: string;
  setFilter: (f: string) => void;
  onOpenUpload: () => void;
  onOpenSources: () => void;
}

function tally(doc: UploadedDoc) {
  let v = 0,
    r = 0,
    f = 0,
    u = 0;
  for (const vd of Object.values(doc.verdicts)) {
    if (vd.verdict === "traced") v++;
    else if (vd.verdict === "needs_review") r++;
    else if (vd.verdict === "conflict") f++;
    else u++;
  }
  return { v, r, f, u, total: v + r + f + u };
}

export function TopBar({ filter, setFilter, onOpenUpload, onOpenSources }: Props) {
  const store = useStore();
  const doc = store.active;
  const t = doc ? tally(doc) : { v: 0, r: 0, f: 0, u: 0, total: 0 };
  const pct = t.total ? Math.round((t.v / t.total) * 100) : 0;
  const dashoffset = (94.2 * (1 - (t.total ? t.v / t.total : 0))).toFixed(1);

  const chips = [
    { f: "all", label: "All", sw: null },
    { f: "v", label: "Traced", sw: "sw-v" },
    { f: "r", label: "Review", sw: "sw-r" },
    { f: "f", label: "Flagged", sw: "sw-f" },
    { f: "u", label: "Untraced", sw: "sw-u" },
  ];

  return (
    <header className="topbar">
      <div className="brand">
        <span className="mark">
          Attest<span className="dot">.</span>
        </span>
        <span className="tag">disclosure you can trace</span>
      </div>

      {doc && (
        <div className="coverage">
          <div className="ring">
            <svg width="38" height="38" viewBox="0 0 38 38">
              <circle cx="19" cy="19" r="15" fill="none" stroke="#E4DFD4" strokeWidth="4" />
              <circle
                cx="19"
                cy="19"
                r="15"
                fill="none"
                stroke="#235C3F"
                strokeWidth="4"
                strokeLinecap="round"
                strokeDasharray="94.2"
                strokeDashoffset={dashoffset}
                style={{ transition: "stroke-dashoffset .6s ease" }}
              />
            </svg>
            <div className="pct">{pct}%</div>
          </div>
          <div className="cov-txt">
            <b>{t.v}</b> of <b>{t.total}</b> figures traced
            <br />
            <b>{t.r + t.f + t.u}</b> need your sign-off
          </div>
          <div className="cov-chips">
            {chips.map((c) => (
              <span
                key={c.f}
                className={`chip ${filter === c.f ? "active" : ""}`}
                onClick={() => setFilter(c.f)}
              >
                {c.sw && <span className={`sw ${c.sw}`} />}
                {c.label}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="top-actions">
        <button className="topbtn ghost" onClick={onOpenSources}>
          Sources
        </button>
        <button className="topbtn" onClick={onOpenUpload}>
          + Upload
        </button>
      </div>
    </header>
  );
}
