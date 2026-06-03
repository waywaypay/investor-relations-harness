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

attest demo          # ingest the Meridian filing, verify the close pack, print a report
attest eval          # run the golden-set deploy gates and print a scored report
pytest               # run the suite, including the eval regression gate
attest serve         # API + upload UI at http://127.0.0.1:8000  (API docs at /docs)
```

Open **http://127.0.0.1:8000** and you get an upload page: drop a real press
release / earnings script / Q&A (`.txt`, `.md`, `.html`, `.docx`, `.pdf`, `.rtf`)
or paste the text, click *Load Meridian demo filing* to seed filed sources, and
hit *Analyze*. Every figure is highlighted in place by verdict, with the Reg G /
safe-harbor / consistency findings underneath.

## What it does

Given a company's filing (ingested as machine-tagged XBRL) and a draft release /
script / Q&A, the engine renders one of four verdicts per figure:

| Verdict | Meaning |
| --- | --- |
| `traced` | exact match to a **filed** source, within the tenant's rounding policy |
| `needs_review` | bound to a non-filed source (e.g. forward guidance) — needs sign-off |
| `conflict` | bound to a source but the value differs, **including cross-filing restatements** |
| `untraced` | no source could be bound |

The bundled `attest demo` reproduces the prototype's Meridian Systems case: the
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
  ingestion/       connectors (EDGAR/XBRL adapter, 8-K EX-99.1 guidance adapter,
                                sample filing + press-release fixtures)
  eval/            golden-set harness + deploy gates over three surfaces:
                     figure tie-outs (figure FN rate must be 0), the extraction
                     edge (no under-detection; a mis-attribution is never traced),
                     and the rule engines (no missed flag, no spurious flag)
  api/             stateless FastAPI surface over the service
  service.py       composition root shared by the API and CLI
  cli.py           `attest demo` / `attest serve`
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
- **Eval is non-negotiable infrastructure.** `attest/eval` scores three surfaces
  against labeled golden sets, each with the right asymmetry. `attest eval` runs
  the gates and exits non-zero on a breach; `tests/test_eval.py` asserts the same
  gates; `.github/workflows/ci.yml` runs lint + the suite + the gates on every push
  and PR. See **Evals & CI** below.

## API surface

```
GET  /                                           the upload & verify web UI
POST /tenants/{tenant}/ingest/xbrl              ingest an XBRL instance
POST /tenants/{tenant}/ingest/guidance          extract forward guidance from 8-K EX-99.1 prose
POST /tenants/{tenant}/ingest/demo              seed the bundled Meridian filing
GET  /tenants/{tenant}/facts                    list facts-with-provenance
GET  /tenants/{tenant}/extraction/aliases       the tenant's metric synonyms
PUT  /tenants/{tenant}/extraction/aliases       configure the tenant's metric synonyms
POST /tenants/{tenant}/analyze                  upload/paste a draft -> verdicts + findings
POST /tenants/{tenant}/verify                   verify one document (pre-built claims)
POST /tenants/{tenant}/verify-close-pack        verify + cross-document consistency
POST /tenants/{tenant}/documents/{id}/sign-off  record an attestation
POST /tenants/{tenant}/override                 record a justified override
GET  /tenants/{tenant}/audit                    export the audit trail (a projection)
GET  /audit/verify                              re-derive the hash chain
```

`POST /analyze` accepts a multipart `file` **or** a `text` field (plus optional
`title`, `kind`, `entity`, `period`). It recovers the prose, lets the edge
propose figure claims, and runs the full engine — the same spine the demo close
pack flows through, now driven by a real document.

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
curl -X PUT .../tenants/meridian/extraction/aliases \
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
curl -X POST .../tenants/meridian/ingest/guidance \
  -H 'content-type: application/json' \
  -d '{"text": "<EX-99.1 prose>", "entity": "MRDN",
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

## Evals & CI

For a trust product the eval pipeline is the asset that lets you say "trustworthy"
without lying, so it is wired as a gate, not a dashboard. `attest/eval` scores three
surfaces against labeled golden sets (`src/attest/eval/golden/*.json`), each with the
asymmetry that matters for that surface:

| Surface | Golden set | What it measures | Gate |
| --- | --- | --- | --- |
| **Figure tie-outs** | `figure_tieouts` | the engine's verdict per labeled figure | a wrong number called `traced` is catastrophic → figure FN rate **= 0** |
| **Extraction edge** | `extraction_attribution` | the replaceable (eventually-LLM) edge mapping prose → claims | never under-detect (recall **= 1.0**); a mis-attributed figure is **never** `traced` (unsafe traces **= 0**); attribution accuracy held to a regression floor |
| **Rule engines** | `rule_findings` | Reg G / FLS / consistency / derived findings | never miss a compliance flag (recall **= 1.0**); never raise a spurious one (precision **= 1.0**) |

The extraction gate encodes the architecture's load-bearing claim directly: the edge
is *allowed* to be imperfect — attribution accuracy is a regression floor, not a demand
for perfection — **because the deterministic core is the safety net.** The harness runs
each labeled figure end to end and proves that every figure the edge mis-attributes
comes back `untraced` / `conflict` / `needs_review`, never `traced`. That is what lets
you swap a model into the edge without re-earning trust downstream.

```bash
attest eval     # run all three gates, print the scored report, exit non-zero on a breach
pytest tests/test_eval.py   # the same gates, asserted as the CI regression test
```

`.github/workflows/ci.yml` runs `ruff` + the full `pytest` suite (which includes the
gates) + `attest eval` + the demo smoke test on every push and pull request, across
Python 3.11 and 3.12. A model, rule, or prompt change that regresses any surface fails
CI before it can merge.

## Scope of this v1

In scope (and built): the deterministic spine, the rules engines, ingestion, the
audit log, the eval harness, the API/CLI. Deliberately **out of scope** for v1
(per the build sequence): the narrative/historical-consistency LLM service, the
consensus parser, the ERP/close-package connector, the editor add-ins, and the
production datastores (Postgres/Redis/vector/object store). Each store here is an
in-memory reference implementation behind a `Protocol`, so the persistence
backend is a constructor swap, not an API change.
