import { useState } from "react";
import { useStore } from "../store";
import { StatusIcon } from "../lib/icons";
import { Scrim } from "./FigureModal";
import type { Narrative } from "../types";

const KIND_LABEL: Record<string, string> = {
  wording: "WORDING",
  narrative: "NARRATIVE",
  forwardlooking: "FORWARD-LOOKING",
  onmessage: "ON-MESSAGE",
};

const stClass = (st: string) => (st === "ok" ? "v" : st === "conflict" ? "f" : "r");
const stText = (st: string) =>
  st === "ok"
    ? "Consistent with approved messaging"
    : st === "conflict"
    ? "Conflicts with the data"
    : "Needs your attention";

function History({ nar }: { nar: Narrative }) {
  const h = nar.history;
  if (!h) return null;
  return (
    <>
      <div className="srctitle">How this has been framed over time</div>
      <div className="htl">
        {h.rows.map((r, i) => {
          const cur = i === h.rows.length - 1;
          return (
            <div className={`htl-row ${r.s}${cur ? " cur" : ""}`} key={i}>
              <div className="htl-dot" />
              <div className="htl-body">
                <div className="htl-period">{r.p}{cur ? " · this draft" : ""}</div>
                <div className="htl-quote">{r.q}</div>
                {r.m && <div className="htl-meta">{r.m}</div>}
              </div>
            </div>
          );
        })}
      </div>
      <div className={`htl-verdict ${h.worst}`}>{h.verdict}</div>
    </>
  );
}

export function NarrativeModal({ nar, onClose }: { nar: Narrative; onClose: () => void }) {
  const store = useStore();
  const hasHist = !!nar.history;
  const [tab, setTab] = useState<"now" | "hist">("now");

  const apply =
    nar.st === "ok" ? null : nar.suggestion ? (
      <>
        <button className="btn go" onClick={() => { store.resolveNarrative(nar.id); onClose(); }}>
          Use approved wording: “{nar.suggestion}”
        </button>
        <button className="btn">Keep as written</button>
      </>
    ) : (
      <>
        <button className="btn amber" onClick={() => { store.resolveNarrative(nar.id); onClose(); }}>
          {nar.applyLabel || "Resolve"}
        </button>
        <button className="btn">Keep as written</button>
      </>
    );

  return (
    <Scrim onClose={onClose}>
      <div className="mbar">
        <span className={`nbadge ${nar.st}`}>{KIND_LABEL[nar.kind] || "CHECK"}</span>
        <div>
          <div className="ttl">{nar.title}</div>
          <div className="sub">Checked against: {nar.against}</div>
        </div>
        <button className="x" onClick={onClose}>×</button>
      </div>
      <div className="mbody">
        <div className="source">
          <div className="srctabs">
            <button className={`stab ${tab === "now" ? "active" : ""}`} onClick={() => setTab("now")}>
              This quarter
            </button>
            {hasHist && (
              <button className={`stab ${tab === "hist" ? "active" : ""}`} onClick={() => setTab("hist")}>
                Over time
              </button>
            )}
          </div>
          {tab === "now" ? (
            <div className="reason nardetail" style={{ margin: 0 }}
              dangerouslySetInnerHTML={{ __html: nar.compare }} />
          ) : (
            <History nar={nar} />
          )}
        </div>
        <div className="detail">
          <div className={`statusbar ${stClass(nar.st)}`}>
            <StatusIcon st={stClass(nar.st) as "v" | "r" | "f"} />
            <span>{stText(nar.st)}</span>
          </div>
          <div className="vlock">
            <div className="l">In the script</div>
            <div className="v" style={{ fontSize: 18 }}>“{nar.cur}”</div>
          </div>
          <div className="reason">{nar.detail}</div>
          {apply && <div className="acts">{apply}</div>}
        </div>
      </div>
    </Scrim>
  );
}
