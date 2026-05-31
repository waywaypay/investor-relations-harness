import { useMemo } from "react";
import type { ApiClaim, ApiVerdict, RuleFinding, UploadedDoc } from "../types";
import { VERDICT_STATE } from "../types";
import type { PopTarget } from "./Popover";

interface Props {
  doc: UploadedDoc;
  filter: string; // "all" | "v" | "r" | "f" | "u"
  setPop: (t: PopTarget) => void;
  onFigureClick: (claimId: string) => void;
}

const TAG: Record<string, string> = { v: "✓", r: "?", f: "!", u: "?" };

interface Segment {
  text: string;
  claim?: ApiClaim;
}

// Walk the prose, splicing in the proposed figure spans. Claims without a span
// (or overlapping a prior one) are rendered after the prose as a chip list.
function segments(doc: UploadedDoc): { inline: Segment[]; orphans: ApiClaim[] } {
  const spanned = doc.claims
    .filter((c) => c.span && c.span[1] > c.span[0])
    .sort((a, b) => a.span![0] - b.span![0]);

  const inline: Segment[] = [];
  const orphans: ApiClaim[] = doc.claims.filter((c) => !c.span);
  let cursor = 0;
  for (const c of spanned) {
    const [start, end] = c.span!;
    if (start < cursor) {
      orphans.push(c); // overlaps an already-placed figure
      continue;
    }
    if (start > cursor) inline.push({ text: doc.text.slice(cursor, start) });
    inline.push({ text: doc.text.slice(start, end), claim: c });
    cursor = end;
  }
  if (cursor < doc.text.length) inline.push({ text: doc.text.slice(cursor) });
  return { inline, orphans };
}

function FindingRow({ f }: { f: RuleFinding }) {
  return (
    <div className={`finding ${f.severity}`}>
      <span className={`sev ${f.severity}`}>{f.severity}</span>
      <div className="finding-body">
        <div className="finding-msg">{f.message}</div>
        <div className="finding-rule">
          {f.rule}
          {f.metric ? ` · ${f.metric}` : ""}
        </div>
        {f.detail && <div className="finding-detail">{f.detail}</div>}
      </div>
    </div>
  );
}

export function DocumentView({ doc, filter, setPop, onFigureClick }: Props) {
  const { inline, orphans } = useMemo(() => segments(doc), [doc]);

  const verdictFor = (claimId: string): ApiVerdict | undefined => doc.verdicts[claimId];

  const showPop = (claim: ApiClaim) => (e: React.MouseEvent) => {
    const v = verdictFor(claim.claim_id);
    if (!v) return;
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setPop({ verdict: v, claim, rect });
  };

  const figClass = (v: ApiVerdict | undefined): string => {
    const st = v ? VERDICT_STATE[v.verdict] : "u";
    const dim = filter !== "all" && st !== filter ? " dim" : "";
    return `fig ${st}${dim}`;
  };

  const blocking = doc.verdicts;
  const unresolved = Object.values(blocking).filter(
    (v) => v.verdict === "conflict" || v.verdict === "untraced"
  ).length;

  return (
    <>
      <div className="doctools">
        <div className="dt-crumb">
          {doc.entity}
          {doc.period ? ` · ${doc.period}` : ""} · <b>{doc.title}</b>
        </div>
        <div className="dt-right">
          <span className={`livechip ${doc.publishable ? "sync" : "warn"}`}>
            <span className="dotp" />
            {doc.publishable ? "Publishable" : `${unresolved} need sign-off`}
          </span>
        </div>
      </div>

      <article className="doc">
        <div className="eyebrow">{doc.kind}</div>
        <h1>{doc.title}</h1>
        <div className="dek">
          {doc.entity}
          {doc.period ? ` · ${doc.period}` : ""}
        </div>
        <hr />
        <p className="doc-prose">
          {inline.map((seg, i) =>
            seg.claim ? (
              (() => {
                const v = verdictFor(seg.claim.claim_id);
                return (
                  <span
                    key={i}
                    className={figClass(v)}
                    data-tag={TAG[v ? VERDICT_STATE[v.verdict] : "u"]}
                    onMouseEnter={showPop(seg.claim)}
                    onMouseLeave={() => setPop(null)}
                    onClick={() => onFigureClick(seg.claim!.claim_id)}
                  >
                    {seg.text}
                  </span>
                );
              })()
            ) : (
              <span key={i}>{seg.text}</span>
            )
          )}
        </p>

        {orphans.length > 0 && (
          <div className="orphans">
            <div className="orphans-cap">Other detected figures</div>
            {orphans.map((c) => {
              const v = verdictFor(c.claim_id);
              return (
                <span
                  key={c.claim_id}
                  className={`${figClass(v)} chip`}
                  data-tag={TAG[v ? VERDICT_STATE[v.verdict] : "u"]}
                  onClick={() => onFigureClick(c.claim_id)}
                >
                  {c.displayed_text}
                </span>
              );
            })}
          </div>
        )}
      </article>

      {doc.warnings.length > 0 && (
        <div className="warnings">
          {doc.warnings.map((w, i) => (
            <div key={i} className="warn-row">
              ⚠ {w}
            </div>
          ))}
        </div>
      )}

      {doc.findings.length > 0 && (
        <div className="findings-panel">
          <div className="panel-cap">Rule findings</div>
          {doc.findings.map((f, i) => (
            <FindingRow key={i} f={f} />
          ))}
        </div>
      )}
    </>
  );
}
