# Attest — disclosure-verification spine (v1)

> Deterministic tie-outs, provenance-typed facts, and a tamper-evident audit
> trail for the quarterly earnings cycle.

This repository is the **v1 "shippable wedge"** from the Attest production
architecture: the *boring core beneath the AI layer* that a chat window
structurally cannot replicate. It implements the inside-out build sequence —
fact store + EDGAR/XBRL ingestion + deterministic figure verification + an
append-only, hash-chained audit log — plus the deterministic Reg G / FLS /
cross-document rules and the eval harness that gates changes.

There is **no model in the loop here, by design.** The LLM's job (locating
candidate numbers, proposing what metric a span asserts) is the *probabilistic
edge*; everything in this package is the *deterministic core* that disposes. The
model is the replaceable component; this spine is the company.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

attest verify        # verify the reference close pack, print a report
attest verify --use-llm  # same close pack, claims proposed by the LLM edge (see "v2" below)
attest ingest-edgar PANW # pull a real issuer's filed facts from SEC EDGAR (see "live EDGAR" below)
pytest               # run the suite, including the eval regression gate
attest serve         # API + upload UI at http://127.0.0.1:8000  (API docs at /docs)
```

A React + TypeScript front-end (the disclosure-drafting workspace) lives in
[`web/`](web/README.md): upload your own release / script / Q&A, and every figure
is tied out against your filed sources, with the documents you've uploaded
toggled in the sidebar. `attest serve` builds and serves it at `/`; for the dev
loop see [`web/README.md`](web/README.md).

## What it does

Given a company's filing (ingested as machine-tagged XBRL) and a draft release /
script / Q&A, the engine renders one of four verdicts per figure:

| Verdict | Meaning |
| --- | --- |
| `traced` | exact match to a **filed** source, within the tenant's rounding policy |
| `needs_review` | bound to a non-filed source (e.g. forward guidance) — needs sign-off |
| `conflict` | bound to a source but the value differs, **including cross-filing restatements** |
| `untraced` | no source could be bound |

The bundled `attest verify` reproduces the reference close pack: the
release ties out, the `31%` cloud-growth figure is caught as a **restatement
conflict** (the prior-year base was restated `$467.0M → $474.3M`, so the correct
figure is `29%`), guidance is routed to human sign-off, the script trips Reg G
(non-GAAP EPS without an equal-prominence GAAP figure), and the release trips the
forward-looking-statement safe-harbor rule.

## Architecture map

The package is organised inside-out, mirroring the principle *"the fact store is
the spine"*:

```
src/attest/
  domain/          provenance-typed value objects
    money.py         Quantity + RoundingPolicy — the deterministic tie-out math
    facts.py         Fact (the spine record) + Provenance, SourceType
    metrics.py       canonical metric registry (incl. Reg G relationships)
    verdicts.py      FigureClaim (edge proposes) / FigureVerdict (core disposes)
    document.py      the unit submitted for verification
  factstore/       the normalized, restatement-aware store of facts-with-provenance
  verification/
    candidates.py    greedy figure detection (over-detect, never under-detect)
    engine.py        detect -> normalize -> bind -> verdict, with audit logging
    rules/           deterministic checks, all model-free:
                       reg_g          counterpart, reconciliation source + arithmetic,
                                      equal-prominence ordering
                       derived        recomputation — YoY/QoQ growth, bps delta, TTM sum,
                                      segment sum, ratio & percent-ratio identities
                       directional    prose direction word vs. the sign of the change
                       units          claim unit vs. metric's declared unit
                       consistency    cross-document and intra-document figure agreement
                       ranges         guidance low<=high and stated-midpoint consistency
                       forward_looking FLS detection -> safe-harbor requirement
  audit/           append-only, event-sourced, sha256 hash-chained log
  edge/            the replaceable LLM layer (v2): claim proposer + history narrator
  storage/         durable backends behind the store Protocols: Postgres + Redis cache
  ingestion/       connectors (XBRL-instance adapter, live SEC EDGAR companyfacts
                                connector, 8-K EX-99.1 guidance adapter, fixtures)
  eval/            the golden-set harness + CI gate (figure FN rate must be 0)
    perturbation.py  synthetic case generator — known mutations of real filed values
    synthetic_eval.py scores the engine on synthetic cases in a SEPARATE bucket
    sheets_bridge.py  loads the corpus workbook's 02_Facts CSV export into facts
  api/             stateless FastAPI surface over the service
  service.py       composition root shared by the API and CLI
  cli.py           `attest verify [--use-llm]` / `attest serve` / `attest synth`
