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
pytest               # run the suite, including the eval regression gate
attest serve         # run the API at http://127.0.0.1:8000  (docs at /docs)
```

A React + TypeScript front-end (the disclosure-drafting workspace from the design
brief) lives in [`web/`](web/README.md) — run it standalone with `cd web && npm install && npm run dev`.

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
  ingestion/       connectors (EDGAR/XBRL adapter + a sample filing fixture)
  eval/            the golden-set harness + CI gate (figure FN rate must be 0)
    perturbation.py  synthetic case generator — known mutations of real filed values
    synthetic_eval.py scores the engine on synthetic cases in a SEPARATE bucket
    sheets_bridge.py  loads the corpus workbook's 02_Facts CSV export into facts
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

These are scored in **different buckets** (`run_eval` vs `run_synthetic_eval`) and
the synthetic report carries a `caveat` string so its accuracy can't be quietly
pasted next to the real gate. Summing them would inflate the headline metric with
easy synthetic cases — the exact failure mode this split exists to prevent.

The generator is restatement-aware (perturbs only the latest version of a fact);
it found and forced the fix of a mislabel bug on first run — see the commit
history. To generate from the Sheets corpus workbook:

```bash
attest synth --csv 02_Facts.csv --out synthetic.json   # from a sheet export
attest synth                                            # from the bundled fixture, with bucketed report
```

## API surface

```
POST /tenants/{tenant}/ingest/xbrl              ingest an XBRL instance
GET  /tenants/{tenant}/facts                    list facts-with-provenance
POST /tenants/{tenant}/verify                   verify one document
POST /tenants/{tenant}/verify-close-pack        verify + cross-document consistency
POST /tenants/{tenant}/documents/{id}/sign-off  record an attestation
POST /tenants/{tenant}/override                 record a justified override
GET  /tenants/{tenant}/audit                    export the audit trail (a projection)
GET  /audit/verify                              re-derive the hash chain
```

## Scope of this v1

In scope (and built): the deterministic spine, the rules engines, ingestion, the
audit log, the eval harness, the API/CLI. Deliberately **out of scope** for v1
(per the build sequence): the narrative/historical-consistency LLM service, the
consensus parser, the ERP/close-package connector, the editor add-ins, and the
production datastores (Postgres/Redis/vector/object store). Each store here is an
in-memory reference implementation behind a `Protocol`, so the persistence
backend is a constructor swap, not an API change.
