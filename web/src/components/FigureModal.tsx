import { useState } from "react";
import { useStore } from "../store";
import { StatusIcon, Eye, Warn } from "../lib/icons";
import { TrendChart } from "./TrendChart";
import { SourceDocumentModal } from "./SourceDocumentModal";
import type { Figure } from "../types";

const STATUS_TEXT: Record<string, string> = {
  v: "Traced to a filed source",
  r: "Needs a manual check",
  f: "Flagged — conflict found",
};

const BIND_OPTIONS = [
  { name: "ATLS 10-Q", ctx: "Statements of Operations" },
  { name: "ATLS 8-K Ex.99.1", ctx: "Press-release reconciliation" },
  { name: "Prior-year 10-Q", ctx: "Comparative period" },
];

function badgeClass(st: string) {
  return st === "f" ? "f" : st === "r" ? "r" : "";
}

export function FigureModal({ fig, onClose }: { fig: Figure; onClose: () => void }) {
  const store = useStore();
  const [tab, setTab] = useState<"source" | "trend">("source");
  const [trendMode, setTrendMode] = useState<"q" | "y">("q");
  // When set, the full source document opens scrolled to this figure's number.
  const [srcDocOpen, setSrcDocOpen] = useState(false);

  // Untraced figure. A figure from an uploaded/fetched document (namespaced
  // `version::claim` id) gets the engine's honest reason and the claim details —
  // never the demo close pack's bind options, which belong to a different issuer.
  // A figure typed into the demo draft keeps the bind flow.
  if (fig.st === "u") {
    const isUploadFigure = fig.id.includes("::");
    return (
      <Scrim onClose={onClose}>
        <div className="mbar">
          <span className="badge" style={{ background: "#516170" }}>UNTRACED</span>
          <div>
            <div className="ttl">{fig.cur}</div>
            <div className="sub">{isUploadFigure ? fig.lbl || "Not yet traced" : "Detected in the draft · not yet traced"}</div>
          </div>
          <button className="x" onClick={onClose}>×</button>
        </div>
        <div className="mbody">
          <div className="detail" style={{ width: "100%" }}>
            <div className="statusbar" style={{ background: "#EAEEF2", color: "#516170" }}>
              <Eye />
              <span>
                {isUploadFigure
                  ? "Untraced — no filed source matched this figure"
                  : "Untraced figure — bind it to a source"}
              </span>
            </div>
            {isUploadFigure ? (
              <>
                {fig.reason && (
                  <div className="reason" dangerouslySetInnerHTML={{ __html: fig.reason }} />
                )}
                {fig.fields?.length > 0 && (
                  <div className="fields">
                    {fig.fields.map((f, i) => (
                      <div className="field" key={i}>
                        <span>{f.label}</span>
                        <span className={f.tone || ""}>{f.value}</span>
                      </div>
                    ))}
                  </div>
                )}
                <div className="acts">
                  <button className="btn" onClick={onClose}>Close</button>
                </div>
              </>
            ) : (
              <>
                <div className="reason">
                  This number was typed into the draft but isn’t linked to a filing yet. Bind it to a
                  source so it traces like everything else — or remove it.
                </div>
                <div className="bindlist">
                  {BIND_OPTIONS.map((o) => (
                    <div key={o.name} className="bindopt"
                      onClick={() => { store.bindFigure(fig.id, o.name); onClose(); }}>
                      <div>
                        <div className="bn">{o.name}</div>
                        <div className="bc">{o.ctx}</div>
                      </div>
                      <span className="badge">Bind</span>
                    </div>
                  ))}
                </div>
                <div className="acts">
                  <button className="btn" onClick={() => { store.removeFigure(fig.id); onClose(); }}>
                    Remove from draft
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </Scrim>
    );
  }

  // Edited away from filed value -> restore flow.
  if (fig.editedFrom) {
    return (
      <Scrim onClose={onClose}>
        <div className="mbar">
          <span className="badge f">EDITED</span>
          <div>
            <div className="ttl">{fig.lbl}</div>
            <div className="sub">Differs from the filed source</div>
          </div>
          <button className="x" onClick={onClose}>×</button>
        </div>
        <div className="mbody">
          <div className="detail" style={{ width: "100%" }}>
            <div className="statusbar f"><Warn /><span>Edited away from the filed value</span></div>
            <div className="vlock">
              <div className="l">{fig.lbl}</div>
              <div className="v">{fig.cur}</div>
            </div>
            <div className="reason">
              You changed this figure to <b>{fig.cur}</b>, but the filed source reads{" "}
              <b>{fig.editedFrom}</b>. Attest will not mark an edited-away figure as traced.
            </div>
            <div className="fields">
              <div className="field"><span>As filed</span><span className="good">{fig.editedFrom}</span></div>
              <div className="field"><span>In draft (edited)</span><span className="bad">{fig.cur}</span></div>
            </div>
            <div className="acts">
              <button className="btn go" onClick={() => { store.restoreFigure(fig.id); onClose(); }}>
                Restore filed value ({fig.editedFrom})
              </button>
              <button className="btn">Keep &amp; add justification</button>
            </div>
          </div>
        </div>
      </Scrim>
    );
  }

  const conflictLabel =
    fig.id === "cloudgrowth"
      ? "Apply corrected 29%"
      : fig.filed
        ? `Apply source value (${fig.filed})`
        : "Apply source value";

  const acts =
    fig.st === "f" ? (
      <>
        <button className="btn go" onClick={() => { store.resolveFigure(fig.id); onClose(); }}>
          {conflictLabel}
        </button>
        <button className="btn">Keep &amp; add justification</button>
      </>
    ) : fig.st === "r" ? (
      <>
        <button className="btn amber" onClick={() => { store.resolveFigure(fig.id); onClose(); }}>
          Approve &amp; attach safe harbor
        </button>
        <button className="btn">Request edit from FP&amp;A</button>
      </>
    ) : (
      <>
        <button className="btn">Re-verify against latest filing</button>
        <button className="btn">Copy citation</button>
      </>
    );

  return (
    <>
    <Scrim onClose={onClose}>
      <div className="mbar">
        <span className={`badge ${badgeClass(fig.st)}`}>{fig.badge}</span>
        <div>
          <div className="ttl">{fig.lbl}</div>
          <div className="sub">{fig.cite}</div>
        </div>
        <button className="x" onClick={onClose}>×</button>
      </div>
      <div className="mbody">
        <div className="source">
          <div className="srctabs">
            <button className={`stab ${tab === "source" ? "active" : ""}`} onClick={() => setTab("source")}>
              Source as filed
            </button>
            <button className={`stab ${tab === "trend" ? "active" : ""}`} onClick={() => setTab("trend")}>
              Trend over time
            </button>
          </div>
          {tab === "source" ? (
            <>
              <div dangerouslySetInnerHTML={{ __html: fig.page }} />
              {fig.page && (
                <button className="viewsource" onClick={() => setSrcDocOpen(true)}>
                  🔍 Click to source — open the document at this exact number ↗
                </button>
              )}
            </>
          ) : (
            <div>
              <div className="trendtoggle">
                <button className={trendMode === "q" ? "active" : ""} onClick={() => setTrendMode("q")}>
                  Quarterly
                </button>
                <button className={trendMode === "y" ? "active" : ""} onClick={() => setTrendMode("y")}>
                  Year over year
                </button>
              </div>
              <TrendChart figureId={fig.id} mode={trendMode} />
            </div>
          )}
        </div>
        <div className="detail">
          <div className={`statusbar ${fig.st}`}>
            <StatusIcon st={fig.st as "v" | "r" | "f"} />
            <span>{STATUS_TEXT[fig.st]}</span>
          </div>
          <div className="vlock">
            <div className="l">{fig.lbl}</div>
            <div className="v">{fig.v}</div>
          </div>
          <div className="reason" dangerouslySetInnerHTML={{ __html: fig.reason }} />
          <div className="fields">
            {fig.fields.map((f, i) => (
              <div className="field" key={i}>
                <span>{f.label}</span>
                <span className={f.tone || ""}>{f.value}</span>
              </div>
            ))}
          </div>
          <div className="acts">{acts}</div>
        </div>
      </div>
    </Scrim>
    {srcDocOpen && (
      <SourceDocumentModal fig={fig} onClose={() => setSrcDocOpen(false)} />
    )}
    </>
  );
}

export function Scrim({
  children,
  onClose,
  modalClass,
}: {
  children: React.ReactNode;
  onClose: () => void;
  modalClass?: string;
}) {
  return (
    <div className="scrim show" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal${modalClass ? ` ${modalClass}` : ""}`}>{children}</div>
    </div>
  );
}