```

### How the design-doc principles show up in code

- **Deterministic core, probabilistic edge.** `FigureClaim` is the edge's
  proposal; `VerificationEngine._bind` is the only thing that may say `traced`,
  and only on an exact-with-tolerance match (`Quantity.matches`).
- **Provenance is a first-class data type.** Every `Fact` carries
  `source_type`, `source_ref`, `as_of`, and `confidence`; a verdict without a
  bound source is never `traced`.
- **Human-in-the-loop with captured accountability.** Ingest, verdict, edit,
  override, and sign-off are immutable, attributable events in a hash-chained
  log. `audit_verify()` re-derives the chain; any retroactive edit breaks it.
- **The fact store is the spine.** Verification, Reg G, and consistency are all
  *consumers* of the store; restatements are new `Fact` versions, not mutations.
- **Eval is non-negotiable infrastructure.** `attest/eval` scores the engine
  against a labeled golden set with the right asymmetry (a missed wrong number is
  catastrophic); `tests/test_eval.py` is the CI gate.

## The golden corpus: synthetic vs. labeled (read before trusting a number)

Reliability is *measured*, not asserted — and the measurement is only honest if
two kinds of cases stay strictly separated:

- **Human-/EDGAR-labeled cases** (`eval/golden/figure_tieouts.json`) carry labels
  that reflect disclosure *judgment* or real adjudicated errors. These feed the
  **reliability gate** (`tests/test_eval.py`): figure false-negative rate must be 0.
- **Synthetic perturbation cases** (`eval/perturbation.py`) are minted by applying
  *known mutations* (scale error, digit transpose, ~15% typo) to real filed
  values. Their labels are correct *by construction*, so they can test the engine
  without it grading its own homework — but they only measure **robustness
  coverage**, not reliability.

- **EDGAR restatement cases** (`eval/restatement.py`) are harvested from 8-K
  **Item 4.02** ("non-reliance") filings — adjudicated cases where a reported
  number was wrong. Each yields two *real* labels: a draft citing the original
  value → `conflict`, the restated value → `traced`. Unlike synthetic cases these
  reflect how disclosures actually go wrong, carry the adjudicating accession as
  provenance, and are eligible for the reliability gate. `attest restatements`
  prints/emits them; a test asserts the engine *agrees* with the harvested labels.
- **Production-feedback candidates** (`eval/feedback.py`) are derived from human
  *overrides* in the audit log. Only an override tagged `engine_wrong` becomes a
  candidate (an "accepting risk" or "dismissing noise" override would poison
  precision if fed back as truth). Candidates are anonymizable (MNPI scrub keeps
  metric/period/verdict, drops the figure text + justification), tagged
  `production_feedback`, and are **candidates** — a human promotes them into the
  labeled set; nothing auto-enters the gate. This bucket is blind to false
  *negatives* by construction, so it complements restatement harvesting, never
  replaces it.

These are scored in **different buckets** (`run_eval` vs `run_synthetic_eval`; the
feedback bucket stays out of the gate entirely until promoted). The synthetic
report carries a `caveat` string so its accuracy can't be quietly pasted next to
the real gate. Summing buckets would inflate the headline metric — the exact
failure mode this separation exists to prevent.

The generator is restatement-aware (perturbs only the latest version of a fact);
it found and forced the fix of a mislabel bug on first run — see the commit
history. To generate from the Sheets corpus workbook:

```bash
attest synth --csv 02_Facts.csv --out synthetic.json   # from a sheet export
attest synth                                            # from the bundled fixture, with bucketed report
```

## API surface

```
GET  /                                           the upload & verify web UI
POST /tenants/{tenant}/ingest/xbrl              ingest an XBRL instance
POST /tenants/{tenant}/ingest/guidance          extract forward guidance from 8-K EX-99.1 prose

