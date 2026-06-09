import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";
import { App } from "./App";
import { client } from "./api/client";
import { detectNewFigures } from "./lib/verify";
import type { AnalyzeClaim, AnalyzeVerdict } from "./api/client";

// The user's own walkthrough, graded at the DOM: fetch a historical release,
// open it, and check the two things they actually judge —
//   1. every number in the rendered prose is a linked chip (none left as plain
//      text), and
//   2. nothing is falsely linked: a traced chip cites its SEC source, an
//      untraced chip says so honestly (and never offers another company's demo
//      bind options), and the hover label matches the verdict.
vi.mock("./api/client", () => ({
  apiBaseUrl: "",
  client: {
    searchHistorical: vi.fn(),
    ingestHistorical: vi.fn(),
  },
}));

const TEXT = `Palo Alto Networks Reports Fiscal Third Quarter 2026 Financial Results

SANTA CLARA, Calif., May 28, 2026 -- Palo Alto Networks (NASDAQ: PANW), the global cybersecurity leader, today announced financial results for its fiscal third quarter 2026, ended April 30, 2026.

Total revenue for the fiscal third quarter 2026 grew 20% year over year to $3.0 billion, compared with total revenue of $2.5 billion for the fiscal third quarter 2025. Remaining performance obligation grew 25% year over year to $18.4 billion.

GAAP net income for the fiscal third quarter 2026 was $310 million, or $0.45 per diluted share. Non-GAAP net income per diluted share was $0.85.

Operating margin expanded 120 basis points year over year to 13.0%. Net cash provided by operating activities was $870 million, and free cash flow was $800 million.

The company ended the quarter serving more than 4,000 customers across its platforms.

For the fiscal fourth quarter 2026, the company expects total revenue in the range of $3.30 billion to $3.40 billion.`;

const EDGAR_PROV = (tag: string) => ({
  source_type: "edgar_xbrl",
  ref: `0001327567-26-000015#us-gaap:${tag}`,
  label: `Form 10-Q · us-gaap:${tag}`,
  excerpt: "",
});
const DERIVED_PROV = (identity: string) => ({
  source_type: "derived",
  ref: `derived:${identity}`,
  label: `Recomputed from filed sources (${identity})`,
  excerpt: "",
});

// Each figure in the prose, with the verdict the real backend renders for it.
// Spans are computed from the text exactly as the backend computes them.
const FIGURES: Array<{
  text: string;
  metric: string;
  period: string;
  verdict: string;
  reason: string;
  source_value?: string | null;
  provenance?: ReturnType<typeof EDGAR_PROV> | null;
}> = [
  { text: "20%", metric: "revenue_growth_yoy", period: "FY2026-Q3", verdict: "traced",
    reason: "Recomputed from filed sources (total_revenue FY2026-Q3 vs FY2025-Q3) within the rounding policy.",
    source_value: "20%", provenance: DERIVED_PROV("total_revenue FY2026-Q3 vs FY2025-Q3") },
  { text: "$3.0 billion", metric: "total_revenue", period: "FY2026-Q3", verdict: "traced",
    reason: "Matched the as-filed source within the rounding policy.",
    source_value: "$3,000,000,000", provenance: EDGAR_PROV("RevenueFromContractWithCustomerExcludingAssessedTax") },
  { text: "$2.5 billion", metric: "total_revenue", period: "FY2025-Q3", verdict: "traced",
    reason: "Matched the as-filed source within the rounding policy.",
    source_value: "$2,500,000,000", provenance: EDGAR_PROV("RevenueFromContractWithCustomerExcludingAssessedTax") },
  { text: "25%", metric: "rpo_growth_yoy", period: "FY2026-Q3", verdict: "traced",
    reason: "Recomputed from filed sources (total_rpo FY2026-Q3 vs FY2025-Q3) within the rounding policy.",
    source_value: "25%", provenance: DERIVED_PROV("total_rpo FY2026-Q3 vs FY2025-Q3") },
  { text: "$18.4 billion", metric: "total_rpo", period: "FY2026-Q3", verdict: "traced",
    reason: "Matched the as-filed source within the rounding policy.",
    source_value: "$18,400,000,000", provenance: EDGAR_PROV("RevenueRemainingPerformanceObligation") },
  { text: "$310 million", metric: "net_income", period: "FY2026-Q3", verdict: "traced",
    reason: "Matched the as-filed source within the rounding policy.",
    source_value: "$310,000,000", provenance: EDGAR_PROV("NetIncomeLoss") },
  { text: "$0.45", metric: "gaap_diluted_eps", period: "FY2026-Q3", verdict: "traced",
    reason: "Matched the as-filed source within the rounding policy.",
    source_value: "$0.45", provenance: EDGAR_PROV("EarningsPerShareDiluted") },
  { text: "$0.85", metric: "non_gaap_diluted_eps", period: "FY2026-Q3", verdict: "untraced",
    reason: "No source bound for 'non_gaap_diluted_eps' in FY2026-Q3.", source_value: null, provenance: null },
  { text: "120 basis points", metric: "operating_margin_change_bps", period: "FY2026-Q3", verdict: "traced",
    reason: "Recomputed from filed sources (operating_margin FY2026-Q3 vs FY2025-Q3) within the rounding policy.",
    source_value: "120 bps", provenance: DERIVED_PROV("operating_margin FY2026-Q3 vs FY2025-Q3") },
  { text: "13.0%", metric: "operating_margin", period: "FY2026-Q3", verdict: "traced",
    reason: "Recomputed from filed sources (operating_income / total_revenue) within the rounding policy.",
    source_value: "13%", provenance: DERIVED_PROV("operating_income / total_revenue") },
  { text: "$870 million", metric: "operating_cash_flow", period: "FY2026-Q3", verdict: "traced",
    reason: "Matched the as-filed source within the rounding policy.",
    source_value: "$870,000,000", provenance: EDGAR_PROV("NetCashProvidedByUsedInOperatingActivities") },
  { text: "$800 million", metric: "free_cash_flow", period: "FY2026-Q3", verdict: "traced",
    reason: "Recomputed from filed sources (operating_cash_flow - capex) within the rounding policy.",
    source_value: "$800,000,000", provenance: DERIVED_PROV("operating_cash_flow - capex") },
  { text: "4,000 customers", metric: "unidentified", period: "FY2026-Q3", verdict: "untraced",
    reason: "No source bound for 'unidentified' in FY2026-Q3.", source_value: null, provenance: null },
  { text: "$3.30 billion to $3.40 billion", metric: "q2_revenue_guidance", period: "FY2026-Q4", verdict: "untraced",
    reason: "No source bound for 'q2_revenue_guidance' in FY2026-Q4.", source_value: null, provenance: null },
];

