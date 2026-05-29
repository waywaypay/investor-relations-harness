import { useStore } from "../store";
import { Eye, Tick } from "../lib/icons";
import { Scrim } from "./FigureModal";
import type { Commitment } from "../types";

export function CommitmentModal({ commit, onClose }: { commit: Commitment; onClose: () => void }) {
  const store = useStore();
  const open = commit.status === "open";
  return (
    <Scrim onClose={onClose}>
      <div className="mbar">
        <span className="nbadge warn">COMMITMENT</span>
        <div>
          <div className="ttl">Open commitment from a prior call</div>
          <div className="sub">{commit.period}</div>
        </div>
        <button className="x" onClick={onClose}>×</button>
      </div>
      <div className="mbody">
        <div className="detail" style={{ width: "100%" }}>
          <div className={`statusbar ${open ? "r" : "v"}`}>
            {open ? <Eye /> : <Tick />}
            <span>{open ? "Not addressed in this draft" : "Addressed"}</span>
          </div>
          <div className="vlock">
            <div className="l">{commit.period}</div>
            <div className="v" style={{ fontSize: 15, lineHeight: 1.45 }}>{commit.text}</div>
          </div>
          <div className="reason">{commit.detail}</div>
          {open && (
            <div className="acts">
              <button className="btn go" onClick={() => { store.addressCommitment(commit.id); onClose(); }}>
                Add a line addressing this
              </button>
              <button className="btn" onClick={onClose}>Dismiss</button>
            </div>
          )}
        </div>
      </div>
    </Scrim>
  );
}