POST /tenants/{tenant}/ingest/edgar             pull an issuer's real filed facts from SEC EDGAR by ticker
GET  /tenants/{tenant}/facts                    list facts-with-provenance
GET  /tenants/{tenant}/extraction/aliases       the tenant's metric synonyms
PUT  /tenants/{tenant}/extraction/aliases       configure the tenant's metric synonyms
POST /tenants/{tenant}/analyze                  upload/paste a draft -> verdicts + findings
POST /tenants/{tenant}/verify                   verify one document  (?use_llm=true for the edge)
POST /tenants/{tenant}/verify-close-pack        verify + cross-document consistency  (?use_llm)
POST /tenants/{tenant}/documents/{id}/sign-off  record an attestation
POST /tenants/{tenant}/override                 record a justified override
GET  /tenants/{tenant}/audit                    export the audit trail (a projection)
GET  /audit/verify                              re-derive the hash chain
```

`POST /analyze` accepts a multipart `file` **or** a `text` field (plus optional
`title`, `kind`, `entity`, `period`). It recovers the prose, lets the edge
propose figure claims, and runs the full engine — the same spine the demo close
pack flows through, now driven by a real document. When `entity` is an issuer
**ticker** (e.g. `PANW`) and live EDGAR is enabled, the upload first loads that
issuer's real filed facts (below), so the draft ties out against its as-filed
numbers rather than coming back uniformly untraced.

### Uploading real documents — and where the "edge" lives

`attest analyze` needs something the demo provides for free: the mapping from a
numeric span in prose to *what metric it asserts*. The architecture is explicit
that this is the probabilistic edge's job and that **the model is the replaceable
component**. `src/attest/extraction/` is a deterministic, model-free reference
implementation of that edge — greedy figure detection plus keyword/alias mapping,
segment-entity resolution grounded in the tenant's own ingested facts, and light
period inference. It deliberately *over*-detects and labels anything it cannot
confidently attribute as low-confidence, so the deterministic core still disposes
and a guessed binding is never asserted as `traced`. Swap in an LLM here and
nothing downstream changes.

The metric vocabulary is **tenant-configurable** — every issuer's house style
differs ("topline" vs "net revenue", segment names, non-GAAP labels). Each tenant
starts from a default `AliasConfig` and layers its own synonyms over it via
`PUT /tenants/{tenant}/extraction/aliases` (or the *Custom terms* field in the
UI):

```bash
curl -X PUT .../tenants/{tenant}/extraction/aliases \
  -H 'content-type: application/json' \
  -d '{"aliases": {"total_revenue": ["topline", "net sales"]}, "replace": false}'
```

`replace: false` unions the phrases into the metric's existing list; `true`
overwrites it. The registry's own label is always in scope, unknown metric ids
are rejected, and a tenant's config never affects another's.

### Forward guidance — the one figure class that isn't in XBRL

XBRL is a gift for *reported* numbers, but management's next-period **guidance**
(revenue / EPS / operating margin) lives only in the press-release prose — the
8-K Exhibit 99.1 — never in a tagged fact. `src/attest/ingestion/guidance.py` is
the prose analog of the XBRL adapter: it consumes the EX-99.1 text your SEC
connector already fetches and lands each guidance statement as a citable `Fact`,
with the **exact sentence it came from** in `source_excerpt` and a pointer back to
the filed exhibit in `source_ref`. That is the anti–"cited the wrong number"
guarantee applied to the figure that is otherwise unverifiable: *here is the
number management gave, and here is the line it came from.*

```bash
curl -X POST .../tenants/{tenant}/ingest/guidance \
  -H 'content-type: application/json' \
  -d '{"text": "<EX-99.1 prose>", "entity": "ATLS",
       "accession": "0001047469-26-001200", "base_period": "FY2026-Q1"}'
