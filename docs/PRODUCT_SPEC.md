# Product Specification — Attest

**Product:** Attest — the disclosure-verification spine for the quarterly earnings cycle
**Document owner:** Product Management
**Status:** Draft for review
**Last updated:** 2026-06-10
**Scope of this spec:** v1 (the "shippable wedge") plus the near-term roadmap it sets up

> **Scope note:** This document specifies the v1 deterministic verification spine — the
> fact store, the four-verdict engine, the rules engines, the audit chain, and the eval
> gate — and the roadmap as seen from that wedge. The codebase on `main` has continued
> past this point (a web workspace, persistent storage backends, LLM-assisted
> extraction, updated branding), so some items listed as roadmap in §10 already exist
> there in early form. Feature statuses below ("built") refer to the v1 spine.

---

## 1. TL;DR

Every quarter, public companies publish a set of documents — the earnings press release, the call
script, the Q&A prep, the investor deck — that contain hundreds of financial figures. Every one of
those figures must agree with a filed source, with prior guidance, and with every other document in
the pack. Today that assurance is produced by hand: a "tick and tie" exercise across PDFs and
spreadsheets, performed under deadline pressure by some of the most expensive people in the
company. A single missed number is a market-moving, litigable event.

**Attest is the verification layer for that workflow.** It ingests a company's filed financials
(EDGAR/XBRL) and published guidance into a provenance-typed fact store, then checks every figure in
a draft document against it — deterministically, with no model in the verification path. Each
figure gets one of four verdicts (`traced`, `needs_review`, `conflict`, `untraced`), each document
gets a set of compliance findings (Reg G, safe harbor, internal consistency), and every action —
ingest, verdict, override, sign-off — lands in an append-only, hash-chained audit log.

The deliberate bet: AI drafting tools are becoming commodities, and none of them can be trusted to
*verify*. Attest is the boring, deterministic core beneath the AI layer — the part a chat window
structurally cannot replicate. The LLM is a replaceable component at the edge; the spine is the
product.

---

## 2. Background and problem

### 2.1 The job to be done

Each earnings cycle, an issuer's IR and controllership teams must answer, for every figure in every
external-facing document: *"Where did this number come from, is it still correct, and can we prove
both?"* The work breaks down into:

1. **Tie-out** — match each figure in the draft to a filed source (10-Q/10-K, prior release) within
   the company's rounding conventions.
2. **Recomputation** — check derived figures: YoY/QoQ growth percentages, basis-point margin
   deltas, segment sums, ratios, range midpoints.
3. **Compliance review** — Reg G (every non-GAAP measure needs a reconciled GAAP counterpart with
   equal or greater prominence), safe-harbor language wherever forward-looking statements appear.
4. **Cross-document consistency** — the script, release, and Q&A must not disagree with each other.
5. **Accountability** — someone signs off, and the company needs a record of who approved what.

### 2.2 Why this is painful today

- **It is manual and late.** Tie-outs are done against PDFs with highlighters and spreadsheet
  checklists, in the final 72 hours before the release, exactly when drafts are churning fastest.
  Every editorial change reopens the work.
- **The failure cost is asymmetric and severe.** A wrong figure in a release can mean an 8-K
  correction, SEC comment letters, shareholder litigation, and personal reputational damage for the
  CFO's office. A missed Reg G or safe-harbor issue is a compliance event in itself.
- **Restatements are a silent trap.** When a prior-period base is restated, every growth rate
  computed off the old base becomes wrong while still "matching" the old filing. Humans rarely
  catch this; it requires version-aware source data. (This is the demo's signature catch: a
  release claims 31% cloud growth, but the prior-year base was restated from $467.0M to $474.3M,
  so the correct figure is 29%.)
- **Generative AI makes the problem worse before it makes it better.** Drafting tools accelerate
  the production of figure-dense prose, but a probabilistic model cannot be the thing that asserts
  a number is correct: it hallucinates, it cannot show provenance, and its output is not auditable.
  Teams that adopt AI drafting *increase* their need for deterministic verification.

### 2.3 Why now, and why us

