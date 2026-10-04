"""Dashboard tests (no browser, no network)."""

import json
from pathlib import Path

import pandas as pd
import pytest

import dashboard as dashboard_cli
from crypto_tracker import config, dashboard, dataframe, storage


@pytest.fixture(autouse=True)
def temp_dirs(tmp_path, monkeypatch):
    """Point every path at a throwaway directory."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "data" / "snapshots")
    monkeypatch.setattr(config, "HISTORY_DIR", tmp_path / "data" / "history")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    # The dashboard folder is deliberately NOT created here, so the code under
    # test has to create it.
    monkeypatch.setattr(config, "DASHBOARD_DIR", tmp_path / "data" / "dashboard")
    storage.ensure_directories()
    yield


COINS = [
    ("Bitcoin", "BTC", 100.0, 0.50, 1000.0),
    ("Ethereum", "ETH", 200.0, -1.20, 900.0),
    ("Tether", "USDT", 1.0, 0.00, 800.0),
    ("BNB", "BNB", 50.0, 2.00, 700.0),
    ("XRP", "XRP", 0.50, -0.50, 600.0),
    ("USDC", "USDC", 1.0, 0.10, 500.0),
    ("Solana", "SOL", 25.0, 3.00, 400.0),
    ("TRON", "TRX", 0.20, 0.20, 300.0),
    ("Zcash", "ZEC", 40.0, -2.00, 200.0),
    ("Hyperliquid", "HYPE", 10.0, 1.00, 100.0),
]


def coin_rows(prices):
    """Ten rows for one run, using ``prices`` in place of the default price."""
    rows = []
    for i, (name, symbol, price, change, cap) in enumerate(COINS):
        rows.append(
            {
                "rank": i + 1,
                "name": name,
                "symbol": symbol,
                "price": prices.get(symbol, price),
                "change_24h": change,
                "market_cap": cap,
            }
        )
    return rows


def write_csv(rows, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=config.CSV_COLUMNS).to_csv(path, index=False)
    return path


def write_run(run_id, scraped_at, prices=None, coins=None):
    """Write a snapshot and append the same run to the history file."""
    rows = coins if coins is not None else coin_rows(prices or {})
    stamped = [dict(row, run_id=run_id, scraped_at=scraped_at) for row in rows]

    write_csv(stamped, config.SNAPSHOT_DIR / f"crypto_{run_id}.csv")

    history = []
    if storage.history_path().exists():
        history = pd.read_csv(storage.history_path()).to_dict("records")
    write_csv(history + stamped, storage.history_path())


def two_run_dataset():
    """A realistic two-run history: BTC rises, ETH falls, USDT holds."""
    write_run("20261003_090000", "2026-10-03T09:00:00")
    write_run(
        "20261003_100000",
        "2026-10-03T10:00:00",
        prices={"BTC": 110.0, "ETH": 190.0, "USDT": 1.0},
    )
    return dashboard.load_latest_snapshot(), dashboard.load_history()


def build_current(**kwargs):
    """Build from whatever is already on disk, without writing any new run."""
    return dashboard.build_dashboard_data(
        dashboard.load_latest_snapshot(),
        dashboard.load_history(),
        **kwargs,
    )


def build_data(**kwargs):
    """Build from a freshly written two-run dataset."""
    two_run_dataset()
    return build_current(**kwargs)


def render(**kwargs):
    return dashboard.render_html(build_data(**kwargs))


# --- Loading -----------------------------------------------------------------


def test_load_latest_snapshot_returns_the_newest_file():
    write_run("20261003_090000", "2026-10-03T09:00:00", prices={"BTC": 100.0})
    write_run("20261003_100000", "2026-10-03T10:00:00", prices={"BTC": 110.0})

    snapshot = dashboard.load_latest_snapshot()

    assert len(snapshot) == config.COIN_COUNT
    assert snapshot.iloc[0]["run_id"] == "20261003_100000"
    assert snapshot.iloc[0]["price"] == 110.0


def test_load_latest_snapshot_returns_empty_when_there_are_none():
    snapshot = dashboard.load_latest_snapshot()

    assert snapshot.empty
    assert list(snapshot.columns) == config.CSV_COLUMNS


def test_load_history_tolerates_a_missing_file():
    assert dashboard.load_history().empty


def test_load_history_tolerates_an_empty_file():
    write_csv([], storage.history_path())

    assert dashboard.load_history().empty


# --- The payload -------------------------------------------------------------


def test_build_dashboard_data_contains_every_required_figure():
    data = build_data()

    assert data["total_coins"] == config.COIN_COUNT
    assert data["shown_coins"] == config.COIN_COUNT
    assert data["total_runs"] == 2
    assert len(data["coins"]) == config.COIN_COUNT

    coin = data["coins"][0]
    for field in (
        "rank", "name", "symbol", "price", "change_24h", "market_cap",
        "first_price", "last_price", "change_abs", "change_pct",
        "direction", "runs", "series",
    ):
        assert field in coin, f"missing {field}"


def test_build_dashboard_data_reports_the_latest_scraped_at():
    write_run("20261003_090000", "2026-10-03T09:00:00")
    write_run("20261003_100000", "2026-10-03T10:00:00")

    data = build_current()

    assert data["latest_scraped_at"] == "2026-10-03T10:00:00"


def test_build_dashboard_data_merges_trend_fields():
    data = build_data()
    by_symbol = {coin["symbol"]: coin for coin in data["coins"]}

    btc = by_symbol["BTC"]
    assert btc["first_price"] == 100.0
    assert btc["last_price"] == 110.0
    assert btc["change_abs"] == pytest.approx(10.0)
    assert btc["change_pct"] == pytest.approx(10.0)
    assert btc["direction"] == "up"
    assert btc["runs"] == 2

    eth = by_symbol["ETH"]
    assert eth["direction"] == "down"
    assert eth["change_pct"] == pytest.approx(-5.0)


def test_build_dashboard_data_marks_a_single_run_coin_as_unknown():
    write_run("20261003_100000", "2026-10-03T10:00:00")

    data = build_current()
    coin = data["coins"][0]

    assert coin["runs"] == 1
    assert coin["direction"] == "unknown"
    assert coin["change_pct"] is None
    assert coin["change_abs"] is None


def test_price_series_keeps_every_run_oldest_first():
    snapshot, history = two_run_dataset()

    series = dashboard.price_series(history)

    assert series["BTC"] == [100.0, 110.0]
    assert series["ETH"] == [200.0, 190.0]


# --- Filtering ---------------------------------------------------------------


def test_min_price_filter_hides_the_cheaper_coins():
    data = build_data(min_price=40.0)

    shown = {coin["symbol"] for coin in data["coins"]}
    assert "BTC" in shown
    assert "USDT" not in shown
    assert data["shown_coins"] == len(data["coins"])


def test_max_price_filter():
    data = build_data(max_price=40.0)

    assert all(coin["price"] <= 40.0 for coin in data["coins"])
    assert data["shown_coins"] < config.COIN_COUNT


def test_min_change_filter():
    data = build_data(min_change=1.0)

    assert all(coin["change_24h"] >= 1.0 for coin in data["coins"])


def test_max_change_filter():
    data = build_data(max_change=0.0)

    assert all(coin["change_24h"] <= 0.0 for coin in data["coins"])


def test_combined_filters_are_anded():
    data = build_data(min_price=20.0, min_change=1.0)

    for coin in data["coins"]:
        assert coin["price"] >= 20.0
        assert coin["change_24h"] >= 1.0
    # SOL is $25 and +3%, so it survives; ETH is $190 but -1.2%, so it does not.
    assert "SOL" in {coin["symbol"] for coin in data["coins"]}
    assert "ETH" not in {coin["symbol"] for coin in data["coins"]}


def test_filtering_uses_the_same_semantics_as_the_scraper():
    snapshot, history = two_run_dataset()

    data = dashboard.build_dashboard_data(snapshot, history, min_price=40.0)
    expected = dataframe.apply_filters(snapshot, min_price=40.0)

    assert data["shown_coins"] == len(expected)


def test_filtering_never_modifies_any_csv():
    write_run("20261003_090000", "2026-10-03T09:00:00")
    write_run("20261003_100000", "2026-10-03T10:00:00")
    history_before = storage.history_path().read_text(encoding="utf-8")

    snapshot = dashboard.load_latest_snapshot()
    history = dashboard.load_history()
    dashboard.build_dashboard_data(snapshot, history, min_price=999999)

    assert storage.history_path().read_text(encoding="utf-8") == history_before


def test_page_reports_how_many_of_the_ten_coins_are_shown():
    page = render()
    assert "of 10 coins" in page
    assert 'id="shown-count">10<' in page

    filtered = dashboard.render_html(build_data(min_price=40.0))
    assert "of 10 coins" in filtered


# --- HTML structure ----------------------------------------------------------


def test_render_html_produces_a_complete_document():
    page = render()

    assert page.startswith("<!DOCTYPE html>")
    assert "<html lang=\"en\">" in page
    assert "</html>" in page
    assert "<table id=\"coin-table\">" in page
    assert "<style>" in page and "<script>" in page


def test_render_html_contains_every_required_column_header():
    page = render()

    for header in (
        "Rank", "Name", "Symbol", "Price", "24h %", "Market Cap",
        "First", "Last", "Change $", "Change %", "Trend", "Runs", "History",
    ):
        assert f">{header}</th>" in page, f"missing column header {header}"


def test_render_html_shows_the_latest_update_time():
    page = render()

    assert "2026-10-03T10:00:00" in page
    assert "Latest update" in page


def test_render_html_never_leaks_missing_values():
    """A single-run coin leaves blanks; no NaN/None/undefined may show.

    Only the rendered output is checked: the inline script legitimately
    contains JavaScript's own ``isNaN``.
    """
    write_run("20261003_100000", "2026-10-03T10:00:00")
    page = dashboard.render_html(build_current())
    visible = page.split("<script>")[0]

    assert "NaN" not in visible
    assert "None" not in visible
    assert "undefined" not in visible
    assert ">nan<" not in visible
    assert "nan" not in visible.lower()
    # The blank cells are rendered as a dash / NO DATA instead.
    assert "NO DATA" in visible


def test_render_html_escapes_a_crafted_coin_name():
    hostile = '<script>alert("xss")</script>'
    rows = coin_rows({})
    rows[0]["name"] = hostile
    write_run("20261003_100000", "2026-10-03T10:00:00", coins=rows)

    page = dashboard.render_html(build_current())

    assert hostile not in page
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_render_html_escapes_a_hostile_watchlist_note():
    dashboard.add_watch("BTC", note="<img src=x onerror=alert(1)>")
    page = render()

    assert "<img src=x" not in page
    assert "&lt;img src=x" in page


def test_render_html_escapes_symbols_in_the_chart():
    _, history = two_run_dataset()
    page = dashboard.render_html(build_data())

    # Chart labels come from scraped symbols, so they must be escaped too.
    assert "<title>" in page
    for symbol in ("BTC", "ETH"):
        assert f"<title>{symbol}</title>" in page


# --- SVG charts --------------------------------------------------------------


def test_sparkline_draws_a_polyline_for_a_real_series():
    svg = dashboard.sparkline_svg([100.0, 105.0, 110.0])

    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "<polyline" in svg
    assert svg.count(",") >= 3


def test_sparkline_handles_a_single_point():
    svg = dashboard.sparkline_svg([100.0])

    assert svg.startswith("<svg")
    assert "<circle" in svg
    assert "<polyline" not in svg


def test_sparkline_handles_an_empty_series():
    svg = dashboard.sparkline_svg([])

    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "<polyline" not in svg


def test_sparkline_handles_missing_values():
    svg = dashboard.sparkline_svg([100.0, None, 110.0])

    assert "<polyline" in svg
    assert "nan" not in svg.lower()


def test_sparkline_colours_a_rise_green_and_a_fall_red():
    assert "#16a34a" in dashboard.sparkline_svg([100.0, 110.0])
    assert "#dc2626" in dashboard.sparkline_svg([100.0, 90.0])


def test_normalized_series_rebases_the_first_price_to_100():
    assert dashboard.normalized_series([100.0, 110.0]) == [100.0, 110.0]
    assert dashboard.normalized_series([50.0, 25.0]) == [100.0, 50.0]


def test_normalized_series_handles_empty_and_zero():
    assert dashboard.normalized_series([]) == []
    assert dashboard.normalized_series([0.0, 5.0]) == []


def test_price_chart_plots_each_coin_against_the_same_baseline():
    chart = dashboard.price_chart_svg(
        {"BTC": [100.0, 110.0], "ETH": [200.0, 190.0]}
    )

    assert chart.startswith("<svg")
    assert chart.endswith("</svg>")
    # One polyline per coin.
    assert chart.count("<polyline") == 2
    # The rebased starting level is drawn as the dashed reference line.
    assert "stroke-dasharray" in chart


def test_price_chart_skips_coins_with_a_single_reading():
    chart = dashboard.price_chart_svg(
        {"BTC": [100.0, 110.0], "SOL": [25.0]}
    )

    assert chart.count("<polyline") == 1
    assert "SOL" not in chart


def test_price_chart_explains_itself_when_there_is_no_history():
    chart = dashboard.price_chart_svg({"BTC": [100.0]})

    assert "<svg" in chart
    assert "At least 2 recorded runs" in chart


def test_page_labels_the_chart_as_normalized_and_distinguishes_24h():
    page = render()

    assert "Normalized historical performance" in page
    assert "rebased to 100" in page
    assert "not</b> CoinMarketCap" in page


# --- Portfolio ---------------------------------------------------------------


def test_portfolio_starts_empty_and_missing_file_is_not_an_error():
    assert dashboard.load_portfolio().empty


def test_add_holding_saves_and_reloads():
    dashboard.add_holding("btc", 0.5, 42000, name="Bitcoin")

    portfolio = dashboard.load_portfolio()

    assert len(portfolio) == 1
    assert portfolio.iloc[0]["symbol"] == "BTC"
    assert portfolio.iloc[0]["quantity"] == 0.5
    assert portfolio.iloc[0]["avg_buy_price"] == 42000


def test_add_holding_replaces_an_existing_symbol():
    dashboard.add_holding("BTC", 0.5, 42000)
    dashboard.add_holding("BTC", 2.0, 30000)

    portfolio = dashboard.load_portfolio()

    assert len(portfolio) == 1
    assert portfolio.iloc[0]["quantity"] == 2.0


def test_remove_holding_deletes_the_row():
    dashboard.add_holding("BTC", 0.5, 42000)
    dashboard.add_holding("ETH", 1.0, 2000)

    remaining = dashboard.remove_holding("btc")

    assert remaining["symbol"].tolist() == ["ETH"]
    assert dashboard.load_portfolio()["symbol"].tolist() == ["ETH"]


def test_remove_holding_ignores_an_unknown_symbol():
    dashboard.add_holding("BTC", 0.5, 42000)

    remaining = dashboard.remove_holding("DOGE")

    assert remaining["symbol"].tolist() == ["BTC"]


def test_portfolio_does_not_store_the_current_price():
    dashboard.add_holding("BTC", 0.5, 42000)

    saved = dashboard.load_portfolio()

    assert list(saved.columns) == config.PORTFOLIO_COLUMNS
    assert "current_price" not in saved.columns
    assert "price" not in saved.columns


def test_portfolio_values_come_from_the_latest_snapshot():
    dashboard.add_holding("BTC", 2.0, 50.0)
    data = build_data()

    holding = data["portfolio"][0]
    # Latest BTC price is 110.0, so 2 units are worth 220 against a cost of 100.
    assert holding["current_price"] == 110.0
    assert holding["value"] == pytest.approx(220.0)
    assert holding["cost"] == pytest.approx(100.0)
    assert holding["profit"] == pytest.approx(120.0)
    assert holding["profit_pct"] == pytest.approx(120.0)


def test_portfolio_totals_are_summed():
    dashboard.add_holding("BTC", 2.0, 50.0)
    dashboard.add_holding("ETH", 1.0, 100.0)

    totals = build_data()["portfolio_totals"]

    # 2 x 110 + 1 x 190 = 410, cost 2 x 50 + 100 = 200.
    assert totals["value"] == pytest.approx(410.0)
    assert totals["cost"] == pytest.approx(200.0)
    assert totals["profit"] == pytest.approx(210.0)


def test_unknown_portfolio_symbol_is_listed_but_unpriced():
    dashboard.add_holding("DOGE", 100.0, 0.5)
    data = build_data()

    holding = data["portfolio"][0]

    assert holding["symbol"] == "DOGE"
    assert holding["in_top_10"] is False
    assert holding["current_price"] is None
    assert holding["value"] is None
    assert holding["profit"] is None
    assert data["portfolio_totals"]["unpriced"] == 1
    # The page still renders.
    assert "DOGE" in dashboard.render_html(data)


def test_portfolio_section_appears_on_the_page():
    dashboard.add_holding("BTC", 2.0, 50.0)
    page = render()

    assert "Portfolio" in page
    assert "Unrealised P/L" in page
    assert "not investment advice" in page


def test_page_says_when_there_are_no_holdings():
    assert "No holdings saved yet." in render()


# --- Watchlist ---------------------------------------------------------------


def test_watchlist_starts_empty_and_missing_file_is_not_an_error():
    assert dashboard.load_watchlist().empty


def test_add_watch_saves_and_reloads():
    dashboard.add_watch("sol", note="watching breakout")

    watchlist = dashboard.load_watchlist()

    assert len(watchlist) == 1
    assert watchlist.iloc[0]["symbol"] == "SOL"
    assert watchlist.iloc[0]["note"] == "watching breakout"


def test_remove_watch_deletes_the_row():
    dashboard.add_watch("SOL")
    dashboard.add_watch("BTC")

    remaining = dashboard.remove_watch("sol")

    assert remaining["symbol"].tolist() == ["BTC"]


def test_remove_watch_ignores_an_unknown_symbol():
    dashboard.add_watch("SOL")

    assert dashboard.remove_watch("DOGE")["symbol"].tolist() == ["SOL"]


def test_watchlist_prices_come_from_the_latest_snapshot():
    dashboard.add_watch("BTC")
    entry = build_data()["watchlist"][0]

    assert entry["current_price"] == 110.0
    assert entry["in_top_10"] is True


def test_watchlist_shows_an_unknown_coin_as_not_in_the_top_ten():
    dashboard.add_watch("DOGE")
    entry = build_data()["watchlist"][0]

    assert entry["in_top_10"] is False
    assert entry["current_price"] is None


def test_watchlist_section_appears_on_the_page():
    dashboard.add_watch("BTC")
    page = render()

    assert "Watchlist" in page
    assert "BTC" in page


# --- Writing the files -------------------------------------------------------


def test_write_dashboard_creates_its_directory_and_both_files():
    assert not config.DASHBOARD_DIR.exists()

    html_path, json_path = dashboard.write_dashboard(build_data())

    assert html_path == config.DASHBOARD_DIR / config.DASHBOARD_FILENAME
    assert json_path == config.DASHBOARD_DIR / config.DASHBOARD_DATA_FILENAME
    assert html_path.exists()
    assert json_path.exists()
    assert html_path.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_dashboard_data_json_is_valid_strict_json():
    two_run_dataset()
    _, json_path = dashboard.write_dashboard(build_data())
    text = json_path.read_text(encoding="utf-8")

    def reject(constant):
        raise AssertionError(f"non-standard JSON constant: {constant}")

    payload = json.loads(text, parse_constant=reject)

    assert payload["total_coins"] == config.COIN_COUNT
    assert len(payload["coins"]) == config.COIN_COUNT
    assert payload["coins"][0]["symbol"] == COINS[0][1]
    assert "NaN" not in text


def test_dashboard_data_json_reports_missing_values_as_null():
    write_run("20261003_100000", "2026-10-03T10:00:00")
    _, json_path = dashboard.write_dashboard(build_current())

    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert payload["coins"][0]["change_pct"] is None


def test_the_scraper_csv_columns_are_untouched():
    assert config.CSV_COLUMNS == [
        "rank", "name", "symbol", "price", "change_24h", "market_cap",
        "run_id", "scraped_at",
    ]
    assert config.DATA_COLUMNS == [
        "rank", "name", "symbol", "price", "change_24h", "market_cap",
    ]
    assert config.PORTFOLIO_COLUMNS == [
        "symbol", "name", "quantity", "avg_buy_price", "added_at",
    ]
    assert config.WATCHLIST_COLUMNS == ["symbol", "name", "note", "added_at"]


# --- Command line ------------------------------------------------------------


@pytest.fixture()
def no_browser(monkeypatch):
    """Record browser launches instead of performing them."""
    opened = []
    monkeypatch.setattr(
        dashboard_cli.webbrowser, "open", lambda url: opened.append(url)
    )
    return opened


def test_cli_builds_the_dashboard(no_browser, capsys):
    two_run_dataset()

    assert dashboard_cli.main(["--no-open"]) == 0

    out = capsys.readouterr().out
    assert "Coins shown:      10 of 10" in out
    assert (config.DASHBOARD_DIR / config.DASHBOARD_FILENAME).exists()


def test_cli_no_open_never_launches_a_browser(no_browser):
    two_run_dataset()

    assert dashboard_cli.main(["--no-open"]) == 0

    assert no_browser == []


def test_cli_opens_the_browser_by_default(no_browser):
    two_run_dataset()

    assert dashboard_cli.main([]) == 0

    assert len(no_browser) == 1
    assert no_browser[0].endswith("dashboard.html")


def test_cli_without_any_snapshot_explains_what_to_do(no_browser, capsys):
    assert dashboard_cli.main(["--no-open"]) == 1

    out = capsys.readouterr().out
    assert "No snapshot data found" in out
    assert "python main.py" in out
    assert no_browser == []
    assert not config.DASHBOARD_DIR.exists()


def test_cli_applies_a_price_filter(no_browser, capsys):
    two_run_dataset()

    assert dashboard_cli.main(["--no-open", "--min-price", "40"]) == 0

    assert "Coins shown:" in capsys.readouterr().out


def test_cli_stores_a_holding_without_building_a_page(no_browser, capsys):
    assert dashboard_cli.main(["--add-holding", "BTC:0.5:42000"]) == 0

    portfolio = dashboard.load_portfolio()
    assert portfolio.iloc[0]["symbol"] == "BTC"
    assert portfolio.iloc[0]["quantity"] == 0.5
    assert not (config.DASHBOARD_DIR / config.DASHBOARD_FILENAME).exists()


def test_cli_rejects_a_malformed_holding(no_browser, capsys):
    assert dashboard_cli.main(["--add-holding", "BTC:0.5"]) == 2

    out = capsys.readouterr().out
    assert "SYMBOL:QUANTITY:AVG_PRICE" in out
    assert dashboard.load_portfolio().empty


def test_cli_rejects_a_non_numeric_holding(no_browser, capsys):
    assert dashboard_cli.main(["--add-holding", "BTC:half:42000"]) == 2

    assert "must be numbers" in capsys.readouterr().out
    assert dashboard.load_portfolio().empty


def test_cli_adds_and_removes_watchlist_entries(no_browser):
    assert dashboard_cli.main(["--add-watch", "SOL"]) == 0
    assert dashboard.load_watchlist()["symbol"].tolist() == ["SOL"]

    assert dashboard_cli.main(["--remove-watch", "SOL"]) == 0
    assert dashboard.load_watchlist().empty


def test_cli_removes_a_holding(no_browser):
    dashboard.add_holding("BTC", 1.0, 100.0)

    assert dashboard_cli.main(["--remove-holding", "BTC"]) == 0

    assert dashboard.load_portfolio().empty
