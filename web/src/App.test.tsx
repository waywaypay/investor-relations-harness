import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { App } from "./App";

describe("Attest workspace", () => {
  // These exercise the document-rendering features (coverage, figure modal,
  // inline edit, narrative bar) against the bundled sample close pack, so they
  // opt into seeding it. The app itself now loads empty — see the "empty
  // workspace" suite below.
  beforeEach(() => render(<App seedDemo />));

  it("renders the brand and the release as the default document", () => {
    expect(screen.getByText("Attest")).toBeInTheDocument();
    expect(
      screen.getByText(/Atlas Systems Reports First Quarter Fiscal 2026 Results/i)
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
    // Categories are collapsed by default — expand "Call scripts" to reveal the doc.
    fireEvent.click(screen.getByRole("button", { name: /Expand Transcripts/i }));
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
    // Pasting (no file) shows no title field — title/ticker are reserved for the
    // file-upload path — so the draft files under the default name.
    fireEvent.change(screen.getByPlaceholderText(/Paste your draft/i), {
      target: { value: "Revenue this quarter was $1.5 billion, up 12% year over year." },
    });
    fireEvent.click(screen.getByText(/Analyze & add/i));

    // The new document renders (heading), the figure is detected and shown, and an
    // honest warning notes there's no backend to tie it out against.
    expect(await screen.findByRole("heading", { name: "Uploaded document" })).toBeInTheDocument();
    expect(screen.getByText("$1.5 billion")).toBeInTheDocument();
    expect(screen.getByText(/No verification backend connected/i)).toBeInTheDocument();
  });

  it("reveals title and ticker only after a file is chosen, seeding the title from the filename", async () => {
    fireEvent.click(screen.getByRole("button", { name: /Add document/i }));
    // Before a file is chosen the metadata fields are hidden — the modal opens to
    // just the document-type picker, the dropzone, and the paste box.
    expect(screen.queryByPlaceholderText(/e\.g\./i)).not.toBeInTheDocument();

    // Choosing a file reveals them and seeds the title from the filename. (The
    // ticker auto-detect reads file.text(), which jsdom's File lacks; the
    // detection itself is covered in src/lib/ticker.test.ts.)
    const file = new File(["Revenue was $1.5 billion this quarter."], "q2-release.txt", {
      type: "text/plain",
    });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByDisplayValue("q2-release")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/ties the draft out to filed sources/i)).toBeInTheDocument();
  });

  it("lets you remove an uploaded document from the workspace", async () => {
    fireEvent.click(screen.getByRole("button", { name: /Add document/i }));
    fireEvent.change(screen.getByPlaceholderText(/Paste your draft/i), {
      target: { value: "Operating cash flow was $42 million for the period." },
    });
    fireEvent.click(screen.getByText(/Analyze & add/i));
    await screen.findByText("$42 million");

    // Delete it from the documents manager (rename/delete/version actions live
    // there now, keeping the sidebar uncluttered). The manager opens in the main
    // stage from the document's "History" control, not a modal.
    fireEvent.click(screen.getByRole("button", { name: /History/i }));
    const card = (screen.getByDisplayValue("Uploaded document").closest(".dmrow")) as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: /Delete document/i }));
    expect(screen.queryByDisplayValue("Uploaded document")).not.toBeInTheDocument();
  });

  it("files a new version of a document and lets you switch back to the prior one", async () => {
    // Upload an initial document (pasted, so it files under the default name).
    fireEvent.click(screen.getByRole("button", { name: /Add document/i }));
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

  it("opens the documents manager from a document's History and lets you rename it", async () => {
    fireEvent.click(screen.getByRole("button", { name: /Add document/i }));
    fireEvent.change(screen.getByPlaceholderText(/Paste your draft/i), {
      target: { value: "Revenue was $2.0 billion this quarter." },
    });
    fireEvent.click(screen.getByText(/Analyze & add/i));
    await screen.findByText("$2.0 billion");

    // Open the manager from the document's History control and rename the upload.
    fireEvent.click(screen.getByRole("button", { name: /History/i }));
    expect(screen.getByText("Manage documents")).toBeInTheDocument();
    const rename = screen.getByLabelText(/Rename Uploaded document/i) as HTMLInputElement;
    fireEvent.change(rename, { target: { value: "Q1 release (renamed)" } });
    fireEvent.blur(rename);
    expect(screen.getAllByDisplayValue("Q1 release (renamed)").length).toBeGreaterThan(0);
  });

  it("opens the manager scoped to a category to upload past transcripts", () => {
    // Clicking a document category in the sidebar opens the manager focused on
    // that type, where past filings/transcripts for the company can be uploaded.
    fireEvent.click(screen.getByRole("button", { name: /Manage Press releases/i }));
    expect(screen.getByText(/Past filings, transcripts/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Upload past transcript/i })).toBeInTheDocument();
  });
});

describe("empty workspace (no demo seed)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    render(<App />);
  });

  it("loads with no demo documents — no Atlas close pack, just a prompt to add one", () => {
    // Regression: the bundled Atlas sample close pack must not be seeded on load,
    // so a refresh never resurrects fake documents.
    expect(
      screen.queryByText(/Atlas Systems Reports First Quarter Fiscal 2026 Results/i)
    ).not.toBeInTheDocument();
    // The stage instead invites the user to add their own document.
    expect(screen.getByText(/No documents yet/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add a document/i })).toBeInTheDocument();
  });

  it("shows the press-releases category as empty in the sidebar", () => {
    // Every document category starts at a zero count until the user uploads.
    const press = screen.getByRole("button", { name: /Manage Press releases/i });
    expect(press.textContent).toMatch(/0\s*$/);
  });
});