- XBRL has made filed financials machine-readable; EDGAR makes them enumerable (every earnings
  release is an 8-K Item 2.02 with the release attached verbatim as Exhibit 99.1). The
  authoritative source data finally has clean rails.
- The AI wave is pulling budget into the earnings workflow, but buyers (CFO office, counsel) will
  not accept "the model said so." The wedge is the trust layer, not the drafting layer.
- Defensibility: the moat is not the model (deliberately replaceable) but the fact store with
  provenance and restatement history, the deterministic rules engines, the tenant-specific
  vocabulary, the audit chain, and the eval discipline that lets us *prove* the safety claim.

---

## 3. Product principles

These are encoded in the architecture and should govern every roadmap decision:

1. **Deterministic core, probabilistic edge.** A model may *propose* ("this span asserts
   `total_revenue` for FY2026-Q1"); only the deterministic engine may *dispose*, and only an
   exact match within the tenant's rounding policy may ever be called `traced`.
2. **Provenance is a first-class data type.** Every fact carries its source type, a
   machine-resolvable reference (accession + XBRL tag, doc + cell), a human-readable label, an
   excerpt, an as-of date, and a confidence. No bound source, no trace — ever.
3. **Human-in-the-loop with captured accountability.** Attest never silently changes a
   disclosure. Humans resolve what the engine flags; overrides require justification; sign-offs
   are attestations. All of it is immutable, attributable, and tamper-evident.
4. **The fact store is the spine.** New capabilities (rules, consistency checks, exports) are new
   *consumers* of the fact record, not new silos. Restatements are new fact versions, never
   mutations.
5. **Eval is non-negotiable infrastructure.** The metric asymmetry is explicit: a wrong number we
   call `traced` (a figure false negative) is catastrophic and the CI gate holds its rate at
   zero; over-flagging is a tunable annoyance, never a safety issue.

---

## 4. Target users and personas

| Persona | Role in the cycle | What Attest gives them |
| --- | --- | --- |
| **Dana — VP/Director of Investor Relations** (primary user) | Owns the release, script, and Q&A; accountable for every number in them | Upload a draft, see every figure highlighted by verdict in place, fix conflicts before anyone else sees them |
| **Marcus — Corporate Controller / SEC Reporting** (primary approver) | Owns the tie-out and the attestation | A tie-out that is already done, with provenance pointers to re-check; a sign-off action that is recorded |
| **Priya — Securities counsel / disclosure committee** | Reg G and safe-harbor review | Deterministic findings with rule ids and severities instead of a manual read-through |
| **CFO** (economic buyer, occasional user) | Signs the quarter; carries the risk | The publishable/blocked gate and the exportable, hash-verifiable audit trail |
| **FP&A analyst** (secondary) | Owns guidance figures | Guidance ingested as citable facts with the exact published sentence attached |

Initial market: US-listed mid-cap issuers with lean IR teams (where the pain is acute and the
buying motion is short), plus IR agencies and outside counsel who run this process for many
issuers (a natural multi-tenant fit).

---

## 5. Goals and non-goals

### 5.1 Goals (v1)

1. **Safety:** zero missed wrong figures on the golden set — `figure_false_negative_rate == 0`,
   enforced as a CI gate. This is the product's load-bearing claim.
2. **Coverage:** verify a real, uploaded draft end-to-end (`.txt`, `.md`, `.html`, `.docx`,
   `.pdf`, `.rtf`, or pasted text) with no hand-built claim objects required.
3. **Completeness of the check:** figure tie-out **plus** the deterministic rule families — Reg G,
   derived recomputation, directional language, units, ranges, forward-looking/safe-harbor, and
   intra-/cross-document consistency.
4. **Accountability:** every state change is an audit event in a sha256 hash chain that can be
   re-derived on demand (`GET /audit/verify`); overrides and sign-offs are first-class verbs.
5. **Self-serve source data:** a customer can seed real source data without us — XBRL ingest,
   guidance extraction from 8-K EX-99.1 prose, and an EDGAR fetcher that enumerates the last N
   quarters of press releases by construction (not by search).

### 5.2 Non-goals (v1 — deliberate, per the build sequence)

- **Drafting or rewriting documents.** Attest verifies; it does not write. (No generative
  features in v1 at all.)
- **The narrative/historical-consistency LLM service** (tone shifts, hedging-language drift) —
  the harness already anticipates it (`should_not_flag` majority scoring), but it ships later.
- **Consensus/estimates parsing** (`analyst_model` is in the data model; no connector yet).
- **ERP / close-package connectors** (the `internal_close` source type exists; ingestion later).
- **Editor add-ins** (Word/Google Docs). v1 surfaces are the web upload UI, API, and CLI.
- **Production persistence, authn/authz, and multi-user workflow.** Every store is an in-memory
  reference implementation behind a `Protocol`; swapping in Postgres/Redis/object storage is a
  constructor change, not an API change. v1 is single-process and unauthenticated by design.
- **XBRL tagging services or legal advice.** Attest is an assistive control, not counsel.

---

## 6. User journey (the quarterly cycle)

1. **Quarter setup (T-3 weeks).** Marcus's team ingests the latest filed XBRL
   (`POST /tenants/{t}/ingest/xbrl`) and last quarter's published guidance from the 8-K EX-99.1
   prose (`POST /tenants/{t}/ingest/guidance`). Optionally, `attest releases TICKER --quarters 4`
   pulls the prior releases straight from EDGAR for cross-document context. Dana configures the
   house vocabulary once ("topline" → `total_revenue`, segment names, non-GAAP labels) via
   `PUT /tenants/{t}/extraction/aliases` or the UI's *Custom terms* field.
2. **Draft verification (T-2 weeks → T-0, repeatedly).** Dana drops each draft into the upload UI
   or `POST /analyze`. Every detected figure is highlighted in place by verdict; Reg G,
   safe-harbor, derived-math, and consistency findings are listed beneath with severities.
3. **Resolution loop.** `conflict` and `untraced` figures **block publish** and must be fixed or
   formally overridden with a justification (`POST /override` — an audit event attributed to the
   person). `needs_review` figures (anything bound to a non-filed source, e.g. forward guidance)
   route to sign-off rather than silently passing.
4. **Close-pack check (T-1 day).** The release, script, and Q&A are verified together
   (`POST /verify-close-pack`), adding cross-document consistency: the same metric/period must not
   appear with different values in two documents.
5. **Attestation and archive (T-0 and after).** Marcus and Dana record sign-offs per document
   (`POST /documents/{id}/sign-off`). The audit trail — every ingest, verdict, override, and
   sign-off, hash-chained in order — is exportable (`GET /audit`) and independently verifiable
   (`GET /audit/verify`) for internal audit, external auditors, or counsel.

**Demo as the first-run experience:** `attest demo` (CLI) or *Load Meridian demo filing* (UI)
reproduces a complete cycle on the bundled Meridian Systems case — the release ties out, the 31%
cloud-growth figure is caught as a restatement conflict (correct: 29%), guidance routes to
sign-off, the script trips Reg G equal prominence, and the release trips the safe-harbor rule.
This is the product's pitch compressed into one command, and it must stay green forever.

---

## 7. Functional requirements

Priorities: **P0** = must work for v1 to be credible (all P0s below are built); **P1** = fast
follow; **P2** = roadmap.

### FR-1. Fact store with provenance and restatements — P0 (built)

- Facts are immutable records scoped to `(tenant, entity, metric, period)` with value, unit,
  precision (`quantum`), source type, source reference/label/excerpt, as-of date, and confidence.
- Source types encode "how filed" a source is: `edgar_xbrl` and `filing_line` are traceable;
  `internal_close`, `analyst_model`, `management_input` are not (they yield `needs_review` at
  best).
- A restatement is a **new version** with a later `as_of`; all versions are retained so
  cross-filing restatement conflicts are detectable.
- Strict multi-tenant scoping: one tenant's facts, aliases, and audit events never leak into
  another's.

### FR-2. Source ingestion — P0 (built)

- **XBRL connector:** maps `us-gaap`/extension tags to canonical metrics via the registry;
  reports skipped tags rather than guessing.
- **Guidance connector (8-K EX-99.1 prose):** the one figure class XBRL never carries. Attributes
  metric by keyword + unit, parses with the same quantity parser the spine uses (a range collapses
  to its midpoint; the excerpt preserves the range verbatim), infers the target period, and emits
  a fact **only when it can both attribute and parse** — everything else is reported as skipped,
  never guessed. Each guidance fact carries the exact published sentence (`source_excerpt`) and a
  pointer to the filed exhibit (`source_ref`).
- **EDGAR release fetcher:** enumerates 8-K Item 2.02 / EX-99.1 filings from the
  `data.sec.gov` submissions index (paging for longer lookbacks), honors the SEC fair-access
  policy (required user-agent), prints a **figure count per fetched release** so an empty
  advisory/shell page is visible at fetch time, and lists anything unsatisfied as `MISSING` with
  a reason. Search engines are deliberately not the primary path; an optional constrained Exa mode
  exists for teams that want it, with full-text verification and advisory-title rejection.
- Every ingestion writes an attributable `ingest` audit event.

### FR-3. Figure verification engine — P0 (built)

- Pipeline per claim: normalize the figure as written → resolve fact versions
  (restatement-aware) → bind against the latest filed value → verdict + provenance.
- **Verdicts (mutually exclusive):**

  | Verdict | Meaning | Blocking? |
  | --- | --- | --- |
  | `traced` | exact match to a **filed** source within the tenant's rounding policy | no |
  | `needs_review` | bound to a non-filed source (e.g. guidance) — needs sign-off | no, but routed |
  | `conflict` | bound, but the value differs — including cross-filing restatements | **yes** |
  | `untraced` | no source could be bound | **yes** |

- **Tie-out math:** precision-based ("round the source to the draft's quantum") with an optional
  tenant-configurable relative tolerance that defaults to zero — strict unless a tenant's policy
  opts in.
- A document is **publishable** only when no figure verdict and no rule finding blocks.
- Every verdict writes a `verdict` audit event with the claim, disposition, and source reference.

### FR-4. Deterministic rules engines — P0 (built)

All model-free. Severities: `block` (cannot publish), `warn` (needs attention), `info`.

| Family | Rules | What it catches |
| --- | --- | --- |
| Reg G | `reconciliation_required`, `reconciliation_arithmetic`, `equal_prominence`, `equal_prominence_ordering` | a non-GAAP measure without its GAAP counterpart; a reconciliation that doesn't add up; GAAP figure missing or appearing after the non-GAAP one |
| Derived math | `recomputation_mismatch`, `sum_mismatch`, `ratio_mismatch` | YoY/QoQ growth and bps deltas that don't recompute from the store (incl. off a restated base); segments that don't sum; ratio/percent identities that fail |
| Consistency | `intra_document_mismatch`, `cross_document_mismatch` | the same metric/period shown with two values within a document or across the close pack |
| Ranges | `inverted_range`, `midpoint_mismatch` | guidance low > high; a stated midpoint that isn't the midpoint |
| Directional | `sign_mismatch` | prose says "increased" while the change is negative (and vice versa) |
| Units | `unit_mismatch` | claim unit vs. the metric's declared unit (%, bps, currency) |
| Forward-looking | `safe_harbor_required` | FLS detected with no safe-harbor language in the document |

### FR-5. Extraction edge (claim proposal) — P0 reference implementation (built)

- Deterministic, model-free reference implementation of the probabilistic edge: greedy figure
  detection (**over-detect, never under-detect**), keyword/alias metric attribution,
  segment-entity resolution grounded in the tenant's own ingested facts, light period inference.
- Anything it cannot confidently attribute is labeled low-confidence so the core routes it to a
  human instead of asserting it; a guessed binding can never become `traced`.
- **Tenant-configurable vocabulary:** per-tenant alias config layered over a default
  (`replace: false` unions, `true` overwrites); unknown metric ids are rejected so a typo cannot
  create a phantom metric.
- **Swappability is a requirement, not an aspiration:** replacing this module with an LLM changes
  nothing downstream.

### FR-6. Audit log — P0 (built)

- Append-only, event-sourced, sha256 **hash-chained**: each event's hash covers its content and
  the previous hash, so any retroactive edit breaks every subsequent link.
- Closed event vocabulary: `ingest`, `bind`, `verdict`, `edit`, `override`, `sign_off`. Every
  state change in the product must map to one of these; exports are projections.
- `audit_verify()` re-derives the chain end-to-end and is exposed via API and tested.

### FR-7. Eval harness and CI gate — P0 (built)

- Labeled golden set (filing fixture + expected verdict per case) scored on exact verdict
  accuracy and on the binary "flag" decision (positive = anything not `traced`).
- Reported: exact accuracy, flag precision, flag recall, **figure_false_negative_rate** — the
  last gated to **zero** in CI (`tests/test_eval.py`). A change that makes the engine miss a
  wrong number cannot merge.
- The golden set is a growing asset: every real-world miss or false alarm becomes a case.

### FR-8. Surfaces — P0 (built)

- **Web UI** (`attest serve` → `/`): drag-and-drop or paste a draft, seed the demo filing, set
  custom terms, analyze; figures highlighted in place by verdict with findings beneath.
- **API** (FastAPI, OpenAPI docs at `/docs`): ingest (XBRL/guidance/demo), facts listing, alias
  get/put, analyze (multipart file or text + optional title/kind/entity/period), verify,
  verify-close-pack, sign-off, override, audit export, audit chain verification.
- **CLI:** `attest demo`, `attest serve`, `attest releases TICKER --quarters N`.

### FR-9 → roadmap (P1/P2): see §10.

---

## 8. Success metrics

| Dimension | Metric | Target |
| --- | --- | --- |
| **Safety (north star)** | Figure false-negative rate on the golden set (a wrong number called `traced`) | **0**, CI-gated; also 0 in any customer-reported incident |
| Signal quality | Flag precision on the golden set and in-product (share of flags a human confirms as real) | ≥ 0.8 at GA; track per rule family to find noisy rules |
| Coverage | Share of figures in a customer draft that bind to *some* source (not `untraced`) after setup | ≥ 90% on a well-ingested tenant by the second cycle |
| Efficiency | Time from draft upload to resolved verdict list, vs. the manual tie-out baseline | hours → minutes; measure via repeated-analyze cadence per document |
| Adoption | Documents analyzed per tenant per quarter; share of close packs with all three artifacts verified | full close pack by a tenant's second quarter |
| Accountability | Documents published with recorded sign-off; audit chain verification pass rate | 100% |
| Trust (qualitative) | Controller/counsel willingness to *reduce* their manual re-check | survey per design partner, per quarter |

Guardrail: precision improvements may never come at the cost of the false-negative gate — the
asymmetry is the product.

---

## 9. Technical overview (PM summary)

Python 3.11 / FastAPI / Pydantic v2; package layout mirrors the principle "the fact store is the
spine": `domain` (provenance-typed value objects, metric registry incl. Reg G relationships,
deterministic money/rounding math) → `factstore` → `verification` (engine + rules) → `audit` →
`ingestion` (EDGAR/XBRL, guidance, releases, fixtures) → `eval` → `api`/`cli`, composed by a
single `AttestService` shared by every surface.

