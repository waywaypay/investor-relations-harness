import { useMemo, useRef, useState } from "react";
import { useStore } from "../store";
import { DOCS } from "../data/documents";
import type { Block, DocKind, Figure, Inline, Narrative } from "../types";
import { detectNewFigures } from "../lib/verify";
import type { PopTarget } from "./Popover";

interface Props {
  docId: DocKind;
  filter: string; // "all" | "v" | "r" | "f"
  setPop: (t: PopTarget) => void;
  onFigureClick: (id: string) => void;
  onNarrativeClick: (id: string) => void;
  onCommitmentClick: () => void;
}

function FigToken({
  fig, dim, editing, onHover, onLeave, onClick,
}: {
  fig: Figure; dim: boolean; editing: boolean;
  onHover: (e: React.MouseEvent) => void; onLeave: () => void; onClick: () => void;
}) {
  return (
    <span
      className={`fig ${fig.st}${dim ? " dim" : ""}`}
      data-tag={fig.tag}
      onMouseEnter={editing ? undefined : onHover}
      onMouseLeave={editing ? undefined : onLeave}
      onClick={onClick}
    >
      {fig.cur}
    </span>
  );
}

function NarToken({
  nar, editing, onHover, onLeave, onClick,
}: {
  nar: Narrative; editing: boolean;
  onHover: (e: React.MouseEvent) => void; onLeave: () => void; onClick: () => void;
}) {
  return (
    <span
      className={`nar ${nar.st}`}
      data-tag={nar.tag}
      onMouseEnter={editing ? undefined : onHover}
      onMouseLeave={editing ? undefined : onLeave}
      onClick={editing ? undefined : onClick}
    >
      {nar.cur}
    </span>
  );
}

export function DocumentView(props: Props) {
  const { docId, filter, setPop, onFigureClick, onNarrativeClick, onCommitmentClick } = props;
  const store = useStore();
  const doc = useMemo(() => DOCS.find((d) => d.id === docId)!, [docId]);
  const [editing, setEditing] = useState(false);
  const hideTimer = useRef<number | undefined>(undefined);

  const showFig = (id: string) => (e: React.MouseEvent) => {
    window.clearTimeout(hideTimer.current);
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const fig = store.figures[id];
    if (fig) setPop({ type: "fig", fig, rect });
  };
  const showNar = (id: string) => (e: React.MouseEvent) => {
    window.clearTimeout(hideTimer.current);
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const nar = store.narratives[id];
    if (nar) setPop({ type: "nar", nar, rect });
  };
  const hide = () => {
    hideTimer.current = window.setTimeout(() => setPop(null), 120);
  };

  // Inline edit: on blur, commit the new text and re-verify.
  const commitEdit = (id: string, el: HTMLElement) => {
    store.editFigure(id, el.textContent || "");
  };

  const renderInline = (part: Inline, key: number) => {
    if (part.kind === "text")
      return <span key={key} dangerouslySetInnerHTML={{ __html: part.html }} />;
    if (part.kind === "fig") {
      const fig = store.figures[part.id];
      if (!fig) return null;
      const dim = filter !== "all" && fig.st !== filter;
      if (editing) {
        return (
          <span
            key={key}
            className={`fig ${fig.st} editing`}
            data-tag={fig.tag}
            contentEditable
            suppressContentEditableWarning
            onBlur={(e) => commitEdit(part.id, e.currentTarget)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); (e.currentTarget as HTMLElement).blur(); }
            }}
          >
            {fig.cur}
          </span>
        );
      }
      return (
        <FigToken key={key} fig={fig} dim={dim} editing={editing}
          onHover={showFig(part.id)} onLeave={hide} onClick={() => onFigureClick(part.id)} />
      );
    }
    const nar = store.narratives[part.id];
    if (!nar) return null;
    return (
      <NarToken key={key} nar={nar} editing={editing}
        onHover={showNar(part.id)} onLeave={hide} onClick={() => onNarrativeClick(part.id)} />
    );
  };

  const renderBlock = (block: Block, i: number) => {
    switch (block.kind) {
      case "eyebrow": return <div className="eyebrow" key={i}>{block.text}</div>;
      case "h1": return <h1 key={i}>{block.text}</h1>;
      case "dek": return <div className="dek" key={i}>{block.text}</div>;
      case "hr": return <hr key={i} />;
      case "h2": return <h2 key={i}>{block.text}</h2>;
      case "speaker": return <div className="speaker" key={i}>{block.text}</div>;
      case "narbar": return <NarrativeBar key={i} onCommitmentClick={onCommitmentClick} />;
      case "p":
        return <p key={i}>{block.parts.map((p, j) => renderInline(p, j))}</p>;
      case "qa":
        return (
          <div className="qa-item" key={i}>
            <div className="qa-q"><span className="tagq">{block.tag}</span>{block.q}</div>
            <div className="qa-a">{block.a.map((p, j) => renderInline(p, j))}</div>
          </div>
        );
    }
  };

  // Live status chip.
  const figsInDoc = collectFigureIds(doc.blocks);
  const offSource = figsInDoc.filter((id) => {
    const f = store.figures[id];
    return f && (f.editedFrom || f.st === "u");
  }).length;
  let chipCls = "livechip sync", chipTxt = "In sync with source";
  if (editing) { chipCls = "livechip editing"; chipTxt = "Live verification on"; }
  else if (offSource) { chipCls = "livechip warn"; chipTxt = `${offSource} off source`; }

  const toggleEdit = () => {
    if (editing) {
      // On exit, detect any new figures typed into plain text blocks.
      const found = newFigureCount(doc.blocks, store.figures);
      if (found) store.showToast(`${found} new figure${found > 1 ? "s" : ""} detected in your edits — click each to bind it to a source.`);
    }
    setEditing((e) => !e);
  };

  return (
    <>
      <div className="doctools">
        <div className="dt-crumb">Q1 FY2026 close pack · <b>{doc.name}</b></div>
        <div className="dt-right">
          <span className={chipCls}><span className="dotp" />{chipTxt}</span>
          <button className={`editbtn ${editing ? "on" : ""}`} onClick={toggleEdit}>
            {editing ? "Done editing" : "Edit draft"}
          </button>
        </div>
      </div>
      {editing && (
        <div className="edithint">
          ✎ Editing — click any <b>highlighted figure</b> to change its value, then click away to re-verify against the filed source.
        </div>
      )}
      <article className={`doc ${editing ? "editing" : ""}`}>
        {doc.blocks.map((b, i) => renderBlock(b, i))}
      </article>
    </>
  );
}

