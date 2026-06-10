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
    # income statement
    "total_revenue": ("total revenues", "total revenue", "total net revenue", "net revenue", "revenues", "revenue", "net sales", "total sales", "sales"),
    "cost_of_revenue": ("cost of revenue", "cost of sales", "cost of goods sold", "cost of products sold"),
    "gross_profit": ("gross profit",),
    "rnd_expense": ("research and development", "r&d expense"),
    "sga_expense": ("selling, general and administrative", "sg&a"),
    "total_costs_and_expenses": ("total operating costs and expenses", "total operating costs", "total costs and expenses", "total operating expenses"),
    "operating_income": ("earnings from operations", "income from operations", "operating income", "operating earnings"),
    "pretax_income": ("earnings before income taxes", "income before income taxes", "pre-tax income", "pretax income"),
    "income_tax_expense": ("provision for income taxes", "income tax provision"),
    "net_income": ("net earnings attributable", "net earnings", "net income attributable", "net income"),
    "gaap_basic_eps": ("basic earnings per share", "basic eps"),
    "gaap_diluted_eps": ("gaap diluted eps", "gaap eps", "gaap diluted earnings per share", "diluted eps", "diluted earnings per share", "earnings per share"),
    "non_gaap_diluted_eps": ("non-gaap diluted eps", "non gaap diluted eps", "adjusted diluted eps", "non-gaap eps", "adjusted eps", "non-gaap diluted earnings per share", "adjusted earnings per share", "adjusted net earnings per share"),
    "basic_shares": ("weighted-average basic shares", "basic weighted-average shares"),
    "diluted_shares": ("weighted-average diluted shares", "diluted weighted-average shares", "diluted shares outstanding"),
    # balance sheet
    "total_assets": ("total assets",),
    "total_liabilities": ("total liabilities",),
    "stockholders_equity": ("shareholders' equity", "stockholders' equity", "total equity"),
    "cash_and_equivalents": ("cash and cash equivalents", "cash and equivalents"),
    "long_term_debt": ("long-term debt",),
    # cash flow
    "operating_cash_flow": ("cash flows from operations", "operating cash flow", "cash flow from operations", "cash provided by operating activities", "cash from operations", "cash flows from operating activities"),
    "investing_cash_flow": ("cash flows from investing activities", "cash used for investing activities"),
    "financing_cash_flow": ("cash flows from financing activities", "cash used for financing activities"),
    "capex": ("capital expenditures", "purchases of property, plant and equipment", "purchases of property and equipment", "capex"),
    "dividends_paid": ("cash dividends paid", "dividends paid"),
    "share_repurchases": ("share repurchases", "repurchases of common stock", "repurchase of common stock", "common stock repurchases", "repurchased", "stock buyback", "buyback"),
    # margins / ratios
    "operating_margin": ("operating margin", "margin from operations"),
    "operating_margin_change_bps": ("operating margin expanded", "operating margin improved", "margin expansion", "basis points"),
    "medical_care_ratio": ("medical care ratio", "medical loss ratio", "care ratio"),
    "operating_cost_ratio": ("operating cost ratio",),
    "return_on_equity": ("return on equity",),
    # payer income-statement lines
    "premium_revenue": ("premium revenues", "premium revenue", "premiums"),
    "medical_costs": ("medical costs", "medical cost trend"),
    "medical_costs_payable": ("medical costs payable",),
    # guidance (period-agnostic targets; the claim period carries the quarter/year)
    "revenue_guidance": ("revenue in the range", "revenue guidance", "expects total revenue", "revenue outlook", "guidance"),
    "eps_guidance": ("eps guidance", "earnings per share guidance", "net earnings outlook", "eps outlook"),
    "adjusted_eps_guidance": ("adjusted eps guidance", "adjusted earnings per share guidance", "adjusted net earnings outlook"),
    # Meridian demo issuer vocabulary
    "cloud_revenue": ("cloud segment revenue", "cloud revenue", "cloud segment", "cloud business"),
    "cloud_growth_yoy": ("cloud growth", "cloud segment revenue grew", "cloud revenue grew", "cloud"),
    # UNH issuer vocabulary: the segment rows as the table renderer labels them
    # ("<section> — <row label>"), plus the bare segment names.
    "unitedhealthcare_revenue": ("revenues — unitedhealthcare", "unitedhealthcare revenue", "unitedhealthcare"),
    "optum_revenue": ("revenues — optum", "optum revenue", "total optum"),
    "optum_health_revenue": ("revenues — optum health", "optum health revenue", "optum health"),
    "optum_insight_revenue": ("revenues — optum insight", "optum insight revenue", "optum insight"),
    "optum_rx_revenue": ("revenues — optum rx", "optum rx revenue", "optum rx"),
    "unitedhealthcare_operating_earnings": ("earnings from operations — unitedhealthcare", "unitedhealthcare earnings from operations"),
    "optum_operating_earnings": ("earnings from operations — optum", "optum earnings from operations"),
    "optum_health_operating_earnings": ("earnings from operations — optum health", "optum health earnings from operations"),
    "optum_insight_operating_earnings": ("earnings from operations — optum insight", "optum insight earnings from operations"),
    "optum_rx_operating_earnings": ("earnings from operations — optum rx", "optum rx earnings from operations"),
}

