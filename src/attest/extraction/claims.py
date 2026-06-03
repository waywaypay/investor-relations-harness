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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from attest.domain.facts import Confidence
from attest.domain.metrics import MetricRegistry
from attest.domain.money import Unit
from attest.domain.verdicts import FigureClaim
from attest.factstore.repository import FactStore
from attest.verification.candidates import detect_candidates

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
    "net_income": ("net income", "net earnings"),
    "operating_income": ("operating income", "income from operations", "operating profit"),
    "gross_profit": ("gross profit",),
    # Total RPO only — bound to the full phrase so the call's "current RPO" (a
    # different, smaller figure) isn't misattributed to the total.
    "total_rpo": ("remaining performance obligation", "remaining performance obligations"),
    "cash_and_equivalents": ("cash and cash equivalents", "cash and equivalents"),
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
_ORD_TO_Q = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2,
    "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
}
_NEXT_Q_WORDS = re.compile(r"\b(first|second|third|fourth)\s+quarter\b", re.IGNORECASE)
_PERIOD_RE = re.compile(r"FY\d{4}-Q[1-4]", re.IGNORECASE)

# A quarter bound to its year, the phrasing earnings materials actually use:
# "Second Quarter 2026", "First Quarter Fiscal 2026", "second quarter of fiscal
# 2026", "Q2 2026", "Q2 FY2026". The year may sit right after "quarter" or behind
# an intervening "fiscal"/"of fiscal".
_QY_RE = re.compile(
    r"\b(?:(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter|q\s*([1-4]))"
    r"(?:\s+of)?(?:\s+fiscal)?(?:\s+fy)?[\s,'-]+(?:fy\s*)?(20\d\d)\b",
    re.IGNORECASE,
)
# The reverse order: "fiscal 2026 second quarter", "2026 Q2".
_YQ_RE = re.compile(
    r"\b(?:fiscal\s+|fy\s*)?(20\d\d)[\s,'-]+(?:fiscal\s+)?"
    r"(?:(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter|q\s*([1-4]))\b",
    re.IGNORECASE,
)


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


def _scan_quarter_year(text: str) -> str | None:
    """The earliest "<quarter> <year>" (or "<year> <quarter>") phrase in ``text``.

    Earliest wins because the title / first line of an earnings transcript states
    the period under report ("Fiscal Second Quarter 2026"), while later mentions
    are usually guidance or comparatives for *other* periods.
    """
    best: tuple[int, int, int] | None = None  # (position, year, quarter)
    for regex, year_first in ((_QY_RE, False), (_YQ_RE, True)):
        for m in regex.finditer(text):
            if year_first:
                year, ord_word, qnum = m.group(1), m.group(2), m.group(3)
            else:
                ord_word, qnum, year = m.group(1), m.group(2), m.group(3)
            quarter = _ORD_TO_Q[ord_word.lower()] if ord_word else int(qnum)
            cand = (m.start(), int(year), quarter)
            if best is None or cand[0] < best[0]:
                best = cand
    return f"FY{best[1]}-Q{best[2]}" if best else None


def infer_period(*texts: str) -> str | None:
    """Best-effort fiscal period like ``FY2026-Q1`` from a title/body.

    In order of confidence: an explicit ``FY2026-Q1`` token; a quarter bound to a
    year ("Fiscal Second Quarter 2026"); finally a loose "any quarter word + any
    year" scan. Returns ``None`` when it cannot tell — the caller leaves the period
    unset and the engine honestly reports those figures as untraced.
    """
    for text in texts:
        if not text:
            continue
        if (m := _PERIOD_RE.search(text)) is not None:
            return m.group(0).upper()
    for text in texts:
        if not text:
            continue
        if (period := _scan_quarter_year(text)) is not None:
            return period
    blob = " ".join(t for t in texts if t).lower()
    year_m = re.search(r"(?:fiscal|fy)\s*(20\d\d)", blob) or re.search(r"\b(20\d\d)\b", blob)
    q = None
    for word, num in _QUARTER_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b(?:\s+quarter)?", blob):
            q = num
            break
    if year_m and q:
        return f"FY{year_m.group(1)}-Q{q}"
    return None


def _next_period(period: str | None) -> str | None:
    if not period:
        return None
    m = re.match(r"FY(\d{4})-Q([1-4])", period, re.IGNORECASE)
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    return f"FY{year + 1}-Q1" if q == 4 else f"FY{year}-Q{q + 1}"


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
        is_growth = bool(metric_id and self.registry.get(metric_id) and self.registry.get(metric_id).derived_kind)
        return metric_id, is_growth

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
            metric = "q2_revenue_guidance" if "q2_revenue_guidance" in self.registry else "revenue_guidance"
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
                    detect_confidence=Confidence.HIGH,
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