function buildPayload() {
  const claims: AnalyzeClaim[] = [];
  const verdicts: AnalyzeVerdict[] = [];
  FIGURES.forEach((f, i) => {
    const start = TEXT.indexOf(f.text);
    if (start < 0) throw new Error(`fixture drift: ${f.text} not in TEXT`);
    claims.push({
      claim_id: `c${i}`, metric: f.metric, period: f.period, entity: "PANW",
      displayed_text: f.text, span: [start, start + f.text.length],
    });
    verdicts.push({
      claim_id: `c${i}`, metric: f.metric, period: f.period, displayed_text: f.text,
      verdict: f.verdict, reason: f.reason, source_value: f.source_value ?? null,
      provenance: f.provenance ?? null, as_of: f.verdict === "traced" ? "2026-06-03" : null,
    });
  });
  return {
    entity: "PANW",
    documents: [{
      url: "https://www.paloaltonetworks.com/q3", title: "PANW Earnings release · FY2026-Q3 (pub 2026-05-28)",
      published_date: "2026-05-28", ingested: FIGURES.length, skipped: 0, period: "FY2026-Q3",
      text: TEXT, claims, verdicts,
    }],
    total_ingested: FIGURES.length,
  };
}

const CANDIDATE = {
  url: "https://www.paloaltonetworks.com/q3",
  title: "PANW Earnings release · FY2026-Q3 (pub 2026-05-28)",
  published_date: "2026-05-28",
  source: "paloaltonetworks.com",
  snippet: "Total revenue for the fiscal third quarter 2026 grew 20% year over year",
  doc_type: "release",
  period: "FY2026-Q3",
};

const mock = client as unknown as {
  searchHistorical: ReturnType<typeof vi.fn>;
  ingestHistorical: ReturnType<typeof vi.fn>;
};

