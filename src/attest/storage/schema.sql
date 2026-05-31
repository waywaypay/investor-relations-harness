-- Attest persistence schema (Postgres reference backend).
--
-- Two tables mirror the two in-memory reference stores exactly:
--   facts          the restatement-aware fact store (every version kept)
--   audit_events   the append-only, hash-chained audit log
--
-- The full domain object is stored losslessly in a JSONB `data` column; the
-- promoted columns exist only to index/scope queries. Reads reconstruct the
-- Pydantic model from `data`, so the column set can evolve without a data
-- migration. Idempotent (IF NOT EXISTS) so it doubles as a first-run bootstrap.

CREATE TABLE IF NOT EXISTS facts (
    seq        BIGSERIAL PRIMARY KEY,          -- stable insertion order (ties on as_of)
    id         TEXT        NOT NULL UNIQUE,     -- the fact's own id; dedupe key
    tenant_id  TEXT        NOT NULL,
    entity     TEXT        NOT NULL,
    metric     TEXT        NOT NULL,
    period     TEXT        NOT NULL,
    as_of      TEXT        NOT NULL,            -- ISO date the value was established/restated
    data       JSONB       NOT NULL             -- the full Fact, model_dump(mode="json")
);

-- Resolution is always by scope, newest-version-last; this index serves both
-- `versions(scope)` and `latest(scope)`.
CREATE INDEX IF NOT EXISTS facts_scope_idx
    ON facts (tenant_id, entity, metric, period, as_of, seq);
CREATE INDEX IF NOT EXISTS facts_tenant_idx
    ON facts (tenant_id, seq);

CREATE TABLE IF NOT EXISTS audit_events (
    seq        BIGINT      PRIMARY KEY,          -- 0-based chain index, contiguous
    timestamp  TEXT        NOT NULL,
    actor      TEXT        NOT NULL,
    type       TEXT        NOT NULL,
    tenant_id  TEXT        NOT NULL,
    payload    JSONB       NOT NULL,
    prev_hash  TEXT        NOT NULL,
    hash       TEXT        NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_tenant_idx ON audit_events (tenant_id, seq);
