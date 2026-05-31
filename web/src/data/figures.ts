import type { Figure } from "../types";

const WARN_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 9v4M12 17h.01M10.3 3.9L2 18a1.9 1.9 0 001.7 2.9h16.6A1.9 1.9 0 0022 18L13.7 3.9a1.9 1.9 0 00-3.4 0z"/></svg>';
const EYE_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="2.5"/></svg>';

// The fact-with-provenance records behind every figure token, ported verbatim
// from the prototype. `filed` is the canonical as-filed value used to re-verify
// edits; null means there is no filed source (guidance).
export const FIGURES: Record<string, Figure> = {
  rev: {
    id: "rev",
    v: "$1.24 billion",
    lbl: "Total revenue, Q1 FY2026",
    st: "v",
    badge: "10-Q",
    tag: "10-Q",
    cur: "$1.24 billion",
    filed: "$1.24 billion",
    snip: "Total revenue was <mark>$1,241.3 million</mark> for the three months ended March 31, 2026.",
    cite: "Form 10-Q · Statements of Operations · p.4",
    page:
      '<div class="filing"><div class="fhd">MERIDIAN SYSTEMS, INC.</div><div class="fsub">Condensed Consolidated Statements of Operations (unaudited) — in thousands</div>' +
      '<table class="ftable"><tr class="head"><td>Three months ended March 31,</td><td class="num">2026</td><td class="num">2025</td></tr>' +
      "<tr><td>Revenue:</td><td></td><td></td></tr>" +
      "<tr><td>&nbsp;&nbsp;Cloud</td><td class=\"num\">611,800</td><td class=\"num\">467,000</td></tr>" +
      "<tr><td>&nbsp;&nbsp;License &amp; services</td><td class=\"num\">629,500</td><td class=\"num\">584,900</td></tr>" +
      '<tr class="tot"><td>Total revenue</td><td class="num"><mark class="hl">1,241,300</mark></td><td class="num">1,051,900</td></tr>' +
      "<tr><td>Cost of revenue</td><td class=\"num\">372,400</td><td class=\"num\">336,600</td></tr>" +
      '<tr class="tot"><td>Income from operations</td><td class="num">278,000</td><td class="num">221,000</td></tr></table>' +
      '<p class="note">Press release rounds $1,241.3M → $1.24B (one decimal), within disclosure-rounding policy.</p></div>',
    reason:
      "Matched verbatim to the as-filed condensed income statement, then rounded one decimal place.",
    fields: [
      { label: "As filed", value: "$1,241.3M" },
      { label: "In draft", value: "$1.24B" },
      { label: "Rounding", value: "✓ within policy" },
    ],
  },
  gaapeps: {
    id: "gaapeps",
    v: "$0.87",
    lbl: "GAAP diluted EPS",
    st: "v",
    badge: "10-Q",
    tag: "10-Q",
    cur: "$0.87",
    filed: "$0.87",
    snip: "Diluted net income per share was <mark>$0.87</mark> on 232.1M diluted shares.",
    cite: "Form 10-Q · Statements of Operations · p.5",
    page:
      '<div class="filing"><div class="fhd">MERIDIAN SYSTEMS, INC.</div><div class="fsub">Net income per share (unaudited) — in thousands, except per-share</div>' +
      '<table class="ftable"><tr class="head"><td>Three months ended March 31,</td><td class="num">2026</td><td class="num">2025</td></tr>' +
      "<tr><td>Net income</td><td class=\"num\">202,000</td><td class=\"num\">158,400</td></tr>" +
      "<tr><td>Diluted shares</td><td class=\"num\">232,100</td><td class=\"num\">235,400</td></tr>" +
      '<tr class="tot"><td>Diluted EPS</td><td class="num"><mark class="hl">$0.87</mark></td><td class="num">$0.67</td></tr></table></div>',
    reason: "Matched to the as-filed diluted EPS line. No rounding adjustment.",
    fields: [
      { label: "As filed", value: "$0.87" },
      { label: "In draft", value: "$0.87" },
      { label: "Status", value: "✓ exact" },
    ],
  },
  nongaapeps: {
    id: "nongaapeps",
    v: "$1.12",
    lbl: "Non-GAAP diluted EPS",
    st: "v",
    badge: "8-K Ex.99.1",
    tag: "8-K",
    cur: "$1.12",
    filed: "$1.12",
    snip: "Non-GAAP diluted EPS of <mark>$1.12</mark>, excluding stock-based comp and amortization of intangibles.",
    cite: "Form 8-K · Exhibit 99.1 · Non-GAAP reconciliation",
    page:
      '<div class="filing"><div class="fhd">EXHIBIT 99.1</div><div class="fsub">Reconciliation of GAAP to Non-GAAP Diluted EPS</div>' +
      '<table class="ftable"><tr class="head"><td></td><td class="num">Q1 FY2026</td></tr>' +
      "<tr><td>GAAP diluted EPS</td><td class=\"num\">$0.87</td></tr>" +
      "<tr><td>&nbsp;&nbsp;Stock-based compensation</td><td class=\"num\">0.18</td></tr>" +
      "<tr><td>&nbsp;&nbsp;Amortization of intangibles</td><td class=\"num\">0.07</td></tr>" +
      '<tr class="tot"><td>Non-GAAP diluted EPS</td><td class="num"><mark class="hl">$1.12</mark></td></tr></table>' +
      '<p class="note">Reg G: GAAP measure presented with equal prominence; reconciliation bridge present. ✓</p></div>',
    reason:
      "Ties to the reconciliation exhibit. Reg G check passed — the GAAP-to-non-GAAP bridge is disclosed.",
    fields: [
      { label: "GAAP EPS", value: "$0.87" },
      { label: "Adjustments", value: "+$0.25" },
      { label: "Non-GAAP", value: "$1.12" },
    ],
  },
  cloudrev: {
    id: "cloudrev",
    v: "$612 million",
    lbl: "Cloud segment revenue",
    st: "v",
    badge: "10-Q",
    tag: "10-Q",
    cur: "$612 million",
    filed: "$612 million",
    snip: "Cloud segment revenue was <mark>$611.8 million</mark> for the quarter.",
    cite: "Form 10-Q · Note 14, Segment Information · p.27",
    page:
      '<div class="filing"><div class="fhd">Note 14 — Segment Information</div><div class="fsub">Revenue by reportable segment — in thousands</div>' +
      '<table class="ftable"><tr class="head"><td>Three months ended March 31, 2026</td><td class="num">Revenue</td></tr>' +
      '<tr><td>Cloud</td><td class="num"><mark class="hl">611,800</mark></td></tr>' +
      "<tr><td>License &amp; services</td><td class=\"num\">629,500</td></tr>" +
      '<tr class="tot"><td>Total</td><td class="num">1,241,300</td></tr></table>' +
      '<p class="note">Rounded $611.8M → $612M in the release.</p></div>',
    reason: "Matched to the segment footnote and rounded within policy.",
    fields: [
      { label: "As filed", value: "$611.8M" },
      { label: "In draft", value: "$612M" },
      { label: "Rounding", value: "✓ within policy" },
    ],
  },
  cloudgrowth: {
    id: "cloudgrowth",
    v: "31%",
    lbl: "Cloud growth, YoY",
    st: "f",
    badge: "CONFLICT",
    tag: "!",
    cur: "31%",
    filed: "29%",
    snip: "<b>Prior-period base was restated.</b> Draft uses $467.0M; the FY2025 10-K restated it to <mark>$474.3M</mark>.",
    cite: "Conflict: Q1 draft base vs FY2025 Form 10-K, Note 2",
    page:
      '<div class="filing"><div class="srctitle" style="color:#9E382C">' +
      WARN_ICON +
      "&nbsp;Cross-filing conflict</div>" +
      "<p>The 31% figure was computed as <b>$611.8M ÷ $467.0M − 1</b>. But the prior-year base used here was <b>later restated</b>:</p>" +
      '<div class="stack" style="margin:14px 0">' +
      '<div class="minicard"><div class="ml">Base used in this draft</div><div class="mv">$467.0M</div><div class="note" style="margin-top:4px">from the Q1 FY2025 earnings release</div></div>' +
      '<div class="minicard bad"><div class="ml">Restated base — Form 10-K, Note 2</div><div class="mv"><mark class="hl">$474.3M</mark></div><div class="note" style="margin-top:4px">revenue-recognition reclassification</div></div>' +
      '<div class="minicard good"><div class="ml">Corrected YoY growth</div><div class="mv">29%</div></div>' +
      '</div><p class="note">A summarization tool reads each filing in isolation and never catches this. Attest reconciles the base across filings.</p></div>',
    reason:
      "The growth figure used the <b>original</b> prior-year cloud number. That base was restated upward in the 10-K, so the correct figure is <b>29%</b>, not 31%.",
    fields: [
      { label: "Draft base (orig.)", value: "$467.0M", tone: "" },
      { label: "Restated base", value: "$474.3M", tone: "bad" },
      { label: "Stated growth", value: "31%", tone: "bad" },
      { label: "Corrected", value: "29%", tone: "good" },
    ],
  },
  ocf: {
    id: "ocf",
    v: "$338 million",
    lbl: "Operating cash flow",
    st: "v",
    badge: "10-Q",
    tag: "10-Q",
    cur: "$338 million",
    filed: "$338 million",
    snip: "Net cash provided by operating activities was <mark>$338.2 million</mark>.",
    cite: "Form 10-Q · Statements of Cash Flows · p.7",
    page:
      '<div class="filing"><div class="fhd">MERIDIAN SYSTEMS, INC.</div><div class="fsub">Condensed Statements of Cash Flows (unaudited) — in thousands</div>' +
      '<table class="ftable"><tr class="head"><td>Three months ended March 31,</td><td class="num">2026</td></tr>' +
      "<tr><td>Net income</td><td class=\"num\">202,000</td></tr>" +
      "<tr><td>&nbsp;&nbsp;Depreciation &amp; amortization</td><td class=\"num\">88,400</td></tr>" +
      "<tr><td>&nbsp;&nbsp;Changes in working capital</td><td class=\"num\">47,800</td></tr>" +
      '<tr class="tot"><td>Net cash from operating activities</td><td class="num"><mark class="hl">338,200</mark></td></tr>' +
      "<tr><td>Repurchases of common stock</td><td class=\"num\">(250,000)</td></tr></table></div>",
    reason: "Matched to the as-filed cash flow statement, rounded within policy.",
    fields: [
      { label: "As filed", value: "$338.2M" },
      { label: "In draft", value: "$338M" },
      { label: "Status", value: "✓ within policy" },
    ],
  },
  buyback: {
    id: "buyback",
    v: "$250 million",
    lbl: "Share repurchases, Q1",
    st: "v",
    badge: "10-Q",
    tag: "10-Q",
    cur: "$250 million",
    filed: "$250 million",
    snip: "Repurchases of common stock of <mark>$250.0 million</mark> during the quarter.",
    cite: "Form 10-Q · Note 11, Stockholders’ Equity · p.24",
    page:
      '<div class="filing"><div class="fhd">Note 11 — Stockholders’ Equity</div><div class="fsub">Share repurchase activity — in thousands</div>' +
      '<table class="ftable"><tr class="head"><td>Three months ended March 31, 2026</td><td class="num">Amount</td></tr>' +
      "<tr><td>Shares repurchased (000s)</td><td class=\"num\">2,140</td></tr>" +
      '<tr class="tot"><td>Total cost</td><td class="num"><mark class="hl">$250,000</mark></td></tr></table></div>',
    reason:
      "Matched to the equity footnote and the cash flow statement (cross-checked).",
    fields: [
      { label: "As filed", value: "$250.0M" },
      { label: "In draft", value: "$250M" },
      { label: "Status", value: "✓ exact" },
    ],
  },
  guidance: {
    id: "guidance",
    v: "$1.31–$1.34B",
    lbl: "Q2 FY2026 revenue guidance",
    st: "r",
    badge: "NOT FILED",
    tag: "?",
    cur: "$1.31 to $1.34 billion",
    filed: null,
    snip: "<b>No filed source.</b> Forward guidance is management input and cannot be traced to a filing.",
    cite: "Internal — Q2 planning memo (not a public document)",
    page:
      '<div class="filing"><div class="srctitle" style="color:#9A6A14">' +
      EYE_ICON +
      "&nbsp;Source is internal, not a filing</div>" +
      '<div class="minicard" style="border-color:#9A6A14;background:#F8EFDC"><div class="ml">Q2 plan memo v3 · Apr 21 · FP&amp;A</div>' +
      '<p style="font-family:var(--doc);font-size:13.5px;margin:6px 0 0">Planning range: <mark class="hl">$1.31B – $1.34B</mark> (midpoint ~16% YoY).</p></div>' +
      '<p class="note" style="margin-top:14px">Forward-looking figures have no filed source by definition. Attest will not mark this “traced” — it requires human sign-off and attaches safe-harbor language before the release can publish.</p></div>',
    reason:
      "Forward-looking guidance, so by definition there is no filed source. Flagged for sign-off; safe-harbor language must be attached before publish.",
    fields: [
      { label: "Source type", value: "Management" },
      { label: "Filed?", value: "No" },
      { label: "Needs", value: "Sign-off + safe harbor" },
    ],
  },
};
