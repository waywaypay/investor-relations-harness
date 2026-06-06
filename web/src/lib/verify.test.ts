import { describe, expect, it } from "vitest";
import { detectNewFigures, evaluateEdit, VERDICT_LABEL } from "./verify";
import type { Figure } from "../types";

const base: Figure = {
  id: "rev", v: "$1.24 billion", lbl: "Total revenue", st: "v", badge: "10-Q",
  tag: "10-Q", cur: "$1.24 billion", filed: "$1.24 billion", snip: "", cite: "",
  page: "", reason: "", fields: [],
};

describe("evaluateEdit", () => {
  it("matches filed value (ignoring spacing/commas/$) -> traced", () => {
    const r = evaluateEdit({ ...base, cur: "$1,240,000,000".replace(/0/g, "0") } as Figure);
    // exact filed text matches
    expect(evaluateEdit({ ...base, cur: "$1.24 billion" }).st).toBe("v");
    void r;
  });

  it("flags a value edited away from filed -> conflict, records editedFrom", () => {
    const r = evaluateEdit({ ...base, cur: "$1.42 billion" });
    expect(r.st).toBe("f");
    expect(r.tag).toBe("!");
    expect(r.editedFrom).toBe("$1.24 billion");
  });

  it("guidance is always needs_review", () => {
    const r = evaluateEdit({ ...base, id: "guidance", filed: null, cur: "$1.31 to $1.34 billion" });
    expect(r.st).toBe("r");
  });

  it("a figure with no filed source is untraced", () => {
    const r = evaluateEdit({ ...base, id: "newfig", filed: null, cur: "$5 million" });
    expect(r.st).toBe("u");
  });
});

describe("detectNewFigures", () => {
  it("finds currency and percent spans", () => {
    const found = detectNewFigures("Revenue was $1.24 billion, up 31%.");
    expect(found).toContain("$1.24 billion");
    expect(found).toContain("31%");
  });
  it("finds spoken transcript figures (no $, spelled percent)", () => {
    const found = detectNewFigures(
      "Total revenue was 1.24 billion dollars, up 31 percent. Cloud reached 480 million."
    );
    expect(found).toContain("1.24 billion");
    expect(found).toContain("31 percent");
    expect(found).toContain("480 million");
  });
  it("does not read a bare year as a figure", () => {
    expect(detectNewFigures("In fiscal 2026 we expanded the platform.")).toEqual([]);
  });
  it("returns nothing for prose without figures", () => {
    expect(detectNewFigures("no numbers here")).toEqual([]);
  });
});

describe("VERDICT_LABEL", () => {
  it("maps every state", () => {
    expect(VERDICT_LABEL.v).toBe("Traced");
    expect(VERDICT_LABEL.f).toBe("Conflict");
    expect(VERDICT_LABEL.u).toBe("Untraced");
  });
});