Two seams matter for planning:

1. **Persistence.** Every store is in-memory behind a `Protocol`; production datastores are a
   constructor swap. This is the main thing standing between v1 and a hosted pilot.
2. **The edge.** Claim extraction is an isolated module with a fixed contract (`FigureClaim` in,
   verdicts out of the core). The LLM upgrade is contained by design.

---

## 10. Release plan

- **v1 — the wedge (this repo, done):** everything in §7. Positioning: a verification harness a
  design partner's IR team can run against real drafts during a live quarter, locally.
- **v1.x — pilot hardening (next):** durable persistence behind the existing protocols; authn/
  authz and real actor identities (the audit log already attributes every event — it needs real
  user ids); deployment story; alias/rounding policy administration UI; audit export formats
  auditors expect.
- **v2 — the probabilistic edge, for real:** LLM-backed claim extraction behind the same
  contract (expected lift: fewer low-confidence/`untraced` figures, same safety floor, because
  the core still disposes); the narrative/historical-consistency service scored with the
  `should_not_flag` asymmetry the harness already anticipates; consensus ingestion
  (`analyst_model`); close-package connector (`internal_close`); editor add-ins where drafts
  actually live.

Sequencing rationale: trust compounds inside-out. Each layer ships only when the layer beneath it
can prove — via the eval gate and the audit chain — that it deserves the claim.

