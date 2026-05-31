import { useLayoutEffect, useRef, useState } from "react";
import type { ApiClaim, ApiVerdict } from "../types";
import { VERDICT_LABEL, VERDICT_STATE } from "../types";

export type PopTarget = { verdict: ApiVerdict; claim: ApiClaim; rect: DOMRect } | null;

export function Popover({
  target,
  onEnter,
  onLeave,
}: {
  target: PopTarget;
  onEnter: () => void;
  onLeave: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    if (!target || !ref.current) {
      setPos(null);
      return;
    }
    const pw = 320;
    const ph = ref.current.offsetHeight || 180;
    const r = target.rect;
    const left = Math.max(12, Math.min(r.left + r.width / 2 - pw / 2, window.innerWidth - pw - 12));
    let top = r.top - ph - 10;
    if (top < 68) top = r.bottom + 10;
    setPos({ left, top });
  }, [target]);

  if (!target) return <div className="pop" ref={ref} />;

  const v = target.verdict;
  const st = VERDICT_STATE[v.verdict];
  const style = { width: 320, left: pos?.left ?? -9999, top: pos?.top ?? -9999 };

  return (
    <div className="pop show" ref={ref} style={style} onMouseEnter={onEnter} onMouseLeave={onLeave}>
      <div className="ph">
        <span className={`badge ${st === "v" ? "" : st}`}>{v.metric}</span>
        <span className={`st ${st}`}>{VERDICT_LABEL[v.verdict]}</span>
      </div>
      <div className="pop-reason">{v.reason}</div>
      <div className="pop-fields">
        <div className="pf-row">
          <span>As written</span>
          <span className="mono">{v.displayed_text}</span>
        </div>
        <div className="pf-row">
          <span>Filed source</span>
          <span className="mono">{v.source_value ?? "—"}</span>
        </div>
        <div className="pf-row">
          <span>Period</span>
          <span className="mono">{v.period}</span>
        </div>
      </div>
      <div className="pf">
        <span>{v.provenance?.source ?? "no source bound"}</span>
        <span className="go">Click to open ↗</span>
      </div>
    </div>
  );
}
