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

# Where a figure's own trailing label ends and the next clause begins. A trailing
# label is a tight prepositional tail ("$338 million of operating cash flow"); once
# we hit punctuation or a conjunction we are into the next clause's subject and must
# not borrow it — the same caution the old 4-char lookahead enforced, now wide enough
# to actually reach a trailing label.
_CLAUSE_BREAK = re.compile(r"[.,;:\n]|\b(?:and|but|while|which|whereas)\b", re.IGNORECASE)

_QUARTER_WORDS = {
    "first": 1, "1st": 1, "q1": 1, "second": 2, "2nd": 2, "q2": 2,
    "third": 3, "3rd": 3, "q3": 3, "fourth": 4, "4th": 4, "q4": 4,
}
_NEXT_Q_WORDS = re.compile(r"\b(first|second|third|fourth)\s+quarter\b", re.IGNORECASE)
_PERIOD_RE = re.compile(r"FY\d{4}-Q[1-4]", re.IGNORECASE)


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


def infer_period(*texts: str) -> str | None:
    """Best-effort fiscal period like ``FY2026-Q1`` from a title/body.

    Prefers an explicit ``FY2026-Q1`` token, else a "<quarter> ... fiscal <year>"
    phrasing. Returns ``None`` when it cannot tell — the caller then leaves the
    period unset and the engine honestly reports those figures as untraced.
    """
    for text in texts:
        if not text:
            continue
        if (m := _PERIOD_RE.search(text)) is not None:
            return m.group(0).upper()
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

    def _metrics_reported_for(self, tenant_id: str, entity: str) -> set[str]:
        """The metric ids the store actually has facts for under ``entity``.

        Used to bias attribution when a figure resolves to a *segment* entity: a
        segment reports a known, narrow set of metrics, so a generic word like
        "revenue" near a Cloud figure should resolve to ``cloud_revenue``, not the
        parent's ``total_revenue``. Grounded in ingested facts, exactly as segment
        entity resolution already is.
        """
        return {f.metric for f in self.store.all(tenant_id) if f.entity == entity}

    def _match_metric(
        self, before: str, after: str, unit: Unit, preferred: set[str] | None = None
    ) -> tuple[str | None, bool, tuple[int, int] | None]:
        """Pick the best metric whose unit matches and whose alias sits nearest the
        figure, looking *both* ways.

        Disclosure prose usually leads with the label ("non-GAAP EPS of $1.12"),
        but spoken transcripts just as often trail it ("$338 million of operating
        cash flow"). So we score each alias by its gap to the figure in either the
        preceding (``before``) or following (``after``) window and take the closest;
        a segment-``preferred`` metric wins over a generic one at any gap, and on a
        tie a leading label and the longer alias win.

        Returns ``(metric_id, is_growth, trailing_span)`` where ``trailing_span`` is
        ``(start, end)`` offsets into ``after`` when the winning label trailed the
        figure (else ``None``) — so the caller can stop the *next* figure from
        re-attributing this figure's own trailing label.
        """
        best_key: tuple[int, int, int, int] | None = None
        best: tuple[str, tuple[int, int] | None] | None = None  # (metric_id, trailing_span)
        for view in self._views:
            if view.unit != unit:
                continue
            is_pref = 0 if (preferred and view.metric_id in preferred) else 1
            for alias in view.aliases:
                b = before.rfind(alias)
                if b >= 0:
                    gap = len(before) - (b + len(alias))
                    key = (is_pref, gap, 0, -len(alias))  # 0: leading label preferred on ties
                    if best_key is None or key < best_key:
                        best_key, best = key, (view.metric_id, None)
                a = after.find(alias)
                if a >= 0:
                    key = (is_pref, a, 1, -len(alias))
                    if best_key is None or key < best_key:
                        best_key, best = key, (view.metric_id, (a, a + len(alias)))
        if best is None:
            return None, False, None
        metric_id, trailing_span = best
        spec = self.registry.get(metric_id)
        is_growth = bool(spec and spec.derived_kind)
        return metric_id, is_growth, trailing_span

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
        figures = [
            cand
            for cand in detect_candidates(text)
            if not any(s <= cand.span[0] < e for s, e in consumed)
        ]
        # Trailing labels a previous figure already claimed for itself. We blank just
        # these spans out of later figures' preceding windows — without them, the
        # off-by-one in "$338M of operating cash flow and returned $250M" gives both
        # figures the cash-flow label. Masking (rather than truncating the window)
        # keeps the rest of the leading context intact, e.g. the "cloud" that the
        # very next "31%" needs to read as cloud growth.
        claimed_labels: list[tuple[int, int]] = []
        for i, cand in enumerate(figures):
            unit = _unit_of_candidate(cand.text, cand.quantity.unit if cand.quantity else None)
            # Preceding window: the usual 90 chars, with prior figures' trailing
            # labels masked so they are not re-attributed here.
            win_start = max(cand.span[0] - 90, 0)
            before_chars = list(text[win_start : cand.span[0]].lower())
            for ls, le in claimed_labels:
                a, b = max(ls, win_start), min(le, cand.span[0])
                for k in range(a - win_start, b - win_start):
                    before_chars[k] = " "
            before = "".join(before_chars)
            # Following window: a short trailing tail, stopped at the next figure and
            # at the first clause boundary so we read this figure's own label
            # ("$338 million of operating cash flow") but never the next clause's.
            after_end = cand.span[1] + 60
            if i + 1 < len(figures):
                after_end = min(after_end, figures[i + 1].span[0])
            after = text[cand.span[1] : after_end]
            if (brk := _CLAUSE_BREAK.search(after)) is not None:
                after = after[: brk.start()]  # stop at the clause boundary
            after = after.lower()

            # Entity resolution stays leading-biased (a later "cloud" mention must not
            # pull an EPS figure into the Cloud segment), with only a short lookahead.
            ectx = before + " " + text[cand.span[1] : cand.span[1] + 4].lower()
            entity_for = entity
            for keyword, seg_entity in segments.items():
                if keyword in ectx:
                    entity_for = seg_entity
                    break

            # A figure that resolved to a segment should attribute to a metric that
            # segment actually reports, so a bare "revenue" near a Cloud figure binds
            # to cloud_revenue rather than the parent's total_revenue.
            preferred = (
                self._metrics_reported_for(tenant_id, entity_for)
                if entity_for != entity
                else None
            )

            metric, is_growth, trailing_span = self._match_metric(
                before, after, unit, preferred
            )
            claim_period = period or ""
            confidence = Confidence.HIGH

            if unit is Unit.PERCENT and is_growth and not _GROWTH_NEAR.search(before + " " + after):
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

            # Record a trailing label this figure just consumed so later figures don't
            # re-attribute it — but only when it sits closer to this figure than to the
            # next, otherwise it is the next figure's leading label, left for it.
            if trailing_span is not None:
                start_gap, end_off = trailing_span
                label_start = cand.span[1] + start_gap
                label_end = cand.span[1] + end_off
                next_start = figures[i + 1].span[0] if i + 1 < len(figures) else len(text)
                if start_gap <= (next_start - label_end):
                    claimed_labels.append((label_start, label_end))

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
