from decimal import Decimal

import pytest

from attest.domain.money import (
    DEFAULT_POLICY,
    QuantityParseError,
    RoundingPolicy,
    Unit,
    parse_quantity,
)


def test_parse_currency_scales_to_base_units():
    assert parse_quantity("$1.24 billion").value == Decimal("1240000000")
    assert parse_quantity("$1,241.3 million").value == Decimal("1241300000")
    assert parse_quantity("$612 million").value == Decimal("612000000")
    assert parse_quantity("$0.87").value == Decimal("0.87")


def test_parse_units():
    assert parse_quantity("$250M").unit == Unit.CURRENCY
    assert parse_quantity("31%").unit == Unit.PERCENT
    assert parse_quantity("50 bps").unit == Unit.BASIS_POINTS


def test_quantum_tracks_written_precision():
    assert parse_quantity("$1.24 billion").quantum == Decimal("10000000")  # 1e7
    assert parse_quantity("$1,241.3 million").quantum == Decimal("100000")  # 1e5
    assert parse_quantity("$0.87").quantum == Decimal("0.01")
    assert parse_quantity("31%").quantum == Decimal("1")


def test_parenthesised_negative():
    assert parse_quantity("($250.0)").value == Decimal("-250.0")


def test_rounding_match_within_policy():
    draft = parse_quantity("$1.24 billion")
    filed = parse_quantity("$1,241.3 million")
    assert draft.matches(filed, DEFAULT_POLICY)
    # symmetric direction: the more precise figure matches the filed precise value
    assert filed.matches(filed, DEFAULT_POLICY)


def test_rounding_mismatch_is_conflict():
    draft = parse_quantity("$1.42 billion")
    filed = parse_quantity("$1,241.3 million")
    assert not draft.matches(filed, DEFAULT_POLICY)


def test_unit_mismatch_never_matches():
    assert not parse_quantity("31%").matches(parse_quantity("$31"), DEFAULT_POLICY)


def test_percent_rounding():
    assert not parse_quantity("31%").matches(parse_quantity("29%"), DEFAULT_POLICY)
    assert parse_quantity("29%").matches(parse_quantity("29%"), DEFAULT_POLICY)


def test_relative_tolerance_opt_in():
    policy = RoundingPolicy(relative_tolerance=Decimal("0.01"))
    draft = parse_quantity("$100")
    filed = parse_quantity("$100.5")
    assert draft.matches(filed, policy)  # within 1%
    assert not draft.matches(filed, DEFAULT_POLICY)  # strict default rejects


def test_unparseable_raises():
    with pytest.raises(QuantityParseError):
        parse_quantity("a lot of money")
    with pytest.raises(QuantityParseError):
        parse_quantity("$1.31 to $1.34 billion")  # a range is not a single quantity


def test_negative_conventions_parse():
    # Leading minus — how the table renderer states a merged split-cell negative.
    assert parse_quantity("-$1,409 million").value == Decimal(-1_409_000_000)
    assert parse_quantity("-1.53").value == Decimal("-1.53")
    assert parse_quantity("-12.4%").value == Decimal("-12.4")
    # Dollar outside the parens — the raw statement-table convention.
    assert parse_quantity("$ (1,409 )").value == Decimal(-1409)
    assert parse_quantity("$(1.53)").value == Decimal("-1.53")
    # Whole-string parens, as before.
    assert parse_quantity("(250.0)").value == Decimal("-250.0")
