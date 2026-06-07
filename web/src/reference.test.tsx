import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";
import { App } from "./App";
import { client } from "./api/client";

// Drive the historical reference flow against a mocked client: search returns a
// mix of releases and transcripts; ingest reports them loaded. This verifies the
// two things the user cares about end to end —
//   1. the review list groups releases and transcripts (never interleaved), and
//   2. loaded entries file under the matching sidebar category by kind.
vi.mock("./api/client", () => ({
  apiBaseUrl: "",
  client: {
    searchHistorical: vi.fn(),
    ingestHistorical: vi.fn(),
  },
}));

const RELEASE = {
  url: "https://prnewswire.com/panw-q3-release",
  title: "PANW Earnings release · FY2026-Q3 (pub 2026-06-02)",
  published_date: "2026-06-02",
  source: "prnewswire.com",
  snippet: "Palo Alto Networks Reports Fiscal Third Quarter 2026 Financial Results",
  doc_type: "release",
  period: "FY2026-Q3",
};
const TRANSCRIPT = {
  url: "https://seekingalpha.com/panw-q3-transcript",
  title: "PANW Earnings call transcript · FY2026-Q3 (pub 2026-06-03)",
  published_date: "2026-06-03",
  source: "seekingalpha.com",
  snippet: "Palo Alto Networks, Inc. (PANW) Q3 2026 Earnings Call Transcript",
  doc_type: "transcript",
  period: "FY2026-Q3",
};

const mock = client as unknown as {
  searchHistorical: ReturnType<typeof vi.fn>;
  ingestHistorical: ReturnType<typeof vi.fn>;
};

// The sidebar `.sb-cat` block whose header manages the named category.
function category(name: RegExp): HTMLElement {
  const head = screen.getByRole("button", { name });
  return head.closest(".sb-cat") as HTMLElement;
}

