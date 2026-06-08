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
from attest.domain.verdicts import UNIDENTIFIED_METRIC, FigureClaim
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
    #
    # The per-share line is also routinely written as "net income per (diluted) share"
    # rather than "EPS". Those phrasings carry the substring "net income", so without
    # an explicit per-share alias the figure binds to the absolute ``net_income``
    # metric instead — a $0.61 per-share number compared to a billion-dollar fact,
    # which fires a false *conflict*. The per-share aliases below win by sitting
    # nearer the figure (and, on a tie, by being the longer match), so "net income
    # per share" resolves to EPS while a bare "net income" still resolves to the level.
    # The "non-gaap"/"adjusted" qualifier must steer to the non-GAAP line, never the
    # GAAP one, so every GAAP earnings/per-share phrasing has a non-GAAP twin below.
    "gaap_diluted_eps": (
        "gaap diluted eps", "gaap eps",
        "gaap diluted earnings per share", "gaap diluted loss per share",
        "gaap net income per diluted share", "gaap net income per share",
        "diluted eps", "diluted earnings per share", "diluted loss per share",
        "net income per diluted share", "net income per share",
        "per diluted share", "per share",
        "diluted net income per share", "net loss per diluted share",
        "earnings per share", "loss per share", "net loss per share",
    ),
    "non_gaap_diluted_eps": (
        "non-gaap diluted eps", "non gaap diluted eps", "adjusted diluted eps",
        "non-gaap eps", "adjusted eps",
        "non-gaap diluted earnings per share", "non-gaap diluted loss per share",
        "adjusted diluted earnings per share", "adjusted diluted loss per share",
        "non-gaap earnings per share", "non gaap earnings per share", "adjusted earnings per share",
        "non-gaap net income per diluted share", "non gaap net income per diluted share",
        "non-gaap net income per share", "non gaap net income per share",
        "adjusted net income per diluted share", "adjusted net income per share",
        "non-gaap diluted net income per share", "adjusted diluted net income per share",
        "non-gaap loss per share", "adjusted loss per share",
        "non-gaap net loss per share", "adjusted net loss per share",
    ),
    "operating_cash_flow": ("operating cash flow", "cash flow from operations", "cash provided by operating activities", "cash from operations"),
    # Free cash flow = operating cash flow − capex. Bound to the full phrase so it is
    # never swallowed by the "cash flow" in the operating-cash-flow aliases.
    "free_cash_flow": ("free cash flow", "fcf"),
    "capex": ("capital expenditures", "capital expenditure", "capex", "purchases of property and equipment", "purchases of property, plant and equipment"),
    "net_income": ("net income", "net earnings"),
    "operating_income": ("operating income", "income from operations", "operating profit"),
    "gross_profit": ("gross profit",),
    # Total RPO only — bound to the full phrase so the call's "current RPO" (a
    # different, smaller figure) isn't misattributed to the total.
    "total_rpo": ("remaining performance obligation", "remaining performance obligations"),
    "cash_and_equivalents": ("cash and cash equivalents", "cash and equivalents"),
    "share_repurchases": ("share repurchases", "repurchases of common stock", "repurchase of common stock", "repurchased", "stock buyback", "buyback"),
    "operating_margin": ("operating margin", "margin from operations"),
    "gross_margin": ("gross margin", "gross profit margin"),
    # Non-GAAP margins must steer to their own (consistency-only) metric, not the
    # GAAP-derived ratio — the longer alias wins the substring tie over "gross/operating
    # margin", so a recompute against GAAP operands never fires a false conflict.
    "non_gaap_gross_margin": ("non-gaap gross margin", "non gaap gross margin", "adjusted gross margin"),
    "non_gaap_operating_margin": ("non-gaap operating margin", "non gaap operating margin", "adjusted operating margin"),
    "billings": ("billings", "total billings", "calculated billings"),
    # A basis-points figure is the margin *change*. "basis points" alone can't anchor
    # it (those words sit inside the figure span, not its context), so attribute on the
    # margin verb that precedes it — in any inflection a real draft uses ("expanded",
    # "expanding", "contracted", "declined", "improved", "widened", "narrowed").
    "operating_margin_change_bps": (
        "operating margin expanded", "operating margin expanding", "operating margin improved",
        "operating margin improving", "operating margin contracted", "operating margin contracting",
        "operating margin declined", "operating margin widened", "operating margin narrowed",
        "margin expanded", "margin expanding", "margin contracted", "margin improved",
        "margin expansion", "margin contraction", "basis points",
    ),
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

