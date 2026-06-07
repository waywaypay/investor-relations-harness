"""Directional-language consistency.

A drafter writes "operating margin *expanded* year over year" — but did it? This
rule recovers the actual sign of a metric's YoY change from the fact store and
checks it against the direction word used near the figure in prose. An "expanded"
when the number fell is the kind of error that survives every figure tie-out
(each number is individually correct) yet still misstates the quarter.

Deterministic and conservative: it fires only when (a) a claimed metric has a
known direction word adjacent to it in the text, and (b) both the current and
prior-year facts exist. Otherwise it stays silent — it never guesses intent.
"""

from __future__ import annotations

import re

from attest.domain.document import Document
from attest.domain.metrics import MetricRegistry
from attest.domain.verdicts import RuleFinding, RuleSeverity
from attest.factstore.repository import FactStore

_UP_WORDS = {"expanded", "increased", "grew", "rose", "improved", "higher", "up"}
_DOWN_WORDS = {"declined", "decreased", "fell", "contracted", "dropped", "lower", "down"}

_PRIOR_YEAR_RE = re.compile(r"^FY(\d{4})(-.*)?$")


def _prior_year(period: str) -> str | None:
    m = _PRIOR_YEAR_RE.match(period)
    if not m:
        return None
    return f"FY{int(m.group(1)) - 1}{m.group(2) or ''}"


def _direction_word_near(text: str, label: str) -> str | None:
    """Find an up/down word in a window starting at a mention of ``label``.

    Matches on the metric's label (e.g. "operating margin"). If the label is *not*
    in the prose we return None rather than scanning the whole document: a direction
    word elsewhere ("revenue *grew*") describes some *other* metric, and attributing
    it here would block a release over a contradiction that was never asserted. The
    rule must read a direction word that is genuinely adjacent to the metric, exactly
    as its contract promises. Returns 'up', 'down', or None.
    """
    lowered = text.lower()
    idx = lowered.find(label.lower())
    if idx == -1:
        return None  # the metric is not named here — never attribute a foreign verb
    window = lowered[idx : idx + 120]
    words = set(re.findall(r"[a-z]+", window))
    if words & _UP_WORDS:
        return "up"
    if words & _DOWN_WORDS:
        return "down"
    return None


def check_directional_language(
    document: Document, registry: MetricRegistry, store: FactStore
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    seen: set[str] = set()

    for claim in document.claims:
        if claim.metric in seen:
            continue
        spec = registry.get(claim.metric)
        if spec is None:
            continue

        word_dir = _direction_word_near(document.text, spec.label)
        if word_dir is None:
            continue

        prior_period = _prior_year(claim.period)
        if prior_period is None:
            continue
        current = store.latest(document.tenant_id, claim.entity, claim.metric, claim.period)
        prior = store.latest(document.tenant_id, claim.entity, claim.metric, prior_period)
        if current is None or prior is None or current.value == prior.value:
            continue

        actual_dir = "up" if current.value > prior.value else "down"
        if actual_dir != word_dir:
            seen.add(claim.metric)
            findings.append(
                RuleFinding(
                    rule="directional.sign_mismatch",
                    severity=RuleSeverity.BLOCK,
                    document_id=document.id,
                    metric=claim.metric,
                    message=f"Prose describes '{spec.label}' as moving {word_dir}, but it "
                    f"moved {actual_dir} year over year "
                    f"({prior.quantity().display()} → {current.quantity().display()}).",
                    detail="Directional wording contradicts the sign of the change.",
                )
            )

    return findings
