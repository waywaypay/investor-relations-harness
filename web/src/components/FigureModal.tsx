import { StatusIcon, Eye, Warn } from "../lib/icons";
import type { ApiClaim, ApiVerdict } from "../types";
import { VERDICT_LABEL, VERDICT_STATE } from "../types";

const STATUS_TEXT: Record<string, string> = {
  v: "Traced to a filed source",
  r: "Needs your review — bound to a non-filed source",
  f: "Conflict — the value differs from the filed source",
  u: "Untraced — no filed source could be bound",
};

function StatusGlyph({ st }: { st: string }) {
  if (st === "v" || st === "r" || st === "f") return <StatusIcon st={st as "v" | "r" | "f"} />;
  return <Eye />;
}

export function FigureModal({
  verdict,
  claim,
  onClose,
}: {
  verdict: ApiVerdict;
  claim: ApiClaim;
  onClose: () => void;
}) {
  const st = VERDICT_STATE[verdict.verdict];
  const prov = verdict.provenance;

  return (
    <Scrim onClose={onClose}>
      <div className="mbar">
        <span className={`badge ${st === "v" ? "" : st}`}>{VERDICT_LABEL[verdict.verdict]}</span>
        <div>
          <div className="ttl">{verdict.metric}</div>
          <div className="sub">
            {verdict.entity} · {verdict.period}
          </div>
        </div>
        <button className="x" onClick={onClose}>
          ×
        </button>
      </div>
      <div className="mbody">
        <div className="detail" style={{ width: "100%" }}>
          <div className={`statusbar ${st}`}>
            {st === "f" ? <Warn /> : <StatusGlyph st={st} />}
            <span>{STATUS_TEXT[st]}</span>
          </div>

          <div className="vlock">
            <div className="l">As written in the draft</div>
            <div className="v">{verdict.displayed_text}</div>
          </div>

          <div className="reason">{verdict.reason}</div>

          <div className="fields">
            <div className="field">
              <span>Filed source value</span>
              <span className={st === "v" ? "good" : st === "f" ? "bad" : ""}>
                {verdict.source_value ?? "—"}
              </span>
            </div>
            <div className="field">
              <span>Metric</span>
              <span className="mono">{verdict.metric}</span>
            </div>
            <div className="field">
              <span>Period</span>
              <span className="mono">{verdict.period}</span>
            </div>
            {prov?.source && (
              <div className="field">
                <span>Source</span>
                <span>{prov.source}</span>
              </div>
            )}
            {prov?.ref && (
              <div className="field">
                <span>Reference</span>
                <span className="mono">{prov.ref}</span>
              </div>
            )}
            {(verdict.as_of || prov?.as_of) && (
              <div className="field">
                <span>As of</span>
                <span className="mono">{verdict.as_of ?? prov?.as_of}</span>
              </div>
            )}
            <div className="field">
              <span>Detection confidence</span>
              <span className="mono">{claim.detect_confidence}</span>
            </div>
          </div>
        </div>
      </div>
    </Scrim>
  );
}

export function Scrim({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div
      className="scrim show"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal">{children}</div>
    </div>
  );
}
