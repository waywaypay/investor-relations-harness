import { describe, expect, it } from "vitest";
import { detectTicker } from "./ticker";

describe("detectTicker", () => {
  it("pulls the symbol from the listing formats earnings releases use", () => {
    expect(detectTicker("Acme Corp (NASDAQ: PANW) reported strong results.")).toBe("PANW");
    expect(detectTicker("Shares trade on the NYSE: CRM after the close.")).toBe("CRM");
    expect(detectTicker("(Nasdaq Global Select Market: AAPL)")).toBe("AAPL");
    expect(detectTicker("Class B stock (NYSE: BRK.B) was unchanged.")).toBe("BRK.B");
    expect(detectTicker("(NASDAQ:MSFT) — no space after the colon")).toBe("MSFT");
  });

  it("returns null when no listing is present", () => {
    expect(detectTicker("Revenue rose 12% to $1.5 billion this quarter.")).toBeNull();
    expect(detectTicker("")).toBeNull();
    // A bare colon after a non-exchange word must not be mistaken for a listing.
    expect(detectTicker("Total revenue: $1.5B")).toBeNull();
  });
});
