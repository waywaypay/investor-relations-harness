import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { AnalyzeResult, FactRow } from "./types";

// Mock the API client so the workspace runs without a live backend.
const analyze = vi.fn();
const listFacts = vi.fn(async (): Promise<FactRow[]> => []);
const ingestDemo = vi.fn();

vi.mock("./api/client", () => ({
  TENANT: "meridian",
  apiBaseUrl: "",
  client: {
    analyze: (...a: unknown[]) => analyze(...a),
    listFacts: () => listFacts(),
    ingestDemo: () => ingestDemo(),
    ingestXbrl: vi.fn(),
    ingestGuidance: vi.fn(),
  },
}));

import { App } from "./App";

const sampleResult: AnalyzeResult = {
  document_id: "release",
  title: "Q1 FY2026 Earnings Release",
  kind: "release",
  entity: "MRDN",
  period: "FY2026-Q1",
  text: "Meridian reported total revenue of $1.24 billion this quarter.",
  claims: [
    {
      claim_id: "c1",
      document_id: "release",
      entity: "MRDN",
      metric: "total_revenue",
      period: "FY2026-Q1",
      displayed_text: "$1.24 billion",
      span: [35, 48],
      detect_confidence: "high",
    },
  ],
  verdicts: [
    {
      claim_id: "c1",
      document_id: "release",
      entity: "MRDN",
      metric: "total_revenue",
      period: "FY2026-Q1",
      displayed_text: "$1.24 billion",
      verdict: "traced",
      reason: "Exact match to the filed source.",
      provenance: { source: "MRDN 10-Q" },
      source_value: "$1.24 billion",
      as_of: null,
    },
  ],
  findings: [],
  counts: { traced: 1 },
  publishable: true,
  warnings: [],
};

describe("Attest workspace", () => {
  beforeEach(() => {
    analyze.mockReset();
    listFacts.mockReset();
    ingestDemo.mockReset();
    listFacts.mockResolvedValue([]);
  });

  it("renders the empty state with no preloaded documents", async () => {
    render(<App />);
    expect(screen.getByText("Attest")).toBeInTheDocument();
    expect(screen.getByText(/Upload a disclosure draft/i)).toBeInTheDocument();
    expect(screen.queryByText(/Meridian Systems Reports/i)).not.toBeInTheDocument();
  });

  it("uploads a pasted draft and renders its traced figure", async () => {
    analyze.mockResolvedValue(sampleResult);
    render(<App />);

    fireEvent.click(screen.getByText("+ Upload a document"));
    fireEvent.click(screen.getByText("Paste text"));
    fireEvent.change(screen.getByPlaceholderText(/Paste the press release/i), {
      target: { value: sampleResult.text },
    });
    fireEvent.click(screen.getByText("Analyze document"));

    await waitFor(() => expect(analyze).toHaveBeenCalledTimes(1));
    // The analyzed document now drives the workspace.
    expect(await screen.findByText("$1.24 billion")).toBeInTheDocument();
    expect(screen.getByText(/figures traced/i)).toBeInTheDocument();
    // It appears in the sidebar document list and can be toggled.
    expect(screen.getAllByText("Q1 FY2026 Earnings Release").length).toBeGreaterThan(0);
  });

  it("opens the figure modal with the verdict detail on click", async () => {
    analyze.mockResolvedValue(sampleResult);
    render(<App />);
    fireEvent.click(screen.getByText("+ Upload a document"));
    fireEvent.click(screen.getByText("Paste text"));
    fireEvent.change(screen.getByPlaceholderText(/Paste the press release/i), {
      target: { value: sampleResult.text },
    });
    fireEvent.click(screen.getByText("Analyze document"));

    const fig = await screen.findByText("$1.24 billion");
    fireEvent.click(fig);
    expect(screen.getByText(/Exact match to the filed source/i)).toBeInTheDocument();
    expect(screen.getByText(/Traced to a filed source/i)).toBeInTheDocument();
  });
});
