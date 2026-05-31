// Consensus + calendar data, ported verbatim from the prototype.

export interface Analyst {
  firm: string;
  rev: number;
  cloud: number;
  eps: number;
  opm: number;
  q2: number;
}

export const ANALYSTS: Analyst[] = [
  { firm: "Morgan Stanley", rev: 1230, cloud: 605, eps: 1.1, opm: 22.1, q2: 1318 },
  { firm: "Goldman Sachs", rev: 1245, cloud: 615, eps: 1.13, opm: 22.5, q2: 1330 },
  { firm: "J.P. Morgan", rev: 1238, cloud: 610, eps: 1.11, opm: 22.3, q2: 1322 },
  { firm: "Evercore ISI", rev: 1228, cloud: 600, eps: 1.09, opm: 22.0, q2: 1315 },
  { firm: "Wells Fargo", rev: 1250, cloud: 618, eps: 1.14, opm: 22.6, q2: 1335 },
];

export type CUnit = "m" | "eps" | "pct";

export interface CMetric {
  key: keyof Omit<Analyst, "firm">;
  label: string;
  unit: CUnit;
  actual: number;
  actualLabel: string;
  isGuide?: boolean;
}

export const CMETRICS: CMetric[] = [
  { key: "rev", label: "Revenue", unit: "m", actual: 1241.3, actualLabel: "$1,241M" },
  { key: "cloud", label: "Cloud revenue", unit: "m", actual: 611.8, actualLabel: "$612M" },
  { key: "eps", label: "Non-GAAP EPS", unit: "eps", actual: 1.12, actualLabel: "$1.12" },
  { key: "opm", label: "Operating margin", unit: "pct", actual: 22.4, actualLabel: "22.4%" },
  { key: "q2", label: "Q2 revenue (guide)", unit: "m", actual: 1325, actualLabel: "$1,325M", isGuide: true },
];

export interface CalEvent {
  d: number;
  m?: number; // month override (0-based); defaults to the calendar month
  title: string;
  owner: string;
  status: "done" | "doing" | "todo";
  type?: "milestone";
}

export const CALENDAR = {
  year: 2026,
  month: 6, // July (0-based)
  events: [
    { d: 30, m: 5, title: "Q2 FY26 quarter end", owner: "—", status: "done", type: "milestone" },
    { d: 7, title: "Books closed · actuals locked", owner: "Finance", status: "done" },
    { d: 10, title: "Draft release & script in Attest", owner: "IR", status: "doing" },
    { d: 14, title: "Verify figures + narrative (Attest)", owner: "IR", status: "todo" },
    { d: 15, title: "Legal & disclosure committee review", owner: "Legal", status: "todo" },
    { d: 17, title: "Board approval", owner: "CFO", status: "todo" },
    { d: 22, title: "Earnings release (8-K) + call 5:00pm ET", owner: "IR", status: "todo", type: "milestone" },
    { d: 24, title: "File 10-Q", owner: "Legal", status: "todo" },
    { d: 25, title: "Post-call debrief", owner: "IR", status: "todo" },
  ] as CalEvent[],
};

export const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
