"""Structure-aware extraction of financial tables from EDGAR-style HTML.

An 8-K EX-99.1 is mostly *tables* by figure count — the income statement,
balance sheet, cash-flow and segment schedules attached to the prose. Naive
tag-stripping destroys exactly the structure a tie-out needs:

* the table-level scale ("(in millions, except per share data)") is stated
  once in the header, so a flattened ``$ 109,605`` parses six orders of
  magnitude off the filed value — a false **conflict** on a correct number;
* the column headers carry the *period* ("Three Months Ended March 31, 2026
  2025"), so a flattened prior-year comparative binds to the current period —
  another false conflict;
* EDGAR renders the currency symbol and negative parentheses in their own
  cells (``<td>$</td><td>(1,409</td><td>)</td>``), so negatives are invisible
  to a prose-shaped detector — under-detection, the one unacceptable failure;
* row labels leak across rows once cell boundaries become spaces, so a
  segment revenue line inherits the consolidated "Revenues" label above it.

This module re-renders each *financial* table as deterministic prose lines the
rest of the pipeline already understands::

    Revenues — UnitedHealthcare: $81,602 million (FY2026-Q1); $75,414 million (FY2025-Q1)

— the scale word is written out per value, split parentheses become a leading
minus, the column period is annotated next to each value, and section headers
("Revenues:") are chained into the row label so the label that reaches the
extractor is unambiguous. Non-financial (layout) tables are left for the plain
flattening path. Everything here is stdlib and deterministic, like the rest of
the extraction layer.

Calendar-quarter assumption: column dates map onto ``FY{year}-Q{quarter}`` by
calendar month, the same convention the release fetcher's ``report_date``
fallback uses. Off-cycle fiscal issuers resolve correctly only when the header
states the issuer's own fiscal labelling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

_TABLE_TAG_RE = re.compile(r"<table\b[^>]*>|</table\s*>", re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# The table-level scale declaration, stated once near or in the header.
_SCALE_RE = re.compile(
    r"\b(?:amounts\s+)?(?:\$\s*)?in\s+(million|thousand|billion)s?\b", re.IGNORECASE
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_MONTH_WORDS = "|".join(_MONTHS)
_DURATIONS = {"three": 3, "six": 6, "nine": 9, "twelve": 12}

# "Three Months Ended March 31," — the duration banner above the year columns.
_BANNER_RE = re.compile(
    rf"\b(three|six|nine|twelve)\s+months?\s+ended\s+({_MONTH_WORDS})\.?\s+\d{{1,2}}\s*,?",
    re.IGNORECASE,
)
# "March 31, 2026" — a full date, either a duration end or a balance-sheet instant.
_FULL_DATE_RE = re.compile(rf"\b({_MONTH_WORDS})\.?\s+(\d{{1,2}})\s*,?\s+(20\d\d)\b", re.IGNORECASE)
# "First Quarter 2026" / "Q1 2026" — an explicitly labelled quarter column.
_QUARTER_HEAD_RE = re.compile(
    r"\b(?:(first|second|third|fourth)\s+quarter|q([1-4]))\s*,?\s*(?:of\s+)?(?:fiscal\s+)?'?(20\d\d)\b",
    re.IGNORECASE,
)
_QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_BARE_YEAR_RE = re.compile(r"\b(20\d\d)\b")

_BARE_YEAR_CELL_RE = re.compile(r"^(?:19|20)\d\d$")
# A data-bearing numeric cell: optionally $-prefixed / paren-wrapped / %-suffixed.
_NUMERIC_CELL_RE = re.compile(
    r"^(\()?\s*(\$)?\s*\(?\s*([\d,]+(?:\.\d+)?)\s*(\))?\s*(%)?\s*(\))?$"
)
_MARKER_CELLS = {"$", "(", ")", "%", ")%", "%)", "—", "–", "-", ""}

# Rows that must never be coerced to currency by column inference (share counts,
# ratios, day counts, membership) and rows exempt from the table scale (per-share).
_NON_CURRENCY_ROW_RE = re.compile(
    r"\bshares?\b|\bratio\b|\bdays\b|\bmembers?\b|\bpeople\s+served\b|\brate\b",
    re.IGNORECASE,
)
_PER_SHARE_ROW_RE = re.compile(r"\bper\s+(?:\w+\s+)?share\b", re.IGNORECASE)
_FOOTNOTE_TAIL_RE = re.compile(r"\s*\((?:[a-z]|\d{1,2})\)\s*$")
_WS_RE = re.compile(r"[\s\xa0]+")


@dataclass
class _Value:
    """One merged numeric cell, ready to render canonically."""

    number: str
    currency: bool = False
    negative: bool = False
    percent: bool = False


class _RowCollector(HTMLParser):
    """Collects one table's cells as rows of whitespace-collapsed strings."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._close_row()
            self._row = []
        elif tag in ("td", "th"):
            self._close_cell()
            if self._row is None:
                self._row = []
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "table":
            self._close_row()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._row.append(_WS_RE.sub(" ", "".join(self._cell)).strip())
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None:
            self.rows.append(self._row)
        self._row = None