# Words that, near a percentage, signal a year-over-year growth figure.
_GROWTH_NEAR = re.compile(r"\b(grew|growth|up|increase[d]?|higher|rose|gain|yoy|year[- ]over[- ]year)\b", re.IGNORECASE)

# Forward-looking context that reclassifies a figure as guidance for a later period.
_GUIDANCE_NEAR = re.compile(
    r"\b(expects?|expect|anticipates?|outlook|guidance|we see|looking ahead|"
    r"for the (?:first|second|third|fourth) quarter|full[- ]year|in the range of|range of)\b",
    re.IGNORECASE,
)

# A guidance range stated as a single span: "$1.31 to $1.34 billion", "$1.31–1.34B",
# or a percent range ("22% to 23%") for margin-style guidance.
_RANGE_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:to|through|and|[-–—])\s*\$?\s?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:billion|million|thousand|trillion|bn|mm|[bmkt])?"
    r"|\d{1,3}(?:\.\d+)?\s?%\s*(?:to|through|and|[-–—])\s*\d{1,3}(?:\.\d+)?\s?%",
    re.IGNORECASE,
)

# Cues that attribute a guidance range to its metric and period. The range's own
# label often trails it ("$24.65 to $25.15 per share"), so cues are read from a
# window on both sides, clipped at the first clause break after the range.
_EPS_CUE = re.compile(r"\bper\s+(?:diluted\s+|basic\s+)?share\b|\beps\b|\bearnings\s+per\s+share\b", re.IGNORECASE)
_ADJUSTED_CUE = re.compile(r"\badjusted\b|\bnon-?gaap\b", re.IGNORECASE)
_REVENUE_CUE = re.compile(r"\brevenues?\b|\bsales\b|\btopline\b", re.IGNORECASE)
_MARGIN_CUE = re.compile(r"\bmargin\b", re.IGNORECASE)
_FULL_YEAR_CUE = re.compile(
    r"\bfull[- ]year\b[\s,]*(20\d\d)?|\bfiscal\s+(?:year\s+)?(20\d\d)\b|\bfy\s?(20\d\d)\b",
    re.IGNORECASE,
)
_QUARTER_CUE = re.compile(
    r"\b(first|second|third|fourth)\s+quarter\b(?:[\s,]*(?:of\s+)?(?:fiscal\s+)?(20\d\d))?",
    re.IGNORECASE,
)

# The table renderer annotates each value with its column period — "$99,797
# million (FY2025-Q1)" — so a prior-year comparative binds to *its own* period.
_PERIOD_ANNOT_RE = re.compile(r"^\s*\((FY\d{4}(?:-(?:Q[1-4]|H[12]|9M))?)\)")