async function loadTheRelease() {
  fireEvent.click(screen.getByRole("button", { name: "Fetch historical" }));
  fireEvent.change(screen.getByPlaceholderText(/PANW or Palo Alto Networks/i), {
    target: { value: "Palo Alto Networks" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  await screen.findByText(CANDIDATE.title);
  fireEvent.click(screen.getByRole("button", { name: /Load 1 selected/i }));
  await waitFor(() => expect(mock.ingestHistorical).toHaveBeenCalledTimes(1));
  // The loaded document renders in the stage.
  const doc = document.querySelector(".doc") as HTMLElement;
  expect(doc).toBeTruthy();
  return doc;
}

describe("user walkthrough: every number linked, none falsely", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mock.searchHistorical.mockReset();
    mock.ingestHistorical.mockReset();
    mock.searchHistorical.mockResolvedValue({ entity: "PANW", candidates: [CANDIDATE] });
    mock.ingestHistorical.mockResolvedValue(buildPayload());
    document.body.innerHTML = "";
    render(<App />);
  });

  it("renders every figure in the prose as a linked chip — no plain-text numbers", async () => {
    const doc = await loadTheRelease();
    // All sixteen figures are chips…
    const chips = Array.from(doc.querySelectorAll(".fig")).map((el) => el.textContent?.trim());
    for (const f of FIGURES) expect(chips).toContain(f.text);
    // …and no figure-shaped text remains OUTSIDE a chip: strip the chips and
    // scan what's left with the same detector the editor uses.
    for (const p of Array.from(doc.querySelectorAll("p"))) {
      const clone = p.cloneNode(true) as HTMLElement;
      clone.querySelectorAll(".fig").forEach((n) => n.remove());
      const leftovers = detectNewFigures(clone.textContent || "");
      expect(leftovers, `unlinked numbers in: "${clone.textContent}"`).toEqual([]);
    }
  });

  it("a traced figure's chip opens the modal citing its SEC source", async () => {
    const doc = await loadTheRelease();
    const chip = Array.from(doc.querySelectorAll(".fig")).find(
      (el) => el.textContent?.trim() === "$3.0 billion"
    ) as HTMLElement;
    expect(chip.className).toContain("v"); // traced state
    fireEvent.click(chip);

    const modal = document.querySelector(".modal") as HTMLElement;
    expect(within(modal).getByText("TRACED")).toBeInTheDocument();
    expect(modal.querySelector(".ttl")?.textContent).toContain("Total revenue · FY2026-Q3");
    // The citation names the actual filed source, not just a metric.
    expect(modal.textContent).toContain("Form 10-Q · us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax");
    expect(modal.textContent).toContain("$3,000,000,000");
  });

  it("a recomputed growth figure cites the levels it was recomputed from", async () => {
    const doc = await loadTheRelease();
    const chip = Array.from(doc.querySelectorAll(".fig")).find(
      (el) => el.textContent?.trim() === "20%"
    ) as HTMLElement;
    expect(chip.className).toContain("v");
    fireEvent.click(chip);
    const modal = document.querySelector(".modal") as HTMLElement;
    expect(modal.textContent).toContain("total_revenue FY2026-Q3 vs FY2025-Q3");
  });

  it("an untraced figure is honest — no other company's bind options, no false labels", async () => {
    const doc = await loadTheRelease();
    const chip = Array.from(doc.querySelectorAll(".fig")).find(
      (el) => el.textContent?.trim() === "$0.85"
    ) as HTMLElement;
    expect(chip.className).toContain("u");
    fireEvent.click(chip);

    const modal = document.querySelector(".modal") as HTMLElement;
    expect(within(modal).getByText("UNTRACED")).toBeInTheDocument();
    expect(modal.textContent).toContain("No source bound");
    // Never the demo close pack's sources on a fetched PANW document.
    expect(modal.textContent).not.toContain("ATLS");
    expect(within(modal).queryByText(/Bind/)).not.toBeInTheDocument();
  });

  it("hover popovers label each state honestly (untraced is never 'Conflict')", async () => {
    const doc = await loadTheRelease();
    const untraced = Array.from(doc.querySelectorAll(".fig")).find(
      (el) => el.textContent?.trim() === "$0.85"
    ) as HTMLElement;
    fireEvent.mouseEnter(untraced);
    const pop = document.querySelector(".pop.show") as HTMLElement;
    expect(pop.textContent).toContain("Untraced");
    expect(pop.textContent).not.toContain("Conflict");
    fireEvent.mouseLeave(untraced);

    const traced = Array.from(doc.querySelectorAll(".fig")).find(
      (el) => el.textContent?.trim() === "$870 million"
    ) as HTMLElement;
    fireEvent.mouseEnter(traced);
    const pop2 = document.querySelector(".pop.show") as HTMLElement;
    expect(pop2.textContent).toContain("Traced");
  });

  it("Copy citation actually copies the figure's citation", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    const doc = await loadTheRelease();
    const chip = Array.from(doc.querySelectorAll(".fig")).find(
      (el) => el.textContent?.trim() === "$3.0 billion"
    ) as HTMLElement;
    fireEvent.click(chip);
    fireEvent.click(screen.getByRole("button", { name: "Copy citation" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = writeText.mock.calls[0][0] as string;
    expect(copied).toContain("$3.0 billion");
    expect(copied).toContain("Form 10-Q · us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax");
  });

  it("the workspace names the document exactly as reviewed and reports tie-outs", async () => {
    await loadTheRelease();
    // The crumb above the document carries the reviewed title.
    expect(document.querySelector(".dt-crumb")?.textContent).toContain(
      "PANW Earnings release · FY2026-Q3 (pub 2026-05-28)"
    );
    // The toast reports what the user cares about: traced coverage.
    expect(document.querySelector(".toast")?.textContent).toContain("11 of 14 figures traced");
  });
});
