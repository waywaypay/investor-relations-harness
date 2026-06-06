import { useEffect, useRef } from "react";
import type { Figure } from "../types";

// Opens the full source document for a figure and jumps straight to the exact
// number — the as-filed value is rendered in a large, scrollable view, then we
// scroll to and pulse the highlighted figure so the eye lands on it immediately.
export function SourceDocumentModal({ fig, onClose }: { fig: Figure; onClose: () => void }) {
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const body = bodyRef.current;
    if (!body) return;
    const hl = body.querySelector("mark.hl") as HTMLElement | null;
    if (!hl) return;
    // Center the highlighted number in the scroll view…
    body.scrollTop = Math.max(0, hl.offsetTop - body.clientHeight / 2 + hl.offsetHeight / 2);
    // …then draw the eye to it with a one-shot pulse.
    hl.classList.add("pulse");
    const t = window.setTimeout(() => hl.classList.remove("pulse"), 2000);
    return () => window.clearTimeout(t);
  }, [fig]);

  // Escape closes the source view without dismissing the inspection modal beneath.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  return (
    <div
      className="scrim show srcdoc-scrim"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="modal srcdoc-modal">
        <div className="mbar">
          <span className="badge">{fig.badge}</span>
          <div>
            <div className="ttl">Source document</div>
            <div className="sub">{fig.cite}</div>
          </div>
          <button className="x" onClick={onClose} aria-label="Close source document">×</button>
        </div>
        <div className="srcdoc-body" ref={bodyRef} dangerouslySetInnerHTML={{ __html: fig.page }} />
        <div className="srcdoc-foot">
          Showing <b>{fig.lbl}</b> highlighted exactly where it appears in the filed source.
        </div>
      </div>
    </div>
  );
}