# Prose comparatives qualify the figure they trail: "compared to 84.3% last
# year" asserts the *prior-year* value, and must not bind (or conflict) against
# the current period.
_PRIOR_PERIOD_NEAR = re.compile(
    r"\b(?:prior[- ]year|last year|a year ago|year[- ]earlier)\b", re.IGNORECASE
)

# Currency metrics that assert per-share values; a "per share" tail next to a
# figure restricts attribution to these (and never to a dollar aggregate).
_PER_SHARE_TAIL_RE = re.compile(r"^\s*(?:\(FY[^)]{1,12}\)\s*)?per\s+(?:\w+\s+)?share\b", re.IGNORECASE)

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


def _prior_year(period: str | None) -> str | None:
    """'FY2026-Q1' -> 'FY2025-Q1'; 'FY2026' -> 'FY2025'."""
    if not period:
        return None
    m = re.match(r"FY(\d{4})(-.*)?$", period, re.IGNORECASE)
    if not m:
        return None
    return f"FY{int(m.group(1)) - 1}{m.group(2) or ''}"


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
        # Currency metrics that assert per-share values (EPS family, per-share
        # guidance) — the only legal targets for a figure with a "per share" tail.
        self._per_share_ids = {
            spec.id
            for spec in registry.metrics()
            if spec.unit is Unit.CURRENCY
            and ("eps" in spec.id or "per share" in spec.label.lower())
        }

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

    @staticmethod
    def _range_tail(text: str, end: int) -> str:
        """The clause right after a guidance range — where a trailing label lives
        ("$24.65 to $25.15 per share"), clipped before the next clause's subject."""
        tail = text[end : end + 40]
        if (brk := _CLAUSE_BREAK.search(tail)) is not None:
            tail = tail[: brk.start()]
        return tail.lower()

    def _guidance_metric(self, before: str, tail: str, range_text: str) -> tuple[str | None, Confidence]:
        """Attribute a guidance range to its metric from the surrounding clause.

        EPS cues outrank revenue cues (an EPS range often sits in a sentence that
        also says "revenue"); "adjusted"/"non-GAAP" splits the EPS family so two
        same-period ranges (GAAP and adjusted) never collide on one metric. A
        percent range is only asserted when a margin cue is present; an
        uncued dollar range falls back to revenue guidance at low confidence —
        proposed for review, never asserted.
        """
        ctx = f"{before} {tail}"
        if "%" in range_text:
            if _MARGIN_CUE.search(ctx) and "operating_margin_guidance" in self.registry:
                return "operating_margin_guidance", Confidence.HIGH
            return None, Confidence.LOW
        if _EPS_CUE.search(ctx):
            if _ADJUSTED_CUE.search(ctx) and "adjusted_eps_guidance" in self.registry:
                return "adjusted_eps_guidance", Confidence.HIGH
            if "eps_guidance" in self.registry:
                return "eps_guidance", Confidence.HIGH
        if "revenue_guidance" in self.registry:
            confident = bool(_REVENUE_CUE.search(ctx))
            return "revenue_guidance", Confidence.HIGH if confident else Confidence.LOW
        return None, Confidence.LOW

    @staticmethod
    def _guidance_range_period(window: str, doc_period: str | None, fallback: str | None) -> str:
        """The period a guidance range is *for*, read from its own clause.

        "full year 2026" -> FY2026; "the second quarter" -> that quarter (rolling
        into next year when the named quarter is not after the document's own);
        otherwise the next period after the document's, as before.
        """
        doc = re.match(r"FY(\d{4})-Q([1-4])", doc_period or "", re.IGNORECASE)
        doc_year = int(doc.group(1)) if doc else None
        doc_quarter = int(doc.group(2)) if doc else None

        fy = _FULL_YEAR_CUE.search(window)
        if fy:
            year = next((g for g in fy.groups() if g), None)
            if year:
                return f"FY{year}"
            if doc_year:
                return f"FY{doc_year}"
        q = _QUARTER_CUE.search(window)
        if q:
            quarter = _QUARTER_WORDS[q.group(1).lower()]
            if q.group(2):
                return f"FY{q.group(2)}-Q{quarter}"
            if doc_year and doc_quarter:
                year = doc_year + 1 if quarter <= doc_quarter else doc_year
                return f"FY{year}-Q{quarter}"
        return fallback or doc_period or ""

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
        # Growth-like derived kinds assert a *change* and need growth wording
        # nearby; identity kinds (a ratio like the medical care ratio, a sum)
        # are levels and must not be treated as growth claims.
        is_growth = bool(spec and spec.derived_kind in ("yoy_growth", "qoq_growth", "delta_bps"))
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
        # The metric (revenue vs EPS vs adjusted EPS vs margin) and the period
        # (full year vs a named quarter) are read from the surrounding clause,
        # never hardcoded: "full year 2026 ... $24.65 to $25.15 per share" is
        # FY2026 EPS guidance, not next-quarter revenue.
        for m in _RANGE_RE.finditer(text):
            span = (m.start(), m.end())
            window = text[max(0, span[0] - 140) : span[0]].lower()
            if not _GUIDANCE_NEAR.search(window):
                continue
            tail = self._range_tail(text, span[1])
            metric, confidence = self._guidance_metric(window, tail, m.group(0))
            if metric is None:
                continue
            seq += 1
            claims.append(
                FigureClaim(
                    claim_id=f"{document_id}-c{seq}",
                    document_id=document_id,
                    entity=entity,
                    metric=metric,
                    period=self._guidance_range_period(window, period, guidance_period),
                    displayed_text=m.group(0).strip(),
                    span=span,
                    detect_confidence=confidence,
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
            # labels masked so they are not re-attributed here. On a labelled row
            # (a rendered table line, "Label: values…") the window additionally
            # stops at the line start, so the previous row's label can never leak
            # into this row's attribution.
            line_start = text.rfind("\n", 0, cand.span[0]) + 1
            floor = cand.span[0] - 90
            if ":" in text[line_start : cand.span[0]]:
                floor = max(floor, line_start)
            win_start = max(floor, 0)
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

            # A "per share" tail pins the figure to a per-share metric: "net
            # earnings of $6.85 per share" asserts EPS, never the $-aggregate.
            if unit is Unit.CURRENCY and _PER_SHARE_TAIL_RE.match(
                text[cand.span[1] : cand.span[1] + 40]
            ):
                if metric is None or metric not in self._per_share_ids:
                    swap = (
                        "non_gaap_diluted_eps"
                        if _ADJUSTED_CUE.search(before)
                        else "gaap_diluted_eps"
                    )
                    if swap in self.registry:
                        metric, is_growth, trailing_span = swap, False, None

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

            # A rendered table value carries its own column period — "$99,797
            # million (FY2025-Q1)" — which is the most specific signal there is:
            # a prior-year comparative binds to its own period, not the document's.
            annot = _PERIOD_ANNOT_RE.match(text[cand.span[1] : cand.span[1] + 16])
            if annot:
                claim_period = annot.group(1).upper()
            elif (
                "guidance" not in metric
                and _PRIOR_PERIOD_NEAR.search(after)
                and not is_growth
                and not _GROWTH_NEAR.search(before + " " + after)
            ):
                # A trailing "last year"-style qualifier shifts a prose
                # comparative to the prior-year period: "compared to 84.3% last
                # year" asserts last year's value. The window is already clipped
                # at the next figure, so only the comparative shifts — and a
                # growth phrasing ("up 31% from the prior-year period") names
                # its baseline, not the figure's own period, so it never shifts.
                claim_period = _prior_year(claim_period) or claim_period

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
