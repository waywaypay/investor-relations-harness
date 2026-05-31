import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { App } from "./App";

describe("Attest workspace", () => {
  beforeEach(() => render(<App />));

  it("renders the brand and the release as the default document", () => {
    expect(screen.getByText("Attest")).toBeInTheDocument();
    expect(
      screen.getByText(/Meridian Systems Reports First Quarter Fiscal 2026 Results/i)
    ).toBeInTheDocument();
  });

  it("shows the coverage summary for the release (6 of 8 traced)", () => {
    // The release has 8 figures: 6 traced, 1 conflict (cloudgrowth), 1 review (guidance).
    expect(screen.getByText(/figures traced/i)).toBeInTheDocument();
    const cov = screen.getByText(/figures traced/i).closest(".cov-txt")!;
    expect(within(cov as HTMLElement).getAllByText("6").length).toBeGreaterThan(0);
    expect(within(cov as HTMLElement).getByText("8")).toBeInTheDocument();
  });

  it("opens the figure modal on click and resolves the cloud-growth conflict to 29%", () => {
    // The conflicted figure renders its current text "31%".
    const fig = screen.getByText("31%");
    fireEvent.click(fig);
    // Modal shows the conflict reason + the corrective action.
    const apply = screen.getByText(/Apply corrected 29%/i);
    fireEvent.click(apply);
    // After resolving, the figure reads 29% and the toast confirms.
    expect(screen.getByText("29%")).toBeInTheDocument();
    expect(screen.getByText(/Corrected to 29%/i)).toBeInTheDocument();
  });

  it("in edit mode, clicking a figure lets you edit it instead of opening the modal", () => {
    // Enter edit mode; figure tokens become contentEditable.
    fireEvent.click(screen.getByText("Edit draft"));
    const fig = screen.getByText("31%");
    expect(fig).toHaveAttribute("contenteditable", "true");
    // Clicking the figure while editing must NOT pop the inspection modal —
    // otherwise the modal hijacks the click and inline editing is impossible.
    fireEvent.click(fig);
    expect(screen.queryByText(/Source as filed/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Apply corrected 29%/i)).not.toBeInTheDocument();
  });

  it("in edit mode the whole draft is editable, so you can type new text anywhere", () => {
    fireEvent.click(screen.getByText("Edit draft"));
    // The document body itself is contentEditable — not just the figure tokens —
    // so a user can rewrite the prose, not only change the highlighted numbers.
    const article = document.querySelector("article.doc");
    expect(article).toHaveAttribute("contenteditable", "true");
    // And the editing hint tells the user they can type anywhere.
    expect(screen.getByText(/type anywhere to rewrite the draft/i)).toBeInTheDocument();
  });

  it("navigates to consensus and requires two models before building", () => {
    fireEvent.click(screen.getByText("Street consensus"));
    expect(screen.getByText(/Drop sell-side models/i)).toBeInTheDocument();
    expect(screen.getByText(/at least two analyst models/i)).toBeInTheDocument();
    // Ingest two models by clicking the dropzone twice -> consensus table appears.
    const dz = screen.getByText(/Drop sell-side models/i).closest(".dropzone")!;
    fireEvent.click(dz);
    fireEvent.click(dz);
    expect(screen.getByText(/vs Street/i)).toBeInTheDocument();
  });

  it("navigates to the calendar and shows the runbook progress", () => {
    fireEvent.click(screen.getByText("Calendar & tasks"));
    expect(screen.getByText(/Earnings calendar/i)).toBeInTheDocument();
    expect(screen.getByText(/complete/i)).toBeInTheDocument();
  });

  it("on the script, surfaces the narrative summary bar", () => {
    fireEvent.click(screen.getByText("Prepared remarks"));
    expect(screen.getByText(/Narrative & language/i)).toBeInTheDocument();
    // the data-conflict narrative ("accelerating") is present in the script
    expect(screen.getByText("accelerating")).toBeInTheDocument();
  });
});