@dataclass(frozen=True)
class _Header:
    periods: tuple[str, ...]
    scale_word: str | None
    lines: tuple[str, ...]  # the header rows, re-rendered as plain text


@dataclass
class _ColumnKinds:
    """Currency-ness learned per value ordinal — EDGAR puts ``$`` on the first
    row of a column and omits it below; the column stays a currency column."""

    currency: dict[int, bool] = field(default_factory=dict)


def render_financial_tables(raw: str) -> str:
    """Replace each financial ``<table>`` in ``raw`` HTML with rendered text.

    Tables are processed innermost-first, so data tables nested inside layout
    tables are recovered. A table that does not look financial is downgraded to
    ``<div>`` markup and left for the ordinary row-per-line flattening. The
    result still contains the surrounding HTML — callers strip it afterwards.
    """
    raw = _COMMENT_RE.sub(" ", raw)
    for _ in range(1000):  # safety bound; EX-99.1s have tens of tables
        region = _innermost_table(raw)
        if region is None:
            return raw
        start, end = region
        rendered = _render_table(raw[start:end], context_before=raw[max(0, start - 300) : start])
        if rendered is None:
            # Layout table: neutralize the table tags, keep the inner markup.
            segment = raw[start:end]
            segment = re.sub(r"<table\b[^>]*>", "<div>", segment, count=1, flags=re.IGNORECASE)
            segment = re.sub(r"</table\s*>$", "</div>", segment, flags=re.IGNORECASE)
            raw = raw[:start] + segment + raw[end:]
        else:
            raw = raw[:start] + "\n" + rendered + "\n" + raw[end:]
    return raw


def _innermost_table(raw: str) -> tuple[int, int] | None:
    """The span of the first table that contains no nested table."""
    stack: list[int] = []
    for m in _TABLE_TAG_RE.finditer(raw):
        if m.group(0)[1] != "/":
            stack.append(m.start())
        elif stack:
            return stack[-1], m.end()
    return None


def _render_table(table_html: str, context_before: str) -> str | None:
    """Render one table's rows as labelled prose lines, or None if not financial."""
    collector = _RowCollector()
    collector.feed(table_html)
    collector.close()
    rows = [row for row in collector.rows if any(cell for cell in row)]
    if len(rows) < 2:
        return None

    first_data = _first_data_row(rows)
    if first_data is None:
        return None
    # The header block is the leading run of rows that carry header signals
    # (scale declaration, duration banner, dates, bare year columns). A plain
    # label row before the data ("Revenues:") is a *section* header and belongs
    # to the data area, where it chains into the row labels below it.
    header_end = 0
    for i in range(first_data):
        if _looks_like_header(rows[i]):
            header_end = i + 1
        else:
            break
    data_rows = rows[header_end:]
    numeric_cells = sum(1 for row in data_rows for cell in row if _is_data_numeric(cell))
    non_empty = sum(1 for row in data_rows for cell in row if cell)
    if numeric_cells < 2 or non_empty == 0 or numeric_cells / non_empty < 0.2:
        return None  # a layout table that happens to contain a number or two

    header = _parse_header(rows[:header_end], _strip_tags(context_before))
    kinds = _ColumnKinds()
    lines: list[str] = list(header.lines)
    section: str | None = None

    for row in data_rows:
        label, values = _split_row(row, kinds)
        label = _FOOTNOTE_TAIL_RE.sub("", label).strip()
        if not values:
            if label:
                section = label.rstrip(":").strip()
                lines.append(label)
            continue
        full_label = f"{section} — {label}" if section and label else (label or section or "")
        per_share = bool(_PER_SHARE_ROW_RE.search(full_label))
        # Bare numbers in a scaled table carry the scale too ("1,000 million"),
        # unless the row is per-share or a count/ratio row the scale never applies to.
        bare_scale = not per_share and not _NON_CURRENCY_ROW_RE.search(full_label)
        rendered = [
            _render_value(v, header.scale_word, per_share=per_share, bare_scale=bare_scale)
            for v in values
        ]
        if header.periods and len(rendered) == len(header.periods):
            rendered = [f"{text} ({period})" for text, period in zip(rendered, header.periods)]
        body = "; ".join(rendered)
        lines.append(f"{full_label}: {body}" if full_label else body)
        # A "Total …" row closes its section by statement convention.
        if section and label.lower().startswith("total"):
            section = None

    return "\n".join(line for line in lines if line.strip())


