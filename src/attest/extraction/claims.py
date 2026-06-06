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
from attest.domain.money import NOUN_WORDS, Unit
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
    # "loss per share" is the same EPS line item when the period is a loss — a
    # company in the red writes "diluted loss per share", never "earnings". Without
    # these, the loss figure (already the most consequential number to verify) lands
    # unidentified and ships untraced.
    "gaap_diluted_eps": ("gaap diluted eps", "gaap eps", "gaap diluted earnings per share", "gaap diluted loss per share", "diluted eps", "diluted earnings per share", "diluted loss per share", "earnings per share", "loss per share", "net loss per share"),
    "non_gaap_diluted_eps": ("non-gaap diluted eps", "non gaap diluted eps", "adjusted diluted eps", "non-gaap eps", "adjusted eps", "non-gaap diluted earnings per share", "non-gaap diluted loss per share", "adjusted diluted loss per share", "non-gaap loss per share", "adjusted loss per share"),
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

# Words that, near a percentage, signal a year-over-year *change* (rather than a
# level). Earnings prose phrases growth a dozen ways — "grew", "growing", "grows",
# "rising", "increasing", "climbed", "expanded", "gained" — so cover the common
# inflections/synonyms; under-coverage here silently demotes a real change figure
# (and any restatement conflict it carries) to a low-confidence review item. This
# only fires for a percent already mapped to a growth metric, so broader recall
# here cannot turn a level into a spurious change claim.
_GROWTH_NEAR = re.compile(
    r"\b(grow\w*|grew|grown|ros\w*|rose|risen|ris\w*|"
    r"increas\w*|decreas\w*|declin\w*|gain\w*|climb\w*|"
    r"expand\w*|expansion|contract\w*|jump\w*|surg\w*|advanc\w*|"
    r"(?:de|ac)celerat\w*|up|down|higher|lower|yoy|year[- ]over[- ]year)\b",
    re.IGNORECASE,
)

# Forward-looking context that reclassifies a figure as guidance for a later period.
# These verbs/phrases are unambiguously forward; the "for the Nth quarter" opener is
# handled separately by :func:`_has_guidance_context` because it is only guidance
# when the named quarter differs from the period under report (a retrospective
# "results for the first quarter of fiscal 2026" names the *current* period).
_GUIDANCE_NEAR = re.compile(
    r"\b(expects?|expect|anticipates?|outlook|guidance|we see|looking ahead|"
    r"full[- ]year|in the range of|range of)\b",
    re.IGNORECASE,
)

# "For the second quarter, we expect …" — a bare quarter phrase, qualified against
# the current reporting quarter by :func:`_has_guidance_context`.
_FOR_THE_QUARTER = re.compile(r"for the (first|second|third|fourth) quarter", re.IGNORECASE)


def _current_quarter(period: str | None) -> int | None:
    if not period:
        return None
    m = re.match(r"FY\d{4}-Q([1-4])", period, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _has_guidance_context(window: str, period: str | None) -> bool:
    """True when ``window`` reads as forward-looking guidance.

    Strong forward verbs/phrases (``_GUIDANCE_NEAR``) always qualify. A bare "for
    the Nth quarter" qualifies only when the named quarter differs from the current
    reporting quarter — otherwise it is the retrospective "results for the Nth
    quarter" opener naming the period under report, which must not reclassify this
    period's figures as a forecast for a later one. When the period is unknown the
    quarter phrase is treated as guidance (the pre-existing, conservative default).
    """
    if _GUIDANCE_NEAR.search(window):
        return True
    current = _current_quarter(period)
    for m in _FOR_THE_QUARTER.finditer(window):
        q = _ORD_TO_Q.get(m.group(1).lower())
        if q is not None and q != current:
            return True
    return False

# A guidance range stated as a single span: "$1.31 to $1.34 billion", "$1.31–1.34B",
# or — as transcripts phrase it — symbol-free "1.31 to 1.34 billion". When no "$"
# is present a trailing scale word is required so a plain year span ("2025 to 2026")
# is not mistaken for money, and a trailing count noun ("400 to 500 million users")
# is excluded so an operating-metric range is not read as currency guidance.
_RANGE_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:to|through|and|[-–—])\s*\$?\s?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:billion|million|thousand|trillion|bn|mm|[bmkt])?"
    r"|\b\d[\d,]*(?:\.\d+)?\s*(?:to|through|and|[-–—])\s*\d[\d,]*(?:\.\d+)?\s*"
    rf"(?:billion|million|thousand|trillion|bn|mm)\b(?!\s*(?:{NOUN_WORDS})\b)",
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
    # The normalized quantity is authoritative when present; this text-based path
    # is the fallback for spans that resisted normalization (quantity is None).
    if qty_unit is not None:
        return qty_unit
    if "%" in text or re.search(r"\b(?:percent|pct)\b", text, re.IGNORECASE):
        return Unit.PERCENT
    if re.search(r"bps|basis points", text, re.IGNORECASE):
        return Unit.BASIS_POINTS
    if re.search(r"\bshares?\b", text, re.IGNORECASE):
        return Unit.SHARES
    if re.search(rf"\b(?:{NOUN_WORDS})\b", text, re.IGNORECASE):
        return Unit.COUNT
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