```

It is deterministic and model-free like every connector: it attributes a metric by
keyword + unit, parses the figure with the same `parse_quantity` the spine uses
(a range collapses to its midpoint; the excerpt keeps the range verbatim), infers
the target period, and **only emits a fact when it can both attribute and parse** —
everything else is reported as skipped, never guessed. Because guidance published
in a filed 8-K exhibit is a real, citable disclosure, these facts are `filing_line`
(traceable), distinct from the internal pre-filing planning guidance that ingests
as non-filed `management_input`. A later draft that reaffirms prior guidance then
ties out to the exact published sentence.

### Tie out to real SEC filings (live EDGAR)

The reference fixture seeds one hand-shaped set of facts; real use needs the issuer's *actual* filed
numbers. `src/attest/ingestion/edgar.py` is the live sibling of the XBRL adapter:
given a ticker it resolves the CIK, pulls the issuer's machine-tagged `us-gaap`
facts from SEC's public `companyconcept` API, and lands them as `EDGAR_XBRL`
facts-with-provenance — the same shape the fixture produces, so the engine can
still call them `traced`. Upload a Palo Alto Networks earnings transcript with
`entity=PANW` and the headline figures tie out to the Q2 FY2026 10-Q ("$2.59
billion" → the filed `$2,594M`; "$16.0 billion" RPO → the filed value), while the
operational/non-GAAP figures that XBRL doesn't tag stay honestly untraced.

Two design points keep it honest rather than a scrape:

- **The fiscal period comes from the datapoint, not the filing.** SEC's `fy`/`fp`
  describe the *filing's* focus, so a prior-year comparative inside a 10-Q carries
  the current filing's `fy`/`fp`. Binding on that would invent a restatement. The
  connector derives each fact's period from its own `end` date against the issuer's
  fiscal-year-end (`submissions.fiscalYearEnd`), so comparatives land where they
  belong (PANW's year ends 31 July, so a quarter ending 31 Jan 2026 is `FY2026-Q2`).
- **Re-reports aren't restatements.** The same period is often re-stated in a later
  filing at a coarser rounding; those collapse to one fact within a tight
  tolerance, so only a *materially* different value lands as a new version — the
  engine's restatement detection still fires without rounding noise faking one.

```bash
attest ingest-edgar PANW              # preview an issuer's filed facts from EDGAR
attest serve                          # the served UI auto-loads filings when you upload with a ticker
curl -X POST .../tenants/acme/ingest/edgar -H 'content-type: application/json' -d '{"ticker": "PANW"}'
```

It is a swappable transport (`EdgarClient` Protocol): `HttpEdgarClient` is the real
stdlib-only client, `StaticEdgarClient` backs the hermetic tests. Live EDGAR is on
for `attest serve` and opt-in elsewhere via `ATTEST_EDGAR`; SEC asks every client to
identify itself, so set a contact in `ATTEST_EDGAR_USER_AGENT`. The whole suite runs
with no network; the one live smoke test is gated behind `ATTEST_TEST_EDGAR=1`.

## v2: the LLM edge (the *replaceable* probabilistic layer)

v1 is the deterministic spine. The first v2 increment adds the **edge** — the
model's job of *locating* candidate numbers and *reading* narrative direction —
behind a narrow, swappable seam. The core is unchanged: the model proposes, the
deterministic engine still disposes, and **the LLM never gets to say `traced`.**

```
src/attest/edge/
  client.py     LLMClient Protocol · AnthropicClient (prompt-cached) · FakeLLMClient
  prompts.py    system prompts + forced tool schemas (structured output only)
  proposer.py   prose -> FigureClaim[]  (the production stand-in for candidates.py)
  narrator.py   verified history + prose -> RuleFinding[]  (narrative.history_contradiction)
  service.py    EdgeService — composition seam AttestService optionally holds