def _strip_tags(fragment: str) -> str:
    return _WS_RE.sub(" ", re.sub(r"<[^>]+>", " ", fragment))


def _is_data_numeric(cell: str) -> bool:
    """A cell carrying a figure (not a bare header year, not a marker)."""
    if cell in _MARKER_CELLS or _BARE_YEAR_CELL_RE.match(cell):
        return False
    return bool(_NUMERIC_CELL_RE.match(cell))


def _first_data_row(rows: list[list[str]]) -> int | None:
    for i, row in enumerate(rows):
        if any(_is_data_numeric(cell) for cell in row):
            return i
    return None


def _looks_like_header(row: list[str]) -> bool:
    """A row that belongs to the column-header block, not the data area."""
    if not any(row):
        return True
    joined = " ".join(cell for cell in row if cell)
    if (
        _SCALE_RE.search(joined)
        or _BANNER_RE.search(joined)
        or _FULL_DATE_RE.search(joined)
        or _QUARTER_HEAD_RE.search(joined)
    ):
        return True
    # A row of bare year columns ("2026 2025") under a banner.
    cells = [cell for cell in row if cell]
    return bool(cells) and all(_BARE_YEAR_CELL_RE.match(cell) for cell in cells)


# -- header parsing -----------------------------------------------------------


def _parse_header(header_rows: list[list[str]], context_before: str) -> _Header:
    blob = " ".join(cell for row in header_rows for cell in row if cell)
    scale = _SCALE_RE.search(blob) or _SCALE_RE.search(context_before)
    periods = _header_periods(blob)
    lines = tuple(
        " ".join(cell for cell in row if cell) for row in header_rows if any(row)
    )
    return _Header(
        periods=tuple(periods),
        scale_word=scale.group(1).lower() if scale else None,
        lines=lines,
    )


def _quarter_of(month: int) -> int:
    return (month - 1) // 3 + 1


def _duration_period(months: int, end_month: int, year: int) -> str | None:
    """A duration column -> the period key the fact store uses (calendar mapping)."""
    if months == 3:
        return f"FY{year}-Q{_quarter_of(end_month)}"
    if months == 6:
        return f"FY{year}-H{1 if end_month <= 6 else 2}"
    if months == 9:
        return f"FY{year}-9M"
    if months == 12:
        return f"FY{year}"
    return None


