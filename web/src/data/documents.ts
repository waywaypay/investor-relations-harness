import type { DocMeta, Inline } from "../types";

const f = (id: string): Inline => ({ kind: "fig", id });
const n = (id: string): Inline => ({ kind: "nar", id });
const t = (html: string): Inline => ({ kind: "text", html });

export const ICON_RELEASE =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h13l3 3v13H4z"/><path d="M8 9h8M8 13h8M8 17h5"/></svg>';
export const ICON_SCRIPT =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 00-3 3v6a3 3 0 006 0V5a3 3 0 00-3-3z"/><path d="M5 11a7 7 0 0014 0M12 18v3"/></svg>';
export const ICON_QA =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.1 9a3 3 0 015.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/><circle cx="12" cy="12" r="9"/></svg>';
export const ICON_OTHER =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h8M8 17h5"/></svg>';

// Icon to use for a given document kind (uploads pick by kind).
export const ICON_FOR_KIND: Record<string, string> = {
  release: ICON_RELEASE,
  script: ICON_SCRIPT,
  qa: ICON_QA,
  other: ICON_OTHER,
};

export const DOCS: DocMeta[] = [
  {
    id: "release",
    name: "Earnings release",
    kind: "Press release · 8-K Ex.99.1",
    icon: ICON_RELEASE,
    blocks: [
      { kind: "eyebrow", text: "For Immediate Release" },
      { kind: "h1", text: "Atlas Systems Reports First Quarter Fiscal 2026 Results" },
      {
        kind: "dek",
        text: "Cloud segment momentum and disciplined operating leverage drive record quarterly revenue.",
      },
      { kind: "hr" },
      {
        kind: "p",
        parts: [
          t(
            "SAN JOSE, Calif. — Atlas Systems, Inc. (NASDAQ: ATLS) today announced financial results for its first quarter ended March 31, 2026. The company reported total revenue of "
          ),
          f("rev"),
          t(", an increase of 18% year over year, driven by continued enterprise adoption of its cloud platform."),
        ],
      },
      {
        kind: "p",
        parts: [
          t("The company delivered GAAP diluted earnings per share of "),
          f("gaapeps"),
          t(" and non-GAAP diluted earnings per share of "),
          f("nongaapeps"),
          t(", reflecting continued operating leverage."),
        ],
      },
      { kind: "h2", text: "Segment performance" },
      {
        kind: "p",
        parts: [
          t("Cloud segment revenue reached "),
          f("cloudrev"),
          t(", up "),
          f("cloudgrowth"),
          t(" from the prior-year period. Operating cash flow for the quarter was "),
          f("ocf"),
          t("."),
        ],
      },
      { kind: "h2", text: "Capital return" },
      {
        kind: "p",
        parts: [
          t("During the quarter, the company repurchased "),
          f("buyback"),
          t(" of common stock under its existing program."),
        ],
      },
      { kind: "h2", text: "Outlook" },
      {
        kind: "p",
        parts: [
          t("For the second quarter of fiscal 2026, the company expects total revenue in the range of "),
          f("guidance"),
          t("."),
        ],
      },
    ],
  },
  {
    id: "script",
    name: "Prepared remarks",
    kind: "Earnings call script",
    icon: ICON_SCRIPT,
    blocks: [
      { kind: "eyebrow", text: "Confidential — Internal" },
      { kind: "h1", text: "Q1 FY2026 Earnings Call — Prepared Remarks" },
      {
        kind: "dek",
        text: "Speaker script for the live call. Figures trace to source; every claim is checked for narrative and wording consistency.",
      },
      { kind: "hr" },
      { kind: "narbar" },
      { kind: "speaker", text: "Jordan Reyes · Chief Executive Officer" },
      {
        kind: "p",
        parts: [
          t("Good afternoon, everyone, and thank you for joining us. We delivered a "),
          n("strong"),
          t(" to fiscal 2026. Total revenue was "),
          f("rev"),
          t(", and our "),
          n("cloudword"),
          t(" continued to lead the way, with segment revenue of "),
          f("cloudrev"),
          t(", up "),
          f("cloudgrowth"),
          t(" year over year, with momentum "),
          n("accel"),
          t(" into the back half. "),
          t('<span class="cue">[pause]</span>'),
        ],
      },
      { kind: "speaker", text: "Sam Okafor · Chief Financial Officer" },
      {
        kind: "p",
        parts: [
          t("Thanks, Jordan. Turning to the numbers: non-GAAP diluted earnings per share were "),
          f("nongaapeps"),
          t(", and we generated "),
          f("ocf"),
          t(" of operating cash flow in the quarter. We also returned capital to shareholders, repurchasing "),
          f("buyback"),
          t(" of common stock."),
        ],
      },
      {
        kind: "p",
        parts: [
          t("Looking ahead, for the second quarter "),
          n("fls"),
          t(" total revenue in the range of "),
          f("guidance"),
          t(". "),
          t('<span class="cue">[refer to safe-harbor statement]</span>'),
        ],
      },
    ],
  },
  {
    id: "qa",
    name: "Q&A prep",
    kind: "Anticipated analyst questions",
    icon: ICON_QA,
    blocks: [
      { kind: "eyebrow", text: "Confidential — Internal" },
      { kind: "h1", text: "Q1 FY2026 — Q&A Preparation" },
      {
        kind: "dek",
        text: "Likely questions and approved answers. Figures stay locked to the release and the filing.",
      },
      { kind: "hr" },
      {
        kind: "qa",
        tag: "CLOUD",
        q: "Cloud growth looks like it decelerated — how should we think about the trajectory?",
        a: [
          t("Cloud remains our fastest-growing segment, reaching "),
          f("cloudrev"),
          t(" this quarter, up "),
          f("cloudgrowth"),
          t(" year over year. We continue to see durable enterprise demand."),
        ],
      },
      {
        kind: "qa",
        tag: "GUIDANCE",
        q: "What are the key assumptions behind your Q2 outlook?",
        a: [
          t("Our guidance of "),
          f("guidance"),
          t(" assumes continued momentum off this quarter’s "),
          f("rev"),
          t(" in revenue, with normal seasonality."),
        ],
      },
      {
        kind: "qa",
        tag: "MARGINS",
        q: "Can you bridge GAAP to non-GAAP profitability?",
        a: [
          t("GAAP diluted EPS was "),
          f("gaapeps"),
          t("; non-GAAP diluted EPS was "),
          f("nongaapeps"),
          t(", with the difference driven by stock-based compensation and amortization of acquired intangibles."),
        ],
      },
    ],
  },
];
