import { useStore } from "../store";
import { DOCS } from "../data/documents";
import type { Block, DocKind, Figure } from "../types";

interface Props {
  activeDoc: DocKind | null; // null when on consensus/calendar (coverage hidden)
  filter: string;
  setFilter: (f: string) => void;
}

function figureIds(blocks: Block[]): string[] {
  const ids: string[] = [];
  for (const b of blocks) {
    if (b.kind === "p") b.parts.forEach((p) => p.kind === "fig" && ids.push(p.id));
    if (b.kind === "qa") b.a.forEach((p) => p.kind === "fig" && ids.push(p.id));
  }
  return ids;
}

export function TopBar({ activeDoc, filter, setFilter }: Props) {
  const store = useStore();

  let v = 0, r = 0, f = 0, total = 0;
  if (activeDoc) {
    const doc = DOCS.find((d) => d.id === activeDoc)!;
    figureIds(doc.blocks).forEach((id) => {
      const fig: Figure | undefined = store.figures[id];
      if (!fig) return;
      total++;
      if (fig.st === "v") v++; else if (fig.st === "r") r++; else f++;
    });
  }
  const pct = total ? Math.round((v / total) * 100) : 0;
  const dashoffset = (94.2 * (1 - (total ? v / total : 0))).toFixed(1);

  const chips = [
    { f: "all", label: "All", sw: null },
    { f: "v", label: "Traced", sw: "sw-v" },
    { f: "r", label: "Review", sw: "sw-r" },
    { f: "f", label: "Flagged", sw: "sw-f" },
  ];

  return (
    <header className="topbar">
      <div className="brand">
        <span className="mark">Attest<span className="dot">.</span></span>
        <span className="tag">disclosure you can trace</span>
      </div>
      {activeDoc && (
        <div className="coverage">
          <div className="ring">
            <svg width="38" height="38" viewBox="0 0 38 38">
              <circle cx="19" cy="19" r="15" fill="none" stroke="#E4DFD4" strokeWidth="4" />
              <circle cx="19" cy="19" r="15" fill="none" stroke="#235C3F" strokeWidth="4"
                strokeLinecap="round" strokeDasharray="94.2" strokeDashoffset={dashoffset}
                style={{ transition: "stroke-dashoffset .6s ease" }} />
            </svg>
            <div className="pct">{pct}%</div>
          </div>
          <div className="cov-txt">
            <b>{v}</b> of <b>{total}</b> figures traced<br />
            <b>{r + f}</b> need your sign-off
          </div>
          <div className="cov-chips">
            {chips.map((c) => (
              <span key={c.f} className={`chip ${filter === c.f ? "active" : ""}`}
                onClick={() => setFilter(c.f)}>
                {c.sw && <span className={`sw ${c.sw}`} />}{c.label}
              </span>
            ))}
          </div>
        </div>
      )}
    </header>
  );
}
