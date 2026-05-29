"""Attest — the deterministic disclosure-verification spine for investor relations.

The package is organised inside-out, mirroring the architecture's first principle
("the fact store is the spine"):

    domain/        provenance-typed value objects (Fact, Quantity, Verdict)
    factstore/     the normalized store of facts-with-provenance
    verification/  the deterministic engine: detect -> normalize -> bind -> verdict
    audit/         the append-only, hash-chained audit log
    ingestion/     source connectors (EDGAR / XBRL adapter)
    eval/          the golden-set eval harness that lets us say "trustworthy"
    api/           the stateless FastAPI surface over the services
"""

__version__ = "0.1.0"
