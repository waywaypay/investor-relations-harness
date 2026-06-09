import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Figure, Narrative } from "../types";

export type PopTarget =
  | { type: "fig"; fig: Figure; rect: DOMRect }
  | { type: "nar"; nar: Narrative; rect: DOMRect }
  | null;

const KIND_LABEL: Record<string, string> = {
  wording: "WORDING",
  narrative: "NARRATIVE",
  forwardlooking: "FORWARD-LOOKING",
  onmessage: "ON-MESSAGE",
};
const narStClass = (st: string) => (st === "ok" ? "v" : st === "conflict" ? "f" : "r");
const narStText = (st: string) =>
  st === "ok" ? "Consistent with approved messaging"
    : st === "conflict" ? "Conflicts with the data" : "Needs your attention";

export function Popover({
  target, onEnter, onLeave,
}: { target: PopTarget; onEnter: () => void; onLeave: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const docRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    if (!target || !ref.current) {
      setPos(null);
      return;
    }
    const pw = 332;
    const ph = ref.current.offsetHeight || 220;
    const r = target.rect;
    const left = Math.max(12, Math.min(r.left + r.width / 2 - pw / 2, window.innerWidth - pw - 12));
    let top = r.top - ph - 10;
    if (top < 68) top = r.bottom + 10;
    setPos({ left, top });
  }, [target]);

  // Center the cropped source view on the highlighted mark, like the prototype.
  useEffect(() => {
    if (target?.type === "fig" && docRef.current) {
      const hl = docRef.current.querySelector("mark.hl") as HTMLElement | null;
      if (hl) docRef.current.scrollTop = Math.max(0, hl.offsetTop - docRef.current.clientHeight / 2 + hl.offsetHeight / 2);
    }
  }, [target]);

  if (!target) return <div className="pop" ref={ref} />;

  const style = { width: 332, left: pos?.left ?? -9999, top: pos?.top ?? -9999 };

  if (target.type === "fig") {
    const d = target.fig;
    const bc = d.st === "f" ? "f" : d.st === "r" ? "r" : "";
    // Each state gets its own honest word — an untraced figure must never be
    // labeled "Conflict" (a different, scarier claim than "no source bound").
    const stTxt =
      d.st === "v" ? "Traced" : d.st === "r" ? "Manual check" : d.st === "f" ? "Conflict" : "Untraced";
    return (
      <div className={`pop show`} ref={ref} style={style} onMouseEnter={onEnter} onMouseLeave={onLeave}>
        <div className="ph">
          <span className={`badge ${bc}`}>{d.badge}</span>
          <span className={`st ${d.st}`}>{stTxt}</span>
        </div>
        <div className="pop-doc" ref={docRef} dangerouslySetInnerHTML={{ __html: d.page }} />
        <div className="pf"><span>{d.cite}</span><span className="go">Click to open ↗</span></div>
      </div>
    );
  }

  const n = target.nar;
  return (
    <div className={`pop show`} ref={ref} style={style} onMouseEnter={onEnter} onMouseLeave={onLeave}>
      <div className="ph">
        <span className={`nbadge ${n.st}`}>{KIND_LABEL[n.kind] || "CHECK"}</span>
        <span className={`st ${narStClass(n.st)}`}>{narStText(n.st)}</span>
      </div>
      <div className="nar-body" dangerouslySetInnerHTML={{ __html: n.compare }} />
      <div className="pf"><span>vs {n.against}</span><span className="go">Click to review ↗</span></div>
    </div>
  );
}