---

## 11. Risks and mitigations

| Risk | Severity | Mitigation |
| --- | --- | --- |
| **One bad `traced` verdict destroys the product's reason to exist** | Existential | Determinism in the core; only filed sources can trace; precision-based tie-out with zero default tolerance; FN-rate CI gate; provenance shown for every trace so humans can spot-check cheaply |
| Extraction edge misses a figure entirely (never enters verification) | High | Greedy over-detection bias; per-release figure counts at fetch time; eval set grows with every real-world miss; LLM edge in v2 raises recall without touching the safety floor |
| Over-flagging → alert fatigue → users stop reading findings | High | Tenant alias vocabulary; confidence routing instead of hard flags; flag-precision tracked per rule family; severity tiers (`block`/`warn`/`info`) |
| Issuer vocabulary drift ("topline", house segment names) breaks attribution | Medium | Per-tenant alias config layered over defaults, exposed in UI and API; unknown-metric rejection prevents silent misconfiguration |
| EDGAR fair-access limits or format drift in filings | Medium | Required user-agent, paging, explicit `MISSING`-with-reason reporting; fixtures + connector tests pin the parsing contract |
| In-memory v1 stores lose state between runs | Medium (known) | Documented as a v1 boundary; protocol seams make persistence the first v1.x deliverable |
| Compliance positioning risk (seen as legal advice) | Medium | Position as an assistive control with deterministic, explainable rule ids; counsel remains the decision-maker; overrides + sign-offs keep humans accountable |
| Security expectations of an audit-trail product (SOC 2, tenant isolation) | Medium | Hash-chain verifiability is built; isolation enforced at the store level; formal security/compliance program scoped at v1.x |