function collectFigureIds(blocks: Block[]): string[] {
  const ids: string[] = [];
  for (const b of blocks) {
    if (b.kind === "p") b.parts.forEach((p) => p.kind === "fig" && ids.push(p.id));
    if (b.kind === "qa") b.a.forEach((p) => p.kind === "fig" && ids.push(p.id));
  }
  return ids;
}

// In the standalone mock we can't read contentEditable DOM back into the block
// model, so "new figure" detection is a no-op placeholder kept for parity with
// the prototype's toast. Returns 0 (no destructive surprises).
function newFigureCount(_blocks: Block[], _figs: Record<string, Figure>): number {
  void detectNewFigures;
  return 0;
}

function NarrativeBar({ onCommitmentClick }: { onCommitmentClick: () => void }) {
  const store = useStore();
  const narIds = ["strong", "cloudword", "accel", "fls"];
  let conflict = 0, warn = 0, ok = 0;
  narIds.forEach((id) => {
    const n = store.narratives[id];
    if (!n) return;
    if (n.st === "conflict") conflict++;
    else if (n.st === "warn") warn++;
    else ok++;
  });
  const open = store.commitments.filter((c) => c.status === "open").length;
  return (
    <div className="narbar">
      <span className="nb-h">Narrative &amp; language</span>
      {conflict > 0 && <><span className="nb-sep">·</span><span className="nb-conflict">{conflict} conflict{conflict > 1 ? "s" : ""}</span></>}
      {warn > 0 && <><span className="nb-sep">·</span><span className="nb-warn">{warn} to review</span></>}
      <span className="nb-sep">·</span><span className="nb-ok">{ok} on-message</span>
      {open > 0 && (
        <><span className="nb-sep">·</span>
          <span className="nb-commit" onClick={onCommitmentClick}>
            {open} open commitment{open > 1 ? "s" : ""}
          </span>
        </>
      )}
    </div>
  );
}