# Derived kinds that express a *change* (so a percent needs a growth word nearby to
# read as a change, not a level). A ratio level like a margin is deliberately not here.
_GROWTH_KINDS = frozenset({"yoy_growth", "qoq_growth", "delta_bps"})

# Where a figure's own trailing label ends and the next clause begins. A trailing
# label is a tight prepositional tail ("$338 million of operating cash flow"); once
# we hit punctuation or a conjunction we are into the next clause's subject and must
# not borrow it — the same caution the 4-char lookahead enforced, now wide enough to
# actually reach a trailing label.
_CLAUSE_BREAK = re.compile(r"[.,;:\n]|\b(?:and|but|while|which|whereas)\b", re.IGNORECASE)

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

_FYQ_CONTEXT_RE = re.compile(
    r"\b(?:fiscal\s+)?(?:(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter|q\s*([1-4]))"
    r"(?:\s+of)?(?:\s+fiscal)?(?:\s+fy)?[\s,'-]+(?:fy\s*)?(20\d\d)\b",
    re.IGNORECASE,
)
_FY_CONTEXT_RE = re.compile(r"\b(?:fiscal\s+year|full[- ]year|fy)\s*(20\d\d)\b", re.IGNORECASE)
_COMPARATIVE_PRIOR_RE = re.compile(r"\b(compared\s+(?:with|to)|versus|vs\.?)\b", re.IGNORECASE)
_UNSUPPORTED_TOTAL_REVENUE_RE = re.compile(
    r"\b(?:product|products|service|services|subscription|subscriptions|support|license|licenses)\s+revenue\b",
    re.IGNORECASE,
)
_NON_GAAP_RE = re.compile(r"\b(?:non[- ]gaap|adjusted)\b", re.IGNORECASE)
_GAAP_ONLY_METRICS = frozenset({"net_income", "operating_income", "gross_profit", "gross_margin", "operating_margin"})


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


def _prior_year_period(period: str | None) -> str | None:
    if not period:
        return None
    m = re.match(r"FY(\d{4})-Q([1-4])", period, re.IGNORECASE)
    if not m:
        return None
    return f"FY{int(m.group(1)) - 1}-Q{m.group(2)}"


def _period_from_context(window: str) -> str | None:
    """Read an explicit local fiscal period from text near one figure."""
    if (m := _PERIOD_RE.search(window)) is not None:
        return m.group(0).upper()
    if (m := _FYQ_CONTEXT_RE.search(window)) is not None:
        ord_word, qnum, year = m.group(1), m.group(2), m.group(3)
        quarter = _ORD_TO_Q[ord_word.lower()] if ord_word else int(qnum)
        return f"FY{year}-Q{quarter}"
    return None


def _full_year_from_context(window: str) -> str | None:
    if (m := _FY_CONTEXT_RE.search(window)) is not None:
        return f"FY{m.group(1)}"
    return None


def _sentence_window(text: str, start: int, end: int) -> str:
    """The local sentence/line around a figure, wide enough for section guidance.

    The leading/trailing attribution windows are intentionally tight, but historical
    earnings documents often put period qualifiers at sentence level ("compared with
    ... for fiscal third quarter 2025" or a heading-like "For fiscal year 2026").
    This window is used only to choose period / demote unsafe bindings, never to
    assert a metric label by itself.
    """
    lo = max(text.rfind(".", 0, start), text.rfind("\n", 0, start), text.rfind(";", 0, start)) + 1
    stops = [p for p in (text.find(".", end), text.find("\n", end), text.find(";", end)) if p != -1]
    hi = min(stops) if stops else min(len(text), end + 240)
    return text[lo:hi]