def _header_periods(blob: str) -> list[str]:
    """Period keys for each value column, in reading order.

    Handles the three header shapes EDGAR actually uses: an explicit quarter
    label per column ("First Quarter 2026"), a duration banner over bare year
    columns ("Three Months Ended March 31," + "2026 2025"), and full dates per
    column (balance sheets: "March 31, 2026", an instant mapped to its
    calendar quarter). Returns ``[]`` when the header cannot be read — rows
    then render without period annotations rather than guessing.
    """
    explicit = _QUARTER_HEAD_RE.findall(blob)
    if explicit:
        return [
            f"FY{year}-Q{_QUARTER_WORDS[word.lower()] if word else int(digit)}"
            for word, digit, year in explicit
        ]

    banners = [
        (_DURATIONS[m.group(1).lower()], _MONTHS[m.group(2).lower()], m.end())
        for m in _BANNER_RE.finditer(blob)
    ]

    if banners:
        # Every year token after the first banner is a column year — whether it
        # rode inline with the banner's own date ("Three Months Ended March 31,
        # 2026") or sits in the bare years row below ("2026 2025"). When several
        # banners share one years row, the years split evenly between them in
        # reading order ("Three Months ... Six Months ..." + "2026 2025 2026 2025").
        first_end = banners[0][2]
        column_years = [
            int(m.group(1)) for m in _BARE_YEAR_RE.finditer(blob) if m.start(1) >= first_end
        ]
        if not column_years or len(column_years) % len(banners) != 0:
            return []
        per_banner = len(column_years) // len(banners)
        periods: list[str] = []
        for i, (months, end_month, _) in enumerate(banners):
            for year in column_years[i * per_banner : (i + 1) * per_banner]:
                period = _duration_period(months, end_month, year)
                if period is None:
                    return []
                periods.append(period)
        return periods

    dates = [(m.group(1).lower(), int(m.group(3))) for m in _FULL_DATE_RE.finditer(blob)]
    if dates:
        # Balance-sheet style: each full date is an instant; key it to the
        # calendar quarter it closes.
        return [f"FY{year}-Q{_quarter_of(_MONTHS[month])}" for month, year in dates]

    return []


# -- data-row parsing ---------------------------------------------------------


def _split_row(row: list[str], kinds: _ColumnKinds) -> tuple[str, list[_Value]]:
    """Split a row into its label and merged numeric values.

    Walks the cells with a tiny state machine that re-joins what EDGAR split
    apart: a ``$`` marker cell attaches to the next number, an opening-paren
    cell (or in-cell ``(1,409``) marks the value negative until the closing
    cell, and a ``%`` marker cell suffixes the previous value.
    """
    label_parts: list[str] = []
    values: list[_Value] = []
    pending_dollar = False
    open_paren = False
    seen_value = False

    for cell in row:
        if cell == "$":
            pending_dollar = True
            continue
        if cell == "(":
            open_paren = True
            continue
        if cell in (")", ")%", "%)"):
            if values:
                values[-1].negative = values[-1].negative or open_paren or cell != ")"
                if "%" in cell:
                    values[-1].percent = True
                if cell == ")" and open_paren:
                    values[-1].negative = True
            open_paren = False
            continue
        if cell == "%":
            if values:
                values[-1].percent = True
            continue
        if cell in ("—", "–", "-", ""):
            pending_dollar = False
            open_paren = False
            continue
        m = _NUMERIC_CELL_RE.match(cell)
        if m and not _BARE_YEAR_CELL_RE.match(cell):
            lead_open, dollar, number, close, pct, close2 = m.groups()
            in_cell_open = bool(lead_open) or "(" in cell.split(number)[0]
            value = _Value(
                number=number,
                currency=pending_dollar or bool(dollar),
                negative=open_paren or in_cell_open,
                percent=bool(pct),
            )
            if (lead_open or in_cell_open) and (close or close2):
                value.negative = True
            ordinal = len(values)
            row_label = " ".join(label_parts)
            if value.currency:
                kinds.currency[ordinal] = True
            elif (
                kinds.currency.get(ordinal)
                and not value.percent
                and not _NON_CURRENCY_ROW_RE.search(row_label)
            ):
                value.currency = True
            values.append(value)
            seen_value = True
            pending_dollar = False
            if close or close2:
                open_paren = False
            continue
        # A text cell: part of the label before any value, noise after.
        if not seen_value:
            label_parts.append(cell)
        pending_dollar = False

    return " ".join(part for part in label_parts if part).strip(), values


def _render_value(
    value: _Value, scale_word: str | None, *, per_share: bool, bare_scale: bool
) -> str:
    """Canonical text for one merged cell: sign, symbol, scale written out."""
    sign = "-" if value.negative else ""
    if value.percent:
        return f"{sign}{value.number}%"
    if value.currency:
        text = f"{sign}${value.number}"
        if scale_word and not per_share:
            text += f" {scale_word}"
        return text
    if scale_word and bare_scale:
        return f"{sign}{value.number} {scale_word}"
    return f"{sign}{value.number}"
