"""Heuristic claim extraction — a stand-in for the probabilistic edge.

The architecture is explicit that locating numbers and proposing *what metric a
span asserts* is the model's job, and that "the model is the replaceable
component." This module is a **deterministic, model-free placeholder for that
edge**: greedy figure detection (reused from :mod:`attest.verification.candidates`)
plus keyword/alias mapping to canonical metrics, segment-entity resolution grounded
in the tenant's actual ingested facts, and light period inference.

Crucially it changes nothing about the trust model. Everything it emits is a
:class:`FigureClaim` — a *proposal*. The deterministic core still disposes, and it
labels anything it could not confidently attribute as ``LOW`` confidence so the
engine routes it to a human instead of asserting it. Over-detection is fine here;
under-detection is the failure mode, exactly as for the eventual LLM.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from attest.domain.facts import Confidence
from attest.domain.metrics import MetricRegistry
from attest.domain.money import Unit
from attest.domain.period import Period
from attest.domain.verdicts import FigureClaim
from attest.factstore.repository import FactStore
from attest.verification.candidates import detect_candidates


@runtime_checkable
class ClaimProposer(Protocol):
    """The probabilistic edge's contract: propose figure claims from prose.

    This is the swap point the architecture promises. ``ClaimExtractor`` is the
    model-free reference implementation; an LLM-backed proposer satisfies the same
    Protocol and drops in with no change to the deterministic core, because the
    core only ever consumes :class:`FigureClaim` *proposals* and disposes of them
    itself. Like :class:`~attest.factstore.repository.FactStore` and
    :class:`~attest.audit.log.AuditLog`, the seam is a Protocol, not a prose promise.
    """

    def extract(
        self,
        text: str,
        *,
        document_id: str,
        tenant_id: str,
        entity: str,
        period: str | None,
    ) -> tuple[FigureClaim, ...]: ...

# Curated aliases per canonical metric — the *default* vocabulary. Labels from the
# registry are folded in at runtime; this table adds the natural-language synonyms
# an IR draft actually uses. Every issuer's house style differs ("topline" vs "net
# revenue", segment names, non-GAAP labels), so this is a per-tenant override point
# (:class:`AliasConfig`); the shape stays the same whether it's this table, tenant
# config, or a learned model.
_ALIASES: dict[str, tuple[str, ...]] = {
    "total_revenue": ("total revenue", "total net revenue", "net revenue", "revenue", "net sales", "total sales", "sales"),
    "cloud_revenue": ("cloud segment revenue", "cloud revenue", "cloud segment", "cloud business"),
    "cloud_growth_yoy": ("cloud growth", "cloud segment revenue grew", "cloud revenue grew", "cloud"),
    "gaap_diluted_eps": ("gaap diluted eps", "gaap eps", "gaap diluted earnings per share", "diluted eps", "diluted earnings per share", "earnings per share"),
    "non_gaap_diluted_eps": ("non-gaap diluted eps", "non gaap diluted eps", "adjusted diluted eps", "non-gaap eps", "adjusted eps", "non-gaap diluted earnings per share"),
    "operating_cash_flow": ("operating cash flow", "cash flow from operations", "cash provided by operating activities", "cash from operations"),
    "share_repurchases": ("share repurchases", "repurchases of common stock", "repurchase of common stock", "repurchased", "stock buyback", "buyback"),
    "operating_margin": ("operating margin", "margin from operations"),
    "operating_margin_change_bps": ("operating margin expanded", "operating margin improved", "margin expansion", "basis points"),
    "q2_revenue_guidance": ("revenue in the range", "revenue guidance", "expects total revenue", "guidance"),
}

# Words that, near a percentage, signal a year-over-year growth figure.
_GROWTH_NEAR = re.compile(r"\b(grew|growth|up|increase[d]?|higher|rose|gain|yoy|year[- ]over[- ]year)\b", re.IGNORECASE)

# Forward-looking context that reclassifies a figure as guidance for a later period.
_GUIDANCE_NEAR = re.compile(
    r"\b(expects?|expect|anticipates?|outlook|guidance|we see|looking ahead|"
    r"for the (?:first|second|third|fourth) quarter|full[- ]year|in the range of|range of)\b",
    re.IGNORECASE,
)

# A guidance range stated as a single span: "$1.31 to $1.34 billion", "$1.31–1.34B".
_RANGE_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:to|through|and|[-–—])\s*\$?\s?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:billion|million|thousand|trillion|bn|mm|[bmkt])?",
    re.IGNORECASE,
)

_QUARTER_WORDS = {
    "first": 1, "1st": 1, "q1": 1, "second": 2, "2nd": 2, "q2": 2,
    "third": 3, "3rd": 3, "q3": 3, "fourth": 4, "4th": 4, "q4": 4,
}
_NEXT_Q_WORDS = re.compile(r"\b(first|second|third|fourth)\s+quarter\b", re.IGNORECASE)


@dataclass(frozen=True)
class AliasConfig:
    """A tenant's metric-attribution vocabulary for the extraction edge.

    Maps a canonical metric id to the natural-language phrases that, near a figure,
    signal that figure asserts the metric. This is the one piece of the edge that
    is genuinely tenant-specific, so it is a first-class, swappable value rather
    than a module constant. The registry's own ``label`` is always folded in at
    match time, so a tenant only configures the *extra* synonyms — and can never
    fully blind the extractor to a metric.
    """

    aliases: Mapping[str, tuple[str, ...]]

    def for_metric(self, metric_id: str) -> tuple[str, ...]:
        return tuple(self.aliases.get(metric_id, ()))

    def extend(
        self, overrides: Mapping[str, Iterable[str]], *, replace: bool = False
    ) -> "AliasConfig":
        """Return a new config with ``overrides`` applied per metric.

        ``replace=False`` (default) unions the new phrases into the metric's
        existing ones; ``replace=True`` overwrites that metric's list outright.
        Metrics not named in ``overrides`` are untouched either way.
        """
        merged: dict[str, tuple[str, ...]] = {k: tuple(v) for k, v in self.aliases.items()}
        for metric_id, phrases in overrides.items():
            cleaned = tuple(dict.fromkeys(p.strip().lower() for p in phrases if p and p.strip()))
            if replace:
                merged[metric_id] = cleaned
            else:
                merged[metric_id] = tuple(dict.fromkeys(merged.get(metric_id, ()) + cleaned))
        return AliasConfig(aliases=merged)

    def as_dict(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self.aliases.items() if v}


# The default vocabulary every tenant starts from.
DEFAULT_ALIASES = AliasConfig(aliases={k: v for k, v in _ALIASES.items()})

# How the service builds a proposer for a given tenant's analysis. The default is
# ``ClaimExtractor``; an LLM-backed edge is injected by passing any callable with
# this shape to :class:`~attest.service.AttestService`.
ProposerFactory = Callable[[MetricRegistry, FactStore, AliasConfig], ClaimProposer]


@dataclass(frozen=True)
class _MetricView:
    metric_id: str
    unit: Unit
    aliases: tuple[str, ...]


def _unit_of_candidate(text: str, qty_unit: Unit | None) -> Unit:
    if qty_unit is not None:
        return qty_unit
    if "%" in text:
        return Unit.PERCENT
    if re.search(r"bps|basis points", text, re.IGNORECASE):
        return Unit.BASIS_POINTS
    return Unit.CURRENCY


def infer_period(*texts: str) -> str | None:
    """Best-effort fiscal period like ``FY2026-Q1`` from a title/body.

    Prefers an explicit ``FY2026-Q1`` token, else a "<quarter> ... fiscal <year>"
    phrasing. Returns ``None`` when it cannot tell — the caller then leaves the
    period unset and the engine honestly reports those figures as untraced.
    """
    for text in texts:
        if (found := Period.find(text)) is not None:
            return str(found)
    blob = " ".join(t for t in texts if t).lower()
    year_m = re.search(r"(?:fiscal|fy)\s*(20\d\d)", blob) or re.search(r"\b(20\d\d)\b", blob)
    q = None
    for word, num in _QUARTER_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b(?:\s+quarter)?", blob):
            q = num
            break
    if year_m and q:
        return str(Period(year=int(year_m.group(1)), part=f"Q{q}"))
    return None


def _next_period(period: str | None) -> str | None:
    p = Period.parse(period)
    nxt = p.next_quarter() if p else None
    return str(nxt) if nxt else None


class ClaimExtractor:
    """Proposes :class:`FigureClaim` s from raw prose (the model-free edge)."""

    def __init__(
        self,
        registry: MetricRegistry,
        store: FactStore,
        aliases: AliasConfig | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.aliases = aliases or DEFAULT_ALIASES
        self._views = self._build_views(registry, self.aliases)

    @staticmethod
    def _build_views(registry: MetricRegistry, aliases: AliasConfig) -> list[_MetricView]:
        views: list[_MetricView] = []
        for spec in registry.metrics():
            phrases = set(aliases.for_metric(spec.id))
            phrases.add(spec.label.lower())  # the registry label is always in scope
            views.append(
                _MetricView(
                    metric_id=spec.id,
                    unit=spec.unit,
                    aliases=tuple(sorted(phrases, key=len, reverse=True)),
                )
            )
        return views

    def _segments(self, tenant_id: str, primary_entity: str) -> dict[str, str]:
        """keyword -> entity, learned from the tenant's own ingested segment facts.

        e.g. an ingested fact for ``MRDN:Cloud`` teaches us that the word "cloud"
        near a figure means the segment entity, not the parent issuer.
        """
        segments: dict[str, str] = {}
        for fact in self.store.all(tenant_id):
            if ":" in fact.entity and fact.entity != primary_entity:
                keyword = fact.entity.split(":", 1)[1].lower()
                segments[keyword] = fact.entity
        return segments

    def _match_metric(self, window: str, unit: Unit) -> tuple[str | None, bool]:
        """Pick the best metric whose unit matches and whose alias sits nearest the
        figure. Returns ``(metric_id, is_growth)``."""
        best: tuple[int, int, str] | None = None  # (end_pos, alias_len, metric_id)
        for view in self._views:
            if view.unit != unit:
                continue
            for alias in view.aliases:
                pos = window.rfind(alias)
                if pos < 0:
                    continue
                score = (pos + len(alias), len(alias), view.metric_id)
                if best is None or score > best:
                    best = score
        metric_id = best[2] if best else None
        spec = self.registry.get(metric_id) if metric_id else None
        is_growth = bool(spec and spec.derived_kind)
        return metric_id, is_growth

    def _guidance_metric(self, window: str) -> str | None:
        """Pick the currency *guidance* metric a range asserts, from its context.

        Attributes from the tenant's own vocabulary near the range (so a configured
        synonym wins), but only ever resolves to a guidance metric — never to a
        same-period actual like ``total_revenue``. Falls back to the registry's
        generic revenue-guidance metric, and returns ``None`` (caller marks the
        span low-confidence) when the registry defines no guidance metric at all.
        """
        metric, _ = self._match_metric(window, Unit.CURRENCY)
        if metric is not None and metric.endswith("_guidance"):
            return metric
        for spec in self.registry.metrics():
            if spec.unit is Unit.CURRENCY and spec.id.endswith("_guidance"):
                return spec.id
        return None

    def extract(
        self,
        text: str,
        *,
        document_id: str,
        tenant_id: str,
        entity: str,
        period: str | None,
    ) -> tuple[FigureClaim, ...]:
        segments = self._segments(tenant_id, entity)
        guidance_period = _next_period(period)
        claims: list[FigureClaim] = []
        consumed: list[tuple[int, int]] = []
        seq = 0

        # 1) Guidance ranges first — they own their whole span and feed the range rules.
        for m in _RANGE_RE.finditer(text):
            span = (m.start(), m.end())
            window = text[max(0, span[0] - 90) : span[0]].lower()
            if not _GUIDANCE_NEAR.search(window):
                continue
            # Attribute the range from the tenant's vocabulary near it, rather than
            # hard-coding a demo metric id; an unattributable range is surfaced as
            # low-confidence, never asserted.
            metric = self._guidance_metric(window)
            confidence = Confidence.HIGH
            if metric is None:
                metric, confidence = "unidentified", Confidence.LOW
            seq += 1
            claims.append(
                FigureClaim(
                    claim_id=f"{document_id}-c{seq}",
                    document_id=document_id,
                    entity=entity,
                    metric=metric,
                    period=guidance_period or period or "",
                    displayed_text=m.group(0).strip(),
                    span=span,
                    detect_confidence=confidence,
                )
            )
            consumed.append(span)

        # 2) Single figures.
        for cand in detect_candidates(text):
            if any(s <= cand.span[0] < e for s, e in consumed):
                continue  # already captured inside a guidance range
            unit = _unit_of_candidate(cand.text, cand.quantity.unit if cand.quantity else None)
            before = text[max(0, cand.span[0] - 90) : cand.span[0]].lower()
            # In disclosure prose the label precedes the figure ("non-GAAP EPS of $1.12"),
            # so attribute from the preceding context plus a short lookahead — never far
            # enough to borrow the *next* sentence's subject.
            ctx = before + " " + text[cand.span[1] : cand.span[1] + 4].lower()

            entity_for = entity
            for keyword, seg_entity in segments.items():
                if keyword in ctx:
                    entity_for = seg_entity
                    break

            metric, is_growth = self._match_metric(ctx, unit)
            claim_period = period or ""
            confidence = Confidence.HIGH

            if unit is Unit.PERCENT and is_growth and not _GROWTH_NEAR.search(before):
                # A percent that matched a growth metric but reads like a level, not
                # a change — demote to low confidence rather than assert a YoY claim.
                confidence = Confidence.LOW

            if metric is None:
                metric = "unidentified"
                confidence = Confidence.LOW

            # A non-guidance figure sitting in clearly forward-looking prose is routed
            # to the guidance period so it binds (and trips safe-harbor) correctly.
            if guidance_period and metric != "unidentified" and _GUIDANCE_NEAR.search(before) and unit is Unit.CURRENCY and "guidance" in metric:
                claim_period = guidance_period

            seq += 1
            claims.append(
                FigureClaim(
                    claim_id=f"{document_id}-c{seq}",
                    document_id=document_id,
                    entity=entity_for,
                    metric=metric,
                    period=claim_period,
                    displayed_text=cand.text,
                    span=cand.span,
                    detect_confidence=confidence,
                )
            )

        claims.sort(key=lambda c: (c.span or (0, 0))[0])
        return tuple(claims)