def _is_unsupported_metric_binding(metric: str, context: str) -> bool:
    """True when a nearby alias is real, but the filed metric would be the wrong source.

    This is the production-safety valve for historical prose: never bind a product
    revenue, services revenue, non-GAAP income/margin, or other unsupported submetric
    to a GAAP total just because it contains the word "revenue"/"income"/"margin".
    It is better to show an honest untraced figure than a high-confidence false
    conflict against an unrelated SEC tag.
    """
    if metric == "total_revenue" and _UNSUPPORTED_TOTAL_REVENUE_RE.search(context):
        return True
    if metric in _GAAP_ONLY_METRICS and _NON_GAAP_RE.search(context):
        return True
    if metric in {"gross_margin", "operating_margin"} and re.search(
        r"\b(?:product|products|service|services|subscription|subscriptions|support|total)\s+gross\s+margin\b",
        context,
        re.IGNORECASE,
    ):
        return True
    return False

# A guidance range stated as a single span: "$1.31 to $1.34 billion", "$1.31–1.34B",
# "$2.68 billion to $2.71 billion" (the scale word repeated on each end — the phrasing
# most real releases use), or — as transcripts phrase it — symbol-free "1.31 to 1.34
# billion". A scale word is allowed after the *first* number, but only when the joiner
# is an unambiguous range word ("to"/"through"/dash): "and" also joins an enumeration
# of two different metrics ("$1.2 billion and $0.6 billion"), so a scale word before
# "and" would mis-read that as one range. When no "$" is present a trailing scale word
# is required so a plain year span ("2025 to 2026") is not mistaken for money, and a
# trailing count noun ("400 to 500 million users") is excluded so an operating-metric
# range is not read as currency guidance.
_NUM = r"\d[\d,]*(?:\.\d+)?"
_SCALE_WORD = r"(?:billion|million|thousand|trillion|bn|mm)"
_SCALE_TRAIL = r"(?:billion|million|thousand|trillion|bn|mm|[bmkt])"
_RANGE_CONN = r"(?:to|through|[-–—])"  # unambiguous range joiners (scale-before-ok)
_ANY_CONN = r"(?:to|through|and|[-–—])"
_NOUN_TAIL = rf"\b(?!\s*(?:{NOUN_WORDS})\b)"
_RANGE_RE = re.compile(
    # currency, scale word on the first number too (range joiner only)
    rf"\$\s?{_NUM}\s*{_SCALE_WORD}\s*{_RANGE_CONN}\s*\$?\s?{_NUM}\s*{_SCALE_TRAIL}?"
    # currency, any joiner incl. "and", scale word on the last number only
    rf"|\$\s?{_NUM}\s*{_ANY_CONN}\s*\$?\s?{_NUM}\s*{_SCALE_TRAIL}?"
    # symbol-free, scale on the first number too (range joiner only); trailing scale required
    rf"|\b{_NUM}\s*{_SCALE_WORD}\s*{_RANGE_CONN}\s*{_NUM}\s*{_SCALE_WORD}{_NOUN_TAIL}"
    # symbol-free, any joiner; trailing scale required
    rf"|\b{_NUM}\s*{_ANY_CONN}\s*{_NUM}\s*{_SCALE_WORD}{_NOUN_TAIL}",
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
# issuer: "the company (NASDAQ: TICKER)", "(NYSE American: ABC.A)". Anchoring
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

        e.g. an ingested fact for ``ATLS:Cloud`` teaches us that the word "cloud"
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
        # Only *change* metrics (YoY/QoQ growth, bps delta) demand a growth word
        # nearby; a ratio level like a margin is not a change, so it must not be
        # demoted to low confidence for lacking one.
        is_growth = bool(spec and spec.derived_kind in _GROWTH_KINDS)
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
        # Trailing labels a previous figure already claimed for itself. We blank just
        # these spans out of later figures' preceding windows — without them, the
        # off-by-one in "$338M of operating cash flow and returned $250M" gives both
        # figures the cash-flow label. Masking (rather than only bounding the window)
        # keeps the rest of the leading context intact, e.g. the "cloud" the very next
        # "31%" needs to read as cloud growth.
        claimed_labels: list[tuple[int, int]] = []
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
            before_chars = list(text[lo : cand.span[0]].lower())
            for ls, le in claimed_labels:  # mask prior figures' trailing labels
                a, b = max(ls, lo), min(le, cand.span[0])
                for k in range(a - lo, b - lo):
                    before_chars[k] = " "
            before = "".join(before_chars)
            # Following window: a short trailing tail, stopped at the next figure and at
            # the first clause boundary, so we read this figure's own trailing label
            # ("$338 million of operating cash flow") but never the next clause's subject.
            after_end = cand.span[1] + 60
            if idx + 1 < len(all_cands):
                after_end = min(after_end, all_cands[idx + 1].span[0])
            after = text[cand.span[1] : after_end]
            if (brk := _CLAUSE_BREAK.search(after)) is not None:
                after = after[: brk.start()]
            after = after.lower()
            sent = _sentence_window(text, cand.span[0], cand.span[1]).lower()
            wide_before = text[max(0, cand.span[0] - 420) : cand.span[0]].lower()
            context = f"{before} {after} {sent}"
            period_context = f"{context} {wide_before}"
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
                metric = UNIDENTIFIED_METRIC
                confidence = Confidence.LOW
            elif metric == "gaap_diluted_eps" and _NON_GAAP_RE.search(before + " " + after + " " + wide_before[-180:]):
                # "non-GAAP net income per share" contains the generic "per share"
                # EPS cue. Keep it out of the GAAP filed-source comparison unless a
                # tenant has ingested a non-GAAP reference for that period.
                metric = "non_gaap_diluted_eps"
            elif _is_unsupported_metric_binding(metric, context):
                # The label matched a real word but not a filed metric we can safely
                # bind (e.g. "product revenue" vs total revenue, or non-GAAP income
                # vs GAAP net income). Route to the untraced/review lane rather than
                # manufacturing a false SEC conflict.
                metric = UNIDENTIFIED_METRIC
                confidence = Confidence.LOW

            if _COMPARATIVE_PRIOR_RE.search(before) and (prior := _prior_year_period(period)):
                # Comparatives in earnings prose are usually "compared with" the
                # same quarter last year. This takes precedence over a current-period
                # phrase earlier in the same sentence.
                claim_period = prior
            elif (explicit_period := _period_from_context(sent)):
                claim_period = explicit_period

            # A currency figure sitting in clearly forward-looking prose is a forecast
            # for a *later* period, not a restatement of this one — route it away from
            # the current period so it never compares against current-quarter filed
            # sources. The wider context catches heading-style outlook text followed
            # by bullet points on subsequent lines.
            if metric != UNIDENTIFIED_METRIC and _has_guidance_context(period_context, period):
                claim_period = _period_from_context(period_context) or _full_year_from_context(period_context) or guidance_period or claim_period

            # Record a trailing label this figure just consumed so later figures don't
            # re-attribute it — but only when it sits closer to this figure than to the
            # next, otherwise it is the next figure's leading label, left for it.
            if trailing_span is not None:
                start_gap, end_off = trailing_span
                label_start = cand.span[1] + start_gap
                label_end = cand.span[1] + end_off
                next_start = (
                    all_cands[idx + 1].span[0] if idx + 1 < len(all_cands) else len(text)
                )
                if start_gap <= (next_start - label_end):
                    claimed_labels.append((label_start, label_end))

            displayed_text = cand.text
            if (
                metric in {"gaap_diluted_eps", "net_income", "operating_income"}
                and re.search(r"\bloss(?:es)?\b", before + " " + after)
                and not displayed_text.lstrip().startswith("-")
                and "(" not in displayed_text
            ):
                # Earnings prose often writes "loss per share of $0.22" without
                # parentheses. Normalize the claim to the signed economic value so it
                # can tie to XBRL's negative fact while preserving the span.
                displayed_text = "-" + displayed_text

            seq += 1
            claims.append(
                FigureClaim(
                    claim_id=f"{document_id}-c{seq}",
                    document_id=document_id,
                    entity=entity_for,
                    metric=metric,
                    period=claim_period,
                    displayed_text=displayed_text,
                    span=cand.span,
                    detect_confidence=confidence,
                )
            )

        claims.sort(key=lambda c: (c.span or (0, 0))[0])
        return tuple(claims)
