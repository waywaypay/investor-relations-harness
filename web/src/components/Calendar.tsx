import { useState } from "react";
import { CALENDAR, MONTHS, type CalEvent } from "../data/workspace";

export function Calendar() {
  const [events, setEvents] = useState<CalEvent[]>(() =>
    CALENDAR.events.map((e) => ({ ...e }))
  );
  const [hl, setHl] = useState<number | null>(null);

  const done = events.filter((e) => e.status === "done").length;
  const tot = events.length;
  const { year, month } = CALENDAR;

  const toggle = (i: number) =>
    setEvents((prev) =>
      prev.map((e, idx) => (idx === i ? { ...e, status: e.status === "done" ? "todo" : "done" } : e))
    );

  // Month grid.
  const first = new Date(year, month, 1).getDay();
  const days = new Date(year, month + 1, 0).getDate();
  const byday: Record<number, CalEvent[]> = {};
  events.forEach((e) => {
    const em = e.m ?? month;
    if (em === month) (byday[e.d] = byday[e.d] || []).push(e);
  });
  const wd = ["S", "M", "T", "W", "T", "F", "S"];

  const cells: JSX.Element[] = [];
  for (let i = 0; i < first; i++) cells.push(<div className="cal-day empty" key={`e${i}`} />);
  for (let d = 1; d <= days; d++) {
    const evs = byday[d] || [];
    cells.push(
      <div className={`cal-day ${evs.length ? "has" : ""}`} key={d}
        onClick={() => { if (evs.length) { const idx = events.findIndex((e) => (e.m ?? month) === month && e.d === d); setHl(idx); setTimeout(() => setHl(null), 2200); } }}>
        <span className="cd-n">{d}</span>
        <span className="cd-dots">
          {evs.map((e, j) => <span className={`cdot ${e.status}`} key={j} />)}
        </span>
      </div>
    );
  }

  // Ordered task list.
  const order = events
    .map((e, idx) => ({ e, idx }))
    .sort((a, b) => {
      const am = a.e.m ?? month, bm = b.e.m ?? month;
      return am - bm || a.e.d - b.e.d;
    });

  return (
    <div className="cwrap">
      <div className="cv-head">
        <h1>Earnings calendar</h1>
        <div className="dek">The Q2 FY2026 close runbook — key dates, owners, and tasks.</div>
      </div>
      <div className="calprog">
        <div className="calprog-bar"><i style={{ width: `${(done / tot) * 100}%` }} /></div>
        <span>{done} of {tot} complete</span>
      </div>
      <div className="cal-head">{MONTHS[month]} {year}</div>
      <div className="cal-grid">
        {wd.map((d, i) => <div className="cal-wd" key={i}>{d}</div>)}
        {cells}
      </div>
      <div className="sb-cap" style={{ margin: "22px 0 10px" }}>Tasks &amp; milestones</div>
      <div>
        {order.map(({ e, idx }) => {
          const mm = e.m ?? month;
          const dlabel = `${MONTHS[mm].slice(0, 3)} ${e.d}`;
          const lab = e.status === "done" ? "Done" : e.status === "doing" ? "In progress" : "Upcoming";
          const attest = /Attest/.test(e.title);
          return (
            <div className={`task ${e.status}${e.type === "milestone" ? " milestone" : ""}${hl === idx ? " hl" : ""}`} key={idx}>
              <button className="taskchk" onClick={() => toggle(idx)}>{e.status === "done" ? "✓" : ""}</button>
              <div className="task-main">
                <div className="task-t">{e.title}{attest && <span className="attesttag">Attest</span>}</div>
                <div className="task-meta">{dlabel} · {e.owner}</div>
              </div>
              <span className={`task-st ${e.status}`}>{lab}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