```

- **Off by default.** `AttestService(edge=None)` behaves exactly as v1. Pass
  `edge=EdgeService(...)` to light up the `use_llm` path on `verify_document`,
  `verify_close_pack`, the `?use_llm=true` query param, and `attest verify --use-llm`.
- **The proposer** emits the same `FigureClaim` type the engine already consumes,
  so the detector is a constructor choice, not a schema change. Low-confidence
  proposals are routed to a human by the core, never asserted.
- **The narrator** flags prose whose *story* the numbers refute ("accelerating"
  when growth decelerated). Its findings are non-blocking (`warn`/`info`) by
  construction — a narrative false positive advises a human, it never blocks a
  publish.
- **Hermetic by design.** Every test runs with no `ANTHROPIC_API_KEY` and no
  network via `FakeLLMClient`; the integration test pins the invariant that the
  LLM path yields byte-for-byte the same verdicts as the deterministic path. The
  real client is an optional extra (`pip install ".[llm]"`, reads
  `ANTHROPIC_API_KEY`), with prompt caching on the system prompt and tool defs.

```bash
attest verify --use-llm   # runs the demo via the edge (scripted fake when no key is set)
```

## Production storage (Postgres + Redis)

The stores were always Protocols with in-memory reference implementations; this
is the durable backend behind them — proving the design's central claim that the
persistence layer is **a constructor swap, not an API change**. No engine, rule,
or API code is aware of which backend is live.

```
src/attest/storage/
  postgres.py     PostgresFactStore + PostgresAuditLog (psycopg)
  redis_cache.py  CachingFactStore — a read-through cache decorator over any store
  factory.py      service_from_env() / build_storage() — the one place a backend is chosen
  schema.sql      idempotent DDL (facts + hash-chained audit_events), bootstrapped on connect
```

- **`PostgresFactStore`** keeps every restatement version (`ORDER BY as_of, seq`)
  and stores each `Fact` losslessly as JSONB, so reads reconstruct the exact
  Pydantic model — Decimal values and all.
- **`PostgresAuditLog`** reuses the *same* pure `compute_hash` the in-memory log
  uses, so the chain is byte-identical across backends; appends take a
  transaction-scoped advisory lock to keep the sequence contiguous and
  tamper-evident under concurrency. `verify()` re-runs the in-memory verifier
  over the persisted rows.
- **`CachingFactStore`** fronts the read-heavy verification path with Redis
  (per-scope cache, write-through invalidation), and is a transparent decorator —
  it satisfies the `FactStore` Protocol and wraps any inner store.

```bash
docker compose up -d                          # local Postgres + Redis
export ATTEST_DATABASE_URL=postgresql://attest:attest@localhost:5432/attest
export ATTEST_REDIS_URL=redis://localhost:6379/0
attest serve                                  # now durably backed; unset the vars -> in-memory
```

The drivers are an optional extra (`pip install ".[storage]"`); with no env vars
the in-memory stores remain the default, so the demo and the test suite need no
database. The storage integration tests run against real servers when
`ATTEST_TEST_DATABASE_URL` / `ATTEST_TEST_REDIS_URL` are set, and skip otherwise.

## Scope

In scope (and built): the deterministic spine, the rules engines, ingestion
(incl. the **live EDGAR/XBRL connector** that ties uploads out against real filed
facts), the audit log, the eval harness, the API/CLI (v1), the LLM edge — claim
proposer and historical-consistency narrator — **plus durable Postgres + Redis
storage behind the existing Protocols** (above). Still **out of scope**: the
consensus parser, the ERP/close-package connector, the editor add-ins, and the
remaining production datastores (vector / object store). Schema migrations are an idempotent
`schema.sql` for now; a versioned tool (e.g. Alembic) is the next step before the
schema evolves in production.