# An exchange-qualified ticker, the form earnings materials use to identify the
# issuer: "Meridian Systems (NASDAQ: MRDN)", "(NYSE American: ABC.A)". Anchoring
# on a known exchange keyword + colon keeps this from matching arbitrary
# parentheticals or capitalised words. No IGNORECASE on the symbol: tickers are
# upper-case, and that avoids matching lower-case prose after a stray colon.
_TICKER_RE = re.compile(
    r"(?:NYSE(?:\s+American|\s+Arca)?|Nasdaq|NASDAQ|Cboe|OTCQX|OTCQB|OTC|AMEX|TSX|LSE)"
    r"[^):\n]*?:\s*([A-Z]{1,5}(?:\.[A-Z])?)\b"
)


def infer_entity_ticker(*texts: str) -> str | None:
    """Best-effort issuer ticker from a draft's prose, so an upload ties out to
    the right company without the author typing it.

    Looks only for an *exchange-qualified* ticker (e.g. "Acme Corp (NASDAQ: ACME)")
    — that explicit anchor is reliable and keeps it from guessing at arbitrary
    capitalised words. Returns the upper-cased symbol, or ``None`` when none is
    stated, so the caller leaves the entity unresolved rather than tying out to
    the wrong issuer.
    """
    for text in texts:
        if not text:
            continue
        if (m := _TICKER_RE.search(text)) is not None:
            return m.group(1).upper()
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
            if not _has_guidance_context(window, period):
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
        all_cands = list(detect_candidates(text))
        cand_units = [
            _unit_of_candidate(c.text, c.quantity.unit if c.quantity else None) for c in all_cands
        ]
        for idx, cand in enumerate(all_cands):
            if any(s <= cand.span[0] < e for s, e in consumed):
                continue  # already captured inside a guidance range
            unit = cand_units[idx]
            # A label belongs to its own figure. Bound the look-back at the nearest
            # preceding figure *of the same unit*, so a currency label is never stolen
            # across another currency figure ("Operating cash flow was $338M, and we
            # returned $250M…" must not bind $250M to operating_cash_flow). Looking
            # past a different-unit figure is fine and necessary — a percent between a
            # subject and its currency figure ("RPO grew 23% to $16.0 billion") still
            # resolves. Without this, the borrowed label produces a confident false
            # conflict; bounded, the figure falls to low confidence and human review.
            same_unit_end = max(
                (all_cands[j].span[1] for j in range(idx) if cand_units[j] == unit),
                default=0,
            )
            lo = max(cand.span[0] - 90, same_unit_end)
            before = text[lo : cand.span[0]].lower()
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

            # A currency figure sitting in clearly forward-looking prose is a forecast
            # for a *later* period, not a restatement of this one — route it to the
            # guidance period so it binds there (and trips safe-harbor) instead of
            # being asserted against the current period and producing a false conflict
            # ("we expect Q2 revenue of $1.31 billion" must not conflict with filed Q1
            # revenue). The figure keeps its metric; only its period shifts.
            if (
                guidance_period
                and metric != "unidentified"
                and unit is Unit.CURRENCY
                and _has_guidance_context(before, period)
            ):
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
