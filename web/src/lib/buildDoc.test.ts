import { describe, it, expect } from "vitest";
import { buildReferenceVersion } from "./buildDoc";
import type { AnalyzeClaim, AnalyzeVerdict } from "../api/client";

// A loaded historical document is analyzed against the issuer's filed SEC sources,
// so its figures must render with that real disposition (traced / conflict + the
// source value) — the link to the SEC database — not as unattributed numbers.
describe("buildReferenceVersion", () => {
  it("renders the backend's verdicts so figures link to their SEC source", () => {
    const text = "Total revenue was $2.59 billion, up 15% year over year.";
    const claims: AnalyzeClaim[] = [
      {
        claim_id: "c0",
        metric: "total_revenue",
        period: "FY2026-Q3",
        entity: "PANW",
        displayed_text: "$2.59 billion",
        span: [18, 31],
      },
    ];
    const verdicts: AnalyzeVerdict[] = [
      {
        claim_id: "c0",
        metric: "total_revenue",
        period: "FY2026-Q3",
        displayed_text: "$2.59 billion",
        verdict: "traced",
        reason: "Matched the as-filed source within the rounding policy.",
        source_value: "$2.59B",
      },
    ];

    const built = buildReferenceVersion(
      { text, title: "PANW Q3 release", kind: "release", source: "prnewswire.com", period: "FY2026-Q3", claims, verdicts },
      "v_test"
    );

    const figs = Object.values(built.figures);
    expect(figs.length).toBe(1);
    expect(figs[0].st).toBe("v"); // traced — tied to the filed SEC source
    expect(figs[0].lbl).toMatch(/total revenue/i);
    expect(figs[0].lbl).not.toMatch(/reference figure/i); // a real metric, not an unattributed number
    // The source value (the link to the SEC database) is surfaced in the figure.
    expect(JSON.stringify(figs[0].fields)).toContain("$2.59B");
  });

  it("falls back to untraced reference figures when no analysis is provided", () => {
    const built = buildReferenceVersion(
      { text: "Revenue was $2.59 billion this quarter.", title: "x", kind: "release", period: "FY2026-Q3" },
      "v_test2"
    );
    const figs = Object.values(built.figures);
    expect(figs.length).toBeGreaterThan(0);
    expect(figs.every((f) => f.st === "u")).toBe(true);
    expect(figs[0].lbl).toMatch(/reference figure/i);
  });
});
