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

describe("document library & upload", () => {
  beforeEach(() => {
    window.localStorage.clear();
    render(<App />);
  });

  it("offers an upload button and opens the upload modal", () => {
    fireEvent.click(screen.getByRole("button", { name: /Add document/i }));
    expect(screen.getByText("Add a document")).toBeInTheDocument();
    expect(screen.getByText(/Analyze & add/i)).toBeInTheDocument();
  });

  it("uploads a pasted draft and renders it with detected figures", async () => {
    fireEvent.click(screen.getByRole("button", { name: /Add document/i }));
    fireEvent.change(screen.getByPlaceholderText(/e\.g\./i), {
      target: { value: "My Q2 script" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Paste your draft/i), {
      target: { value: "Revenue this quarter was $1.5 billion, up 12% year over year." },
    });
    fireEvent.click(screen.getByText(/Analyze & add/i));

    // The new document renders (heading), the figure is detected and shown, and an
    // honest warning notes there's no backend to tie it out against.
    expect(await screen.findByRole("heading", { name: "My Q2 script" })).toBeInTheDocument();
    expect(screen.getByText("$1.5 billion")).toBeInTheDocument();
    expect(screen.getByText(/No verification backend connected/i)).toBeInTheDocument();
  });

  it("lets you remove an uploaded document from the workspace", async () => {
    fireEvent.click(screen.getByRole("button", { name: /Add document/i }));
    fireEvent.change(screen.getByPlaceholderText(/Paste your draft/i), {
      target: { value: "Operating cash flow was $42 million for the period." },
    });
    fireEvent.click(screen.getByText(/Analyze & add/i));
    await screen.findByText("$42 million");

    // Delete it from the documents manager (rename/delete/version actions live
    // there now, keeping the sidebar uncluttered).
    fireEvent.click(screen.getByRole("button", { name: /Manage all/i }));
    const card = (screen.getByDisplayValue("Uploaded document").closest(".dmcard")) as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: /Delete document/i }));
    expect(screen.queryByText("$42 million")).not.toBeInTheDocument();
  });

  it("files a new version of a document and lets you switch back to the prior one", async () => {
    // Upload an initial document.
    fireEvent.click(screen.getByRole("button", { name: /Add document/i }));
    fireEvent.change(screen.getByPlaceholderText(/e\.g\./i), { target: { value: "My draft" } });
    fireEvent.change(screen.getByPlaceholderText(/Paste your draft/i), {
      target: { value: "Revenue was $1.0 billion this quarter." },
    });
    fireEvent.click(screen.getByText(/Analyze & add/i));
    await screen.findByText("$1.0 billion");

    // The version bar shows the active version; file a new version of this doc.
    fireEvent.click(screen.getByRole("button", { name: /Upload new version/i }));
    expect(await screen.findByText(/New version of/i)).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/Paste your draft/i), {
      target: { value: "Revenue was $1.2 billion this quarter." },
    });
    fireEvent.click(screen.getByText(/Analyze & file version/i));

    // The new version renders; a version selector now offers both.
    expect(await screen.findByText("$1.2 billion")).toBeInTheDocument();
    const select = screen.getByLabelText("Active version") as HTMLSelectElement;
    expect(select.options.length).toBe(2);

    // Switch back to Version 1 — the earlier figure returns.
    fireEvent.change(select, { target: { value: select.options[1].value } });
    expect(await screen.findByText("$1.0 billion")).toBeInTheDocument();
  });

  it("opens the documents manager from the 'Manage all' link and lets you rename a document", () => {
    fireEvent.click(screen.getByRole("button", { name: /Manage all/i }));
    expect(screen.getByText("Manage documents")).toBeInTheDocument();
    // The bundled samples are listed and can be renamed.
    const rename = screen.getByLabelText(/Rename Earnings release/i) as HTMLInputElement;
    fireEvent.change(rename, { target: { value: "Q1 release (renamed)" } });
    fireEvent.blur(rename);
    expect(screen.getAllByDisplayValue("Q1 release (renamed)").length).toBeGreaterThan(0);
  });

  it("opens the manager scoped to a category to upload past transcripts", () => {
    // Clicking a document category in the sidebar opens the manager focused on
    // that type, where past filings/transcripts for the company can be uploaded.
    fireEvent.click(screen.getByRole("button", { name: /Earnings releases/i }));
    expect(screen.getByText(/Past filings, transcripts/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Upload past transcript/i })).toBeInTheDocument();
  });
});