describe("historical reference: grouping and category placement", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mock.searchHistorical.mockReset();
    mock.ingestHistorical.mockReset();
    // Backend returns newest-first across both types — interleaved on the wire.
    mock.searchHistorical.mockResolvedValue([TRANSCRIPT, RELEASE]);
    mock.ingestHistorical.mockResolvedValue({
      documents: [
        { url: RELEASE.url, title: "PANW Q3 release", published_date: "2026-06-02", ingested: 5, skipped: 0 },
        { url: TRANSCRIPT.url, title: "PANW Q3 transcript", published_date: "2026-06-03", ingested: 4, skipped: 0 },
      ],
      total_ingested: 9,
    });
    render(<App />);
  });

  it("groups the review list into Press releases and Transcripts (not interleaved)", async () => {
    fireEvent.click(screen.getByRole("button", { name: "Fetch historical" }));
    fireEvent.change(screen.getByPlaceholderText(/PANW or Palo Alto Networks/i), {
      target: { value: "PANW" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    // Both candidates render…
    await screen.findByText(RELEASE.title);
    const list = document.querySelector(".uphlist") as HTMLElement;
    const groups = list.querySelectorAll(".uphgroup");
    expect(groups.length).toBe(2); // one per type, never one flat interleaved list

    // …each under its own type group: the release title sits in the Press
    // releases group, the transcript title in the Transcripts group.
    const relGroup = within(list).getByText("Press releases").closest(".uphgroup") as HTMLElement;
    const scrGroup = within(list).getByText("Transcripts").closest(".uphgroup") as HTMLElement;
    expect(within(relGroup).getByText(RELEASE.title)).toBeInTheDocument();
    expect(within(relGroup).queryByText(TRANSCRIPT.title)).not.toBeInTheDocument();
    expect(within(scrGroup).getByText(TRANSCRIPT.title)).toBeInTheDocument();
    expect(within(scrGroup).queryByText(RELEASE.title)).not.toBeInTheDocument();
  });

  it("files a loaded release under Press releases and a transcript under Transcripts", async () => {
    fireEvent.click(screen.getByRole("button", { name: "Fetch historical" }));
    fireEvent.change(screen.getByPlaceholderText(/PANW or Palo Alto Networks/i), {
      target: { value: "PANW" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByText(RELEASE.title);

    // Both are pre-selected; load them.
    fireEvent.click(screen.getByRole("button", { name: /Load 2 selected/i }));
    await waitFor(() => expect(mock.ingestHistorical).toHaveBeenCalledTimes(1));

    // The items handed to ingest carry the right doc_type, so the store can file
    // each entry under its category.
    const [, items] = mock.ingestHistorical.mock.calls[0];
    const byUrl = Object.fromEntries(items.map((i: { url: string; doc_type?: string }) => [i.url, i.doc_type]));
    expect(byUrl[RELEASE.url]).toBe("release");
    expect(byUrl[TRANSCRIPT.url]).toBe("transcript");

    // Reveal each category's contents and assert placement.
    fireEvent.click(screen.getByRole("button", { name: /Expand Press releases/i }));
    fireEvent.click(screen.getByRole("button", { name: /Expand Transcripts/i }));

    const press = category(/Manage Press releases/i);
    const scripts = category(/Manage Transcripts/i);
    expect(await within(press).findByText("PANW Q3 release")).toBeInTheDocument();
    expect(within(press).queryByText("PANW Q3 transcript")).not.toBeInTheDocument();
    expect(await within(scripts).findByText("PANW Q3 transcript")).toBeInTheDocument();
    expect(within(scripts).queryByText("PANW Q3 release")).not.toBeInTheDocument();
  });

  it("recovers the category of legacy entries saved without a kind", () => {
    // A pre-upgrade payload: entries persisted before reference docs carried a
    // category. The transcript entry must still land under Transcripts, not be
    // defaulted to Press releases.
    window.localStorage.setItem(
      "attest.refcorpus.v1",
      JSON.stringify([
        { id: "historical:https://x/r", entity: "PANW", label: "PANW Earnings release", count: 5, addedAt: "2026-06-02" },
        { id: "historical:https://x/t", entity: "PANW", label: "PANW Earnings call transcript", count: 4, addedAt: "2026-06-03" },
      ])
    );
    document.body.innerHTML = "";
    render(<App />);
    fireEvent.click(screen.getAllByRole("button", { name: /Expand Press releases/i })[0]);
    fireEvent.click(screen.getAllByRole("button", { name: /Expand Transcripts/i })[0]);

    const press = category(/Manage Press releases/i);
    const scripts = category(/Manage Transcripts/i);
    expect(within(press).getByText("PANW Earnings release")).toBeInTheDocument();
    expect(within(scripts).getByText("PANW Earnings call transcript")).toBeInTheDocument();
    expect(within(press).queryByText("PANW Earnings call transcript")).not.toBeInTheDocument();
  });

  it("persists loaded reference entries across a reload, still under the right category", async () => {
    fireEvent.click(screen.getByRole("button", { name: "Fetch historical" }));
    fireEvent.change(screen.getByPlaceholderText(/PANW or Palo Alto Networks/i), {
      target: { value: "PANW" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByText(RELEASE.title);
    fireEvent.click(screen.getByRole("button", { name: /Load 2 selected/i }));
    await waitFor(() => expect(mock.ingestHistorical).toHaveBeenCalledTimes(1));

    // Simulate a page refresh: tear down and re-render from persisted storage.
    const { unmount } = render(<App />);
    unmount();
    document.body.innerHTML = "";
    render(<App />);

    fireEvent.click(screen.getAllByRole("button", { name: /Expand Press releases/i })[0]);
    fireEvent.click(screen.getAllByRole("button", { name: /Expand Transcripts/i })[0]);
    expect(screen.getByText("PANW Q3 release")).toBeInTheDocument();
    expect(screen.getByText("PANW Q3 transcript")).toBeInTheDocument();
  });
});
