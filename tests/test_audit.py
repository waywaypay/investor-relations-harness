import pytest

from attest.audit.events import AuditEvent, EventType
from attest.audit.log import ChainIntegrityError, InMemoryAuditLog


def test_chain_links_and_verifies():
    log = InMemoryAuditLog()
    log.append(actor="a", type=EventType.INGEST, tenant_id="t", payload={"n": 1})
    log.append(actor="b", type=EventType.VERDICT, tenant_id="t", payload={"v": "traced"})
    events = log.events()
    assert events[0].seq == 0
    assert events[1].prev_hash == events[0].hash
    assert log.verify() is True


def test_tampering_breaks_the_chain():
    log = InMemoryAuditLog()
    log.append(actor="a", type=EventType.INGEST, tenant_id="t", payload={"n": 1})
    log.append(actor="b", type=EventType.SIGN_OFF, tenant_id="t", payload={"doc": "release"})

    # Retroactively rewrite an earlier event's payload (keeping its old hash).
    tampered = log.events()[0].model_copy(update={"payload": {"n": 999}})
    log._events[0] = tampered

    with pytest.raises(ChainIntegrityError):
        log.verify()


def test_exported_chain_verifies_independently():
    log = InMemoryAuditLog()
    for i in range(5):
        log.append(actor="sys", type=EventType.VERDICT, tenant_id="t", payload={"i": i})
    exported = [AuditEvent(**e.model_dump()) for e in log.events()]
    assert InMemoryAuditLog.verify_export(exported) is True


def test_tenant_scoped_export():
    log = InMemoryAuditLog()
    log.append(actor="a", type=EventType.INGEST, tenant_id="t1", payload={})
    log.append(actor="b", type=EventType.INGEST, tenant_id="t2", payload={})
    assert len(log.events("t1")) == 1
    assert len(log.events()) == 2
