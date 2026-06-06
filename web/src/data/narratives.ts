import type { Commitment, Narrative } from "../types";

// Narrative & wording checks for the prepared-remarks script, ported verbatim.
export const NARRATIVES: Record<string, Narrative> = {
  strong: {
    id: "strong",
    phrase: "strong start",
    cur: "strong start",
    kind: "onmessage",
    st: "ok",
    tag: "msg",
    title: "On-message",
    against: "Approved messaging",
    compare:
      "Matches your approved opening framing and is consistent with the release’s characterization of the quarter.",
    detail: "No change needed.",
    suggestion: null,
  },
  cloudword: {
    id: "cloudword",
    phrase: "cloud business",
    cur: "cloud business",
    kind: "wording",
    st: "warn",
    tag: "word",
    title: "Wording drift from approved term",
    against: "Approved term · used in the release",
    compare:
      "The release and your approved messaging library use <mark>“cloud platform”</mark>. The script says “cloud business” — a variant analysts parsing the transcript may read as a different segment.",
    detail:
      "Keep one canonical term across every document so machine summaries and analysts map it to the same segment.",
    suggestion: "cloud platform",
    history: {
      worst: "drift",
      verdict:
        "You’ve used “cloud platform” in every disclosure for six straight quarters. “cloud business” is a single-quarter deviation — keep the canonical term.",
      rows: [
        { p: "Q3 FY25 release", q: "“cloud platform”", m: "", s: "consistent" },
        { p: "Q4 FY25 release", q: "“cloud platform”", m: "", s: "consistent" },
        { p: "Q1 FY26 release", q: "“cloud platform”", m: "", s: "consistent" },
        { p: "Q1 FY26 script", q: "“cloud business”", m: "", s: "drift" },
      ],
    },
  },
  accel: {
    id: "accel",
    phrase: "accelerating",
    cur: "accelerating",
    kind: "narrative",
    st: "conflict",
    tag: "conflict",
    title: "Narrative conflicts with the data",
    against: "Cloud growth trend",
    compare:
      "Cloud growth is <mark>decelerating</mark> — 30% → 30% → 29% over the last three quarters. Describing it as “accelerating” contradicts the figure you just cited in the same sentence.",
    detail:
      "A characterization the numbers don’t support is exactly what gets caught on the call and in transcript analysis afterward.",
    suggestion: "continuing to grow",
    history: {
      worst: "contradict",
      verdict:
        "You walked the language down — “accelerating” → “strong” → “solid” — as growth held around 30%, then returned to “accelerating” just as it slipped to 29%. That reversal contradicts your own three-quarter arc and reads as a tone change to the sell-side.",
      rows: [
        { p: "Q2 FY25 call", q: "“cloud growth accelerating”", m: "+30% YoY", s: "baseline" },
        { p: "Q3 FY25 call", q: "“cloud momentum remains strong”", m: "+30% YoY", s: "drift" },
        { p: "Q4 FY25 call", q: "“solid cloud performance”", m: "+30% YoY", s: "drift" },
        { p: "Q1 FY26", q: "“accelerating into the back half”", m: "+29% YoY", s: "contradict" },
      ],
    },
  },
  fls: {
    id: "fls",
    phrase: "we expect",
    cur: "we expect",
    kind: "forwardlooking",
    st: "warn",
    tag: "FLS",
    title: "Forward-looking statement",
    against: "Reg FD / safe-harbor",
    compare:
      "This introduces forward-looking guidance. <mark>Safe-harbor language is required</mark> before it can be delivered on the call.",
    detail:
      "Attest ties this to the same sign-off as the guidance figure — both must clear before the script can publish.",
    suggestion: null,
    applyLabel: "Attach safe-harbor language",
  },
};

export const COMMITMENTS: Commitment[] = [
  {
    id: "margin",
    period: "Q4 FY25 earnings call",
    status: "open",
    text: "“We expect operating margin to expand in the first half of fiscal 2026.”",
    detail:
      "This forward statement from last quarter’s call isn’t addressed anywhere in the current script, and H1 operating margin came in roughly flat. Analysts tracking the transcript will likely ask you to reconcile it — far better to address it proactively than to be caught flat-footed in Q&A.",
  },
];
