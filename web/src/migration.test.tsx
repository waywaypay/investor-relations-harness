import { describe, expect, it, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { App } from "./App";

// Regression: a returning user has uploaded documents persisted by an older
// bundle (before version control), so their localStorage entry has docs with no
// `versions`/`activeVersionId`. The new code iterates `doc.versions`
// unconditionally (persist effect + sidebar render); with no error boundary that
// TypeError blanked the whole page. The store must migrate old-shape persisted
// docs on load instead of crashing.
describe("persisted-upload migration", () => {
  beforeEach(() => {
    window.localStorage.clear();
    // An upload doc in the PRE-version-control shape: top-level `blocks`, but no
    // `versions` and no `activeVersionId`.
    const legacy = {
      docs: [
        {
          id: "u_legacy",
          kind: "u_legacy",
          name: "Legacy upload",
          subtitle: "8-K",
          icon: "doc",
          source: "upload",
          period: "Q1 FY2026",
          addedAt: "2026-05-01T00:00:00.000Z",
          blocks: [{ kind: "h1", text: "Legacy upload" }],
        },
      ],
      figures: {},
    };
    window.localStorage.setItem("attest.uploads.v1", JSON.stringify(legacy));
  });

  it("renders the workspace instead of crashing to a blank page", () => {
    render(<App />);
    // The brand renders only if the tree mounted without throwing.
    expect(screen.getByText("Attest")).toBeInTheDocument();
  });

  it("keeps the migrated upload visible in the library", () => {
    render(<App />);
    expect(screen.getByText("Legacy upload")).toBeInTheDocument();
  });
});
