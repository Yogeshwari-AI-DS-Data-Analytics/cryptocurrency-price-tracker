"""Unit tests for the pure parsing helpers (no browser required)."""

import pytest

from crypto_tracker import parser


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("$64,123.45", 64123.45),
        ("$0.00001234", 0.00001234),
        ("$1.23", 1.23),
        ("1234.5", 1234.5),
        ("$1,234,567.89", 1234567.89),
        ("1.234,56", 1234.56),
        ("$1.5B", 1.5e9),
        ("N/A", None),
        ("", None),
        (None, None),
        ("-", None),
    ],
)
def test_parse_price(raw, expected):
    assert parser.parse_price(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+2.45%", 2.45),
        ("-0.87%", -0.87),
        ("2.45 %", 2.45),
        ("-0.87 %", -0.87),
        ("\u22120.87%", -0.87),
        ("+12.00%", 12.0),
        ("N/A", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_percent(raw, expected):
    assert parser.parse_percent(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("$1.23T", 1.23e12),
        ("$2,375.4B", 2.3754e12),
        ("$845,120", 845120.0),
        ("$56.1B", 5.61e10),
        ("N/A", None),
    ],
)
def test_parse_market_cap(raw, expected):
    assert parser.parse_market_cap(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw, expected",
    [("1", 1), ("#7", 7), ("10", 10), ("Sponsored", None), ("", None), ("Explore assets", None)],
)
def test_parse_rank(raw, expected):
    assert parser.parse_rank(raw) == expected


def test_parse_symbol_and_name():
    assert parser.parse_symbol("\nBTC\n") == "BTC"
    assert parser.parse_symbol("eth") == "ETH"
    assert parser.parse_symbol("N/A") is None
    assert parser.parse_name("  Bitcoin ") == "Bitcoin"
    assert parser.parse_name("") is None