---

## 12. Open questions

1. **Identity:** what is the v1.x authn/authz model, and how do real user identities flow into
   audit `actor` fields (SSO? per-seat?)?
2. **Rounding policy surface:** tenants will want per-metric tolerances (EPS vs. revenue). How
   much configurability before the "strict by default" story erodes?
3. **Metric registry administration:** the default registry covers the demo close pack; real
   tenants need a managed onboarding path for their full metric map (service-led vs. self-serve?).
4. **Packaging and pricing:** per issuer-quarter (matches the job's cadence) vs. per seat vs.
   agency/multi-issuer tiers — design-partner conversations needed.
5. **Eval set governance:** who labels new golden cases from customer data, and what is the
   privacy posture for using tenant drafts as eval material?
6. **Where does the LLM land first in v2** — extraction recall (more figures bound) or the
   narrative service (new check class)? Driven by design-partner pain ranking.

---

## Appendix A — Glossary

- **8-K Item 2.02 / Exhibit 99.1:** the SEC filing that announces quarterly results; the press
  release is attached verbatim as EX-99.1. The authoritative, enumerable source of releases.
- **XBRL:** machine-readable tagging of filed financials; the "gold standard" source type.
- **Reg G:** SEC regulation requiring any public non-GAAP measure to be reconciled to its most
  directly comparable GAAP measure, presented with equal or greater prominence.
- **FLS / safe harbor:** forward-looking statements require cautionary safe-harbor language to
  qualify for PSLRA protection.
- **Restatement:** a revision to previously filed figures; in Attest, a new fact version for the
  same scope with a later as-of date.
- **Close pack:** the set of documents verified together for one earnings cycle (release, call
  script, Q&A).
- **Tie-out:** matching a published figure to its authoritative source within rounding policy.
