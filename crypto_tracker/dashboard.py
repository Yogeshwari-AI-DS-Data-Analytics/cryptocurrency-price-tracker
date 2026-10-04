"""Dashboard: builds a static HTML report from the CSV files.

Reads the newest snapshot plus the history CSV and writes two files:

* ``data/dashboard/dashboard.html``    - the page you open in a browser
* ``data/dashboard/dashboard_data.json`` - the same figures as plain JSON

Nothing here launches Chrome or scrapes anything. The dashboard only reads the
CSV files the scraper already wrote, so it works even when the site or the
driver is unavailable.

Portfolio and watchlist entries live in two small CSV files next to the page,
so there is no database and no server. Only what the user types in is stored;
current prices are derived from the latest snapshot every time the page is
built, which is why they can never go stale.

Charts are inline SVG built here as text, because the project may not depend on
a plotting library.
"""

import html
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import config, dataframe, storage, trends

logger = logging.getLogger(__name__)


class DashboardError(Exception):
    """Raised when the dashboard cannot be built."""


# Colours reused for the chart lines, in order.
_CHART_COLOURS = (
    "#2563eb", "#16a34a", "#dc2626", "#9333ea", "#0891b2",
    "#ea580c", "#4f46e5", "#65a30d", "#db2777", "#0f766e",
)

_DIRECTION_LABELS = {
    "up": "UP",
    "down": "DOWN",
    "flat": "FLAT",
    "unknown": "NO DATA",
}

# CSS class per direction. "NO DATA" coins get the muted "none" style.
_DIRECTION_CLASSES = {
    "up": "up",
    "down": "down",
    "flat": "flat",
    "unknown": "none",
}


# --- Loading the scraper's data ---------------------------------------------


def load_latest_snapshot() -> pd.DataFrame:
    """The newest snapshot CSV, or an empty frame when there are none."""
    path = storage.latest_snapshot()

    if path is None:
        logger.warning("No snapshot CSV found in %s", config.SNAPSHOT_DIR)
        return pd.DataFrame(columns=config.CSV_COLUMNS)

    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise DashboardError(f"Could not read {path}: {exc}") from exc


def load_history() -> pd.DataFrame:
    """The history CSV, or an empty frame when it is missing or unreadable.

    A broken history file must not stop the page being built; it only means the
    trend columns stay blank.
    """
    try:
        return trends.load_history()
    except trends.TrendError as exc:
        logger.warning("History unavailable (%s); trends will be blank", exc)
        return pd.DataFrame(columns=config.CSV_COLUMNS)


# --- Portfolio and watchlist files ------------------------------------------


def portfolio_path() -> Path:
    """Path of the local holdings CSV."""
    return config.DASHBOARD_DIR / config.PORTFOLIO_FILENAME


def watchlist_path() -> Path:
    """Path of the local watchlist CSV."""
    return config.DASHBOARD_DIR / config.WATCHLIST_FILENAME


def _read_optional(path: Path, columns: List[str]) -> pd.DataFrame:
    """Read one of our own CSVs, treating anything unusable as empty."""
    if not path.exists():
        return pd.DataFrame(columns=columns)

    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        logger.warning("Ignoring unreadable %s", path)
        return pd.DataFrame(columns=columns)

    if frame.empty:
        return pd.DataFrame(columns=columns)

    missing = [c for c in columns if c not in frame.columns]
    if missing:
        logger.warning(
            "%s is missing column(s) %s; ignoring it",
            path.name,
            ", ".join(missing),
        )
        return pd.DataFrame(columns=columns)

    return frame[columns]


def _write_csv(frame: pd.DataFrame, path: Path, columns: List[str]) -> Path:
    """Write one of our own CSVs, creating the folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.reindex(columns=columns).to_csv(path, index=False)
    except OSError as exc:
        raise DashboardError(f"Could not write {path}: {exc}") from exc
    return path


def load_portfolio() -> pd.DataFrame:
    """Saved holdings. A missing file means no holdings."""
    return _read_optional(portfolio_path(), config.PORTFOLIO_COLUMNS)


def save_portfolio(frame: pd.DataFrame) -> Path:
    """Store holdings to CSV."""
    path = _write_csv(frame, portfolio_path(), config.PORTFOLIO_COLUMNS)
    logger.info("Portfolio saved: %s (%s holding(s))", path, len(frame))
    return path


def load_watchlist() -> pd.DataFrame:
    """Saved watchlist. A missing file means an empty watchlist."""
    return _read_optional(watchlist_path(), config.WATCHLIST_COLUMNS)


def save_watchlist(frame: pd.DataFrame) -> Path:
    """Store the watchlist to CSV."""
    path = _write_csv(frame, watchlist_path(), config.WATCHLIST_COLUMNS)
    logger.info("Watchlist saved: %s (%s entry/entries)", path, len(frame))
    return path


def add_holding(
    symbol: str,
    quantity: float,
    avg_buy_price: float,
    name: str = "",
    added_at: Optional[str] = None,
) -> pd.DataFrame:
    """Add or replace one holding. Quantity and cost come from the user.

    The current price is deliberately not stored here; it is looked up from the
    latest snapshot every time the dashboard is built.
    """
    symbol = symbol.strip().upper()

    entry = {
        "symbol": symbol,
        "name": name or symbol,
        "quantity": float(quantity),
        "avg_buy_price": float(avg_buy_price),
        "added_at": added_at or datetime.now().isoformat(timespec="seconds"),
    }

    portfolio = load_portfolio()
    kept = (
        portfolio[portfolio["symbol"] != symbol]
        if not portfolio.empty
        else pd.DataFrame(columns=config.PORTFOLIO_COLUMNS)
    )
    if kept.empty:
        updated = pd.DataFrame([entry], columns=config.PORTFOLIO_COLUMNS)
    else:
        updated = pd.concat([kept, pd.DataFrame([entry])], ignore_index=True)
    save_portfolio(updated)

    logger.info(
        "Holding stored: %s x%s at $%s", symbol, quantity, avg_buy_price
    )
    return updated


def remove_holding(symbol: str) -> pd.DataFrame:
    """Delete one holding. Removing something absent is not an error."""
    symbol = symbol.strip().upper()
    portfolio = load_portfolio()

    if portfolio.empty or symbol not in set(portfolio["symbol"]):
        logger.warning("No holding found for %s", symbol)
        return portfolio

    remaining = portfolio[portfolio["symbol"] != symbol].reset_index(drop=True)
    save_portfolio(remaining)
    logger.info("Holding removed: %s", symbol)
    return remaining


def add_watch(
    symbol: str,
    name: str = "",
    note: str = "",
    added_at: Optional[str] = None,
) -> pd.DataFrame:
    """Add or replace one watchlist entry."""
    symbol = symbol.strip().upper()

    entry = {
        "symbol": symbol,
        "name": name or symbol,
        "note": note,
        "added_at": added_at or datetime.now().isoformat(timespec="seconds"),
    }

    watchlist = load_watchlist()
    kept = (
        watchlist[watchlist["symbol"] != symbol]
        if not watchlist.empty
        else pd.DataFrame(columns=config.WATCHLIST_COLUMNS)
    )
    if kept.empty:
        updated = pd.DataFrame([entry], columns=config.WATCHLIST_COLUMNS)
    else:
        updated = pd.concat([kept, pd.DataFrame([entry])], ignore_index=True)
    save_watchlist(updated)

    logger.info("Watchlist entry stored: %s", symbol)
    return updated


def remove_watch(symbol: str) -> pd.DataFrame:
    """Delete one watchlist entry."""
    symbol = symbol.strip().upper()
    watchlist = load_watchlist()

    if watchlist.empty or symbol not in set(watchlist["symbol"]):
        logger.warning("No watchlist entry found for %s", symbol)
        return watchlist

    remaining = watchlist[watchlist["symbol"] != symbol].reset_index(drop=True)
    save_watchlist(remaining)
    logger.info("Watchlist entry removed: %s", symbol)
    return remaining


# --- Assembling the figures --------------------------------------------------


def _number(value) -> Optional[float]:
    """Turn a possibly-missing number into a float or None.

    None keeps the JSON valid and stops "nan" leaking into the page.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def price_series(history: pd.DataFrame) -> Dict[str, List[float]]:
    """symbol -> every price recorded for it, oldest run first."""
    if history.empty:
        return {}

    ordered = history.sort_values(["scraped_at", "rank"])
    series: Dict[str, List[float]] = {}

    for symbol, group in ordered.groupby("symbol"):
        prices = [_number(value) for value in group["price"].tolist()]
        series[symbol] = [price for price in prices if price is not None]

    return series


def build_portfolio_rows(
    snapshot: pd.DataFrame, portfolio: pd.DataFrame
) -> Tuple[List[dict], dict]:
    """Join holdings against the latest snapshot.

    Returns the per-holding rows and a totals dict. A holding for a coin that
    is not in the current Top 10 is still listed, marked as unpriced, rather
    than dropped or crashing the page.
    """
    latest = (
        {row["symbol"]: row for row in snapshot.to_dict("records")}
        if not snapshot.empty
        else {}
    )

    rows = []
    total_value = 0.0
    total_cost = 0.0
    unpriced = 0

    for holding in portfolio.to_dict("records"):
        symbol = holding["symbol"]
        match = latest.get(symbol)

        quantity = _number(holding.get("quantity")) or 0.0
        avg_buy = _number(holding.get("avg_buy_price"))
        current_price = _number(match["price"]) if match else None

        cost = round(quantity * avg_buy, 2) if avg_buy is not None else None
        value = (
            round(quantity * current_price, 2)
            if current_price is not None
            else None
        )

        if cost is None or value is None:
            profit = None
            profit_pct = None
            unpriced += 1
        else:
            profit = round(value - cost, 2)
            profit_pct = round(profit / cost * 100, 2) if cost else None
            total_value += value
            total_cost += cost

        rows.append(
            {
                "symbol": symbol,
                "name": holding.get("name") or symbol,
                "quantity": quantity,
                "avg_buy_price": avg_buy,
                "current_price": current_price,
                "value": value,
                "cost": cost,
                "profit": profit,
                "profit_pct": profit_pct,
                "in_top_10": match is not None,
                "added_at": holding.get("added_at") or "",
            }
        )

    totals = {
        "value": round(total_value, 2),
        "cost": round(total_cost, 2),
        "profit": round(total_value - total_cost, 2),
        "profit_pct": (
            round((total_value - total_cost) / total_cost * 100, 2)
            if total_cost
            else None
        ),
        "unpriced": unpriced,
    }
    return rows, totals


def build_watchlist_rows(
    snapshot: pd.DataFrame, watchlist: pd.DataFrame
) -> List[dict]:
    """Join watchlist entries against the latest snapshot."""
    latest = (
        {row["symbol"]: row for row in snapshot.to_dict("records")}
        if not snapshot.empty
        else {}
    )

    rows = []
    for entry in watchlist.to_dict("records"):
        symbol = entry["symbol"]
        match = latest.get(symbol)
        rows.append(
            {
                "symbol": symbol,
                "name": entry.get("name") or symbol,
                "note": entry.get("note") or "",
                "current_price": _number(match["price"]) if match else None,
                "change_24h": _number(match["change_24h"]) if match else None,
                "in_top_10": match is not None,
                "added_at": entry.get("added_at") or "",
            }
        )
    return rows


def _is_stale(scraped_at: str) -> bool:
    """True when the newest snapshot is older than config.STALE_AFTER_HOURS."""
    if not scraped_at:
        return True
    try:
        moment = datetime.fromisoformat(scraped_at)
    except ValueError:
        return False

    age_hours = (datetime.now() - moment).total_seconds() / 3600
    return age_hours > config.STALE_AFTER_HOURS


def build_dashboard_data(
    snapshot: pd.DataFrame,
    history: pd.DataFrame,
    portfolio: Optional[pd.DataFrame] = None,
    watchlist: Optional[pd.DataFrame] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_change: Optional[float] = None,
    max_change: Optional[float] = None,
) -> dict:
    """Gather every figure the page needs into one JSON-friendly dict.

    The price and 24h filters are applied here with the existing
    dataframe.apply_filters(), so the dashboard filters exactly like main.py
    does. They only change what is displayed; no CSV is touched.
    """
    portfolio = load_portfolio() if portfolio is None else portfolio
    watchlist = load_watchlist() if watchlist is None else watchlist

    total = len(snapshot)

    # Same filter helper, same AND semantics, as the scraper CLI.
    filtered = dataframe.apply_filters(
        snapshot,
        min_price=min_price,
        max_price=max_price,
        min_change=min_change,
        max_change=max_change,
    )

    trend = trends.build_price_trend(history)
    trend_by_symbol = (
        {row["symbol"]: row for row in trend.to_dict("records")}
        if not trend.empty
        else {}
    )
    series = price_series(history)

    coins = []
    for record in filtered.to_dict("records"):
        symbol = record["symbol"]
        row = trend_by_symbol.get(symbol, {})
        coins.append(
            {
                "rank": _number(record.get("rank")),
                "name": record.get("name"),
                "symbol": symbol,
                "price": _number(record.get("price")),
                "change_24h": _number(record.get("change_24h")),
                "market_cap": _number(record.get("market_cap")),
                "first_price": _number(row.get("first_price")),
                "last_price": _number(row.get("last_price")),
                "change_abs": _number(row.get("change_abs")),
                "change_pct": _number(row.get("change_pct")),
                "direction": row.get("direction") or "unknown",
                "runs": int(row.get("samples") or 0),
                "series": series.get(symbol, []),
            }
        )

    if snapshot.empty:
        latest_scraped_at = ""
    else:
        latest_scraped_at = str(snapshot["scraped_at"].max())

    portfolio_rows, portfolio_totals = build_portfolio_rows(snapshot, portfolio)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "latest_scraped_at": latest_scraped_at,
        "stale": _is_stale(latest_scraped_at),
        "stale_after_hours": config.STALE_AFTER_HOURS,
        "total_coins": total,
        "shown_coins": len(coins),
        "total_runs": (
            int(history["run_id"].nunique()) if not history.empty else 0
        ),
        "coins": coins,
        "portfolio": portfolio_rows,
        "portfolio_totals": portfolio_totals,
        "watchlist": build_watchlist_rows(snapshot, watchlist),
        "filters": {
            "min_price": min_price,
            "max_price": max_price,
            "min_change": min_change,
            "max_change": max_change,
        },
    }


# --- Inline SVG charts -------------------------------------------------------


def normalized_series(prices) -> List[float]:
    """Rebase a price list so its first observation equals 100.

    Comparing raw prices on one axis is useless here, because BTC is ~85,000
    and USDC is ~1. Rebasing to 100 puts every coin on a comparable scale.
    """
    points = [
        value
        for value in (_number(p) for p in prices)
        if value is not None
    ]
    if not points or points[0] == 0:
        return []
    return [round(value / points[0] * 100, 4) for value in points]


def sparkline_svg(values, width: int = 110, height: int = 26) -> str:
    """A small inline line chart of one coin's recorded prices."""
    points = [
        value for value in (_number(v) for v in values) if value is not None
    ]

    if not points:
        return (
            f'<svg class="spark" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="no price history">'
            f'<line x1="0" y1="{height / 2:.1f}" x2="{width}" '
            f'y2="{height / 2:.1f}" stroke="#cbd5e1" stroke-width="1" '
            f'stroke-dasharray="3 3" /></svg>'
        )

    if len(points) == 1:
        # One point cannot draw a line, so show a dot on the centre line.
        middle_y = height / 2
        return (
            f'<svg class="spark" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="single price reading">'
            f'<circle cx="{width / 2:.1f}" cy="{middle_y:.1f}" r="2.5" '
            f'fill="#94a3b8" /></svg>'
        )

    low, high = min(points), max(points)
    span = high - low
    padding = 3
    usable = height - 2 * padding
    step = width / (len(points) - 1)

    coordinates = []
    for i, value in enumerate(points):
        x = i * step
        y = (
            padding + usable * (1 - (value - low) / span)
            if span
            else height / 2
        )
        coordinates.append((x, y))

    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in coordinates)
    rising = points[-1] >= points[0]
    colour = "#16a34a" if rising else "#dc2626"

    return (
        f'<svg class="spark" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="recorded price history">'
        f'<polyline fill="none" stroke="{colour}" stroke-width="1.5" '
        f'points="{polyline}" /></svg>'
    )


def price_chart_svg(
    series_by_symbol: Dict[str, List[float]],
    width: int = 780,
    height: int = 280,
) -> str:
    """Compare coins over time, each rebased to 100 at its first run.

    This is run-over-run performance taken from the history CSV. It is NOT
    CoinMarketCap's 24h change, which is a separate single figure per coin.
    """
    lines = []
    for symbol, prices in series_by_symbol.items():
        rebased = normalized_series(prices)
        if len(rebased) >= 2:
            lines.append((symbol, rebased))

    if not lines:
        return (
            f'<svg class="chart" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="not enough history">'
            f'<text x="{width / 2:.0f}" y="{height / 2:.0f}" '
            f'text-anchor="middle" font-size="13" fill="#94a3b8">'
            f'At least 2 recorded runs are needed to compare coins.'
            f'</text></svg>'
        )

    runs = max(len(values) for _, values in lines)
    everything = [value for _, values in lines for value in values]
    low, high = min(everything), max(everything)
    if high == low:
        high = low + 1
    span = high - low

    pad_left, pad_right, pad_top, pad_bottom = 46, 14, 14, 44
    plot_width = width - pad_left - pad_right
    plot_height = height - pad_top - pad_bottom

    def x_at(index):
        return pad_left + plot_width * (index / (runs - 1))

    def y_at(value):
        return pad_top + plot_height * (1 - (value - low) / span)

    parts = [
        f'<svg class="chart" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="normalized price history">'
    ]

    for step in range(5):
        value = low + span * step / 4
        y = y_at(value)
        parts.append(
            f'<line x1="{pad_left}" y1="{y:.1f}" x2="{width - pad_right}" '
            f'y2="{y:.1f}" stroke="#e2e8f0" stroke-width="1" />'
        )
        parts.append(
            f'<text x="{pad_left - 6}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="10" fill="#64748b">{value:.0f}</text>'
        )

    # Dashed reference line at the rebased starting value.
    baseline = y_at(100)
    parts.append(
        f'<line x1="{pad_left}" y1="{baseline:.1f}" x2="{width - pad_right}" '
        f'y2="{baseline:.1f}" stroke="#94a3b8" stroke-width="1" '
        f'stroke-dasharray="4 4" />'
    )

    for i, (symbol, values) in enumerate(lines):
        colour = _CHART_COLOURS[i % len(_CHART_COLOURS)]
        points = " ".join(
            f"{x_at(j):.1f},{y_at(value):.1f}" for j, value in enumerate(values)
        )
        safe_symbol = html.escape(str(symbol))
        parts.append(
            f'<polyline fill="none" stroke="{colour}" stroke-width="2" '
            f'points="{points}"><title>{safe_symbol}</title></polyline>'
        )

    for i in range(runs):
        parts.append(
            f'<text x="{x_at(i):.1f}" y="{height - 14}" text-anchor="middle" '
            f'font-size="10" fill="#64748b">run {i + 1}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


# --- Formatting helpers ------------------------------------------------------


def _money(value) -> str:
    return f"${value:,.2f}" if value is not None else "-"


def _big_money(value) -> str:
    return f"${value:,.0f}" if value is not None else "-"


def _signed_pct(value) -> str:
    return f"{value:+.2f}%" if value is not None else "-"


def _signed_money(value) -> str:
    if value is None:
        return "-"
    return f"-${abs(value):,.2f}" if value < 0 else f"+${value:,.2f}"


def _change_class(value) -> str:
    if value is None:
        return "flat"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def _plain(value) -> str:
    """Escape untrusted text for use in HTML."""
    return html.escape("" if value is None else str(value))


# --- The page ----------------------------------------------------------------

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 28px;
  font-family: "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: #f1f5f9; color: #0f172a;
}
h1 { margin: 0 0 4px; font-size: 24px; }
h2 { margin: 0 0 12px; font-size: 17px; }
.sub { color: #64748b; font-size: 13px; margin-bottom: 18px; }
.card {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
  padding: 18px; margin-bottom: 18px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, .05);
}
.meta { display: flex; flex-wrap: wrap; gap: 18px; font-size: 13px; }
.meta b { color: #0f172a; }
.warn {
  background: #fffbeb; border: 1px solid #fcd34d; color: #92400e;
  border-radius: 8px; padding: 10px 12px; font-size: 13px; margin-bottom: 16px;
}
.controls {
  display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end;
  margin-bottom: 14px;
}
.controls label { display: block; font-size: 12px; color: #475569; }
.controls input {
  padding: 6px 8px; border: 1px solid #cbd5e1; border-radius: 6px;
  font-size: 13px; width: 120px;
}
.count { font-size: 13px; color: #475569; margin-bottom: 10px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td {
  padding: 7px 9px; text-align: right; border-bottom: 1px solid #eef2f7;
  white-space: nowrap;
}
th {
  background: #f8fafc; color: #475569; font-weight: 600;
  font-size: 11px; text-transform: uppercase; letter-spacing: .03em;
}
th.left, td.left { text-align: left; }
.up { color: #15803d; }
.down { color: #b91c1c; }
.flat { color: #64748b; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 600;
}
.badge.up { background: #dcfce7; color: #15803d; }
.badge.down { background: #fee2e2; color: #b91c1c; }
.badge.flat { background: #f1f5f9; color: #64748b; }
.badge.none { background: #f8fafc; color: #94a3b8; }
.spark { vertical-align: middle; }
.chart { max-width: 100%; height: auto; }
.legend {
  display: flex; flex-wrap: wrap; gap: 12px; margin-top: 10px; font-size: 12px;
}
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.legend i { width: 11px; height: 3px; border-radius: 2px; display: inline-block; }
.note { font-size: 12px; color: #64748b; margin-top: 10px; }
.empty { color: #94a3b8; font-size: 13px; padding: 10px 0; }
footer { color: #94a3b8; font-size: 11px; text-align: center; padding: 10px 0; }
"""

_JS = """
(function () {
  var ids = ["min-price", "max-price", "min-change", "max-change"];
  var inputs = {};
  ids.forEach(function (id) { inputs[id] = document.getElementById(id); });
  var counter = document.getElementById("shown-count");
  var rows = Array.prototype.slice.call(
    document.querySelectorAll("#coin-table tbody tr")
  );

  function limit(id) {
    var raw = inputs[id].value.trim();
    if (raw === "") { return null; }
    var parsed = parseFloat(raw);
    return isNaN(parsed) ? null : parsed;
  }

  function apply() {
    var minP = limit("min-price"), maxP = limit("max-price");
    var minC = limit("min-change"), maxC = limit("max-change");
    var shown = 0;

    rows.forEach(function (row) {
      var price = parseFloat(row.getAttribute("data-price"));
      var change = parseFloat(row.getAttribute("data-change"));
      var keep = true;

      if (minP !== null && price < minP) { keep = false; }
      if (maxP !== null && price > maxP) { keep = false; }
      if (minC !== null && change < minC) { keep = false; }
      if (maxC !== null && change > maxC) { keep = false; }

      row.style.display = keep ? "" : "none";
      if (keep) { shown += 1; }
    });

    if (counter) { counter.textContent = shown; }
  }

  ids.forEach(function (id) {
    inputs[id].addEventListener("input", apply);
  });
  apply();
})();
"""

_TABLE_HEADERS = (
    ("Rank", ""),
    ("Name", "left"),
    ("Symbol", "left"),
    ("Price", ""),
    ("24h %", ""),
    ("Market Cap", ""),
    ("First", ""),
    ("Last", ""),
    ("Change $", ""),
    ("Change %", ""),
    ("Trend", ""),
    ("Runs", ""),
    ("History", ""),
)


def _coins_table(data: dict) -> str:
    """The Top 10 table, with a sparkline per coin."""
    head = "".join(
        f'<th class="{cls}">{_plain(label)}</th>'
        for label, cls in _TABLE_HEADERS
    )

    body = []
    for coin in data["coins"]:
        direction = coin["direction"]
        body.append(
            "<tr "
            f'data-price="{coin["price"] if coin["price"] is not None else ""}" '
            f'data-change="{coin["change_24h"] if coin["change_24h"] is not None else ""}">'
            f'<td>{coin["rank"] if coin["rank"] is not None else "-"}</td>'
            f'<td class="left">{_plain(coin["name"])}</td>'
            f'<td class="left">{_plain(coin["symbol"])}</td>'
            f'<td>{_money(coin["price"])}</td>'
            f'<td class="{_change_class(coin["change_24h"])}">'
            f'{_signed_pct(coin["change_24h"])}</td>'
            f'<td>{_big_money(coin["market_cap"])}</td>'
            f'<td>{_money(coin["first_price"])}</td>'
            f'<td>{_money(coin["last_price"])}</td>'
            f'<td class="{_change_class(coin["change_abs"])}">'
            f'{_signed_money(coin["change_abs"])}</td>'
            f'<td class="{_change_class(coin["change_pct"])}">'
            f'{_signed_pct(coin["change_pct"])}</td>'
            f'<td><span class="badge '
            f'{_DIRECTION_CLASSES.get(direction, "none")}">'
            f'{_DIRECTION_LABELS.get(direction, "NO DATA")}</span></td>'
            f'<td>{coin["runs"]}</td>'
            f'<td>{sparkline_svg(coin["series"])}</td>'
            "</tr>"
        )

    if not body:
        body.append(
            '<tr><td colspan="13" class="empty">No coins matched the '
            "filters.</td></tr>"
        )

    return (
        '<table id="coin-table"><thead><tr>'
        f"{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _simple_table(rows: List[dict], headers, empty_message: str) -> str:
    """A small table for the portfolio and watchlist.

    pandas does the HTML here with escaping switched on, because these rows are
    plain text with no embedded markup.
    """
    if not rows:
        return f'<p class="empty">{_plain(empty_message)}</p>'

    frame = pd.DataFrame(rows)
    frame = frame.reindex(columns=list(headers))

    return '<div class="tablewrap">{}</div>'.format(
        frame.to_html(index=False, escape=True, border=0)
    )


def _portfolio_section(data: dict) -> str:
    totals = data["portfolio_totals"]
    headers = [
        "symbol", "name", "quantity", "avg_buy_price",
        "current_price", "value", "profit", "profit_pct",
    ]

    summary = (
        f'<p class="meta">'
        f"<span>Value <b>{_money(totals['value'])}</b></span>"
        f"<span>Cost <b>{_money(totals['cost'])}</b></span>"
        f"<span>Unrealised P/L <b>{_signed_money(totals['profit'])}</b></span>"
        f"<span>P/L % <b>{_signed_pct(totals['profit_pct'])}</b></span>"
        + (
            f"<span>Not in Top {config.COIN_COUNT} "
            f"<b>{totals['unpriced']}</b></span>"
            if totals["unpriced"]
            else ""
        )
        + "</p>"
    )

    return (
        '<div class="card"><h2>Portfolio</h2>'
        f"{summary}"
        f"{_simple_table(data['portfolio'], headers, 'No holdings saved yet.')}"
        '<p class="note">Figures come from your own quantity and average cost. '
        "Current prices are read from the latest snapshot each time this page "
        "is built. This is arithmetic on numbers you entered, not investment "
        "advice.</p></div>"
    )


def _watchlist_section(data: dict) -> str:
    headers = ["symbol", "name", "current_price", "change_24h", "note"]
    return (
        '<div class="card"><h2>Watchlist</h2>'
        f"{_simple_table(data['watchlist'], headers, 'Nothing on the watchlist yet.')}"
        "</div>"
    )


def render_html(data: dict) -> str:
    """Build the whole page as one HTML string."""
    update = data["latest_scraped_at"] or "never"

    stale_notice = ""
    if data["stale"]:
        stale_notice = (
            f'<div class="warn">The newest snapshot is from '
            f'<b>{_plain(update)}</b>, more than '
            f'{data["stale_after_hours"]} hours old. Run '
            f"<b>python main.py</b> for fresh prices.</div>"
        )

    series = {
        coin["symbol"]: coin["series"]
        for coin in data["coins"]
        if len(coin["series"]) >= 2
    }
    chart = price_chart_svg(series)

    legend = "".join(
        f'<span><i style="background:{_CHART_COLOURS[i % len(_CHART_COLOURS)]}">'
        f"</i>{_plain(symbol)}</span>"
        for i, symbol in enumerate(series)
    )

    applied = data["filters"]
    applied_bits = [
        f"{label} {_plain(value)}"
        for label, value in (
            ("min price", applied["min_price"]),
            ("max price", applied["max_price"]),
            ("min 24h %", applied["min_change"]),
            ("max 24h %", applied["max_change"]),
        )
        if value is not None
    ]
    applied_note = (
        f'<p class="note">Saved filters applied from the command line: '
        f'{", ".join(applied_bits)}. Filters only change what is displayed; '
        f"no CSV is modified.</p>"
        if applied_bits
        else '<p class="note">No command-line filters applied.</p>'
    )

    coins_table = _coins_table(data)
    if not data["coins"]:
        coins_table = (
            '<p class="empty">No coins to display.</p>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crypto Top {config.COIN_COUNT} Dashboard</title>
<style>{_CSS}</style>
</head>
<body>
<h1>Cryptocurrency Top {config.COIN_COUNT} Dashboard</h1>
<p class="sub">
  Static report generated {_plain(data["generated_at"])} from the scraper's
  CSV files. No server, no database.
</p>

{stale_notice}

<div class="card">
  <div class="meta">
    <span>Latest update <b>{_plain(update)}</b></span>
    <span>Runs on record <b>{data["total_runs"]}</b></span>
    <span>Coins tracked <b>{data["total_coins"]}</b></span>
  </div>
</div>

<div class="card">
  <h2>Top {config.COIN_COUNT} coins</h2>
  <div class="controls">
    <div><label for="min-price">Min price</label>
      <input id="min-price" type="number" step="any" placeholder="none"></div>
    <div><label for="max-price">Max price</label>
      <input id="max-price" type="number" step="any" placeholder="none"></div>
    <div><label for="min-change">Min 24h %</label>
      <input id="min-change" type="number" step="any" placeholder="none"></div>
    <div><label for="max-change">Max 24h %</label>
      <input id="max-change" type="number" step="any" placeholder="none"></div>
  </div>
  <p class="count">Showing <b id="shown-count">{data["shown_coins"]}</b>
    of {data["total_coins"]} coins</p>
  {applied_note}
  {coins_table}
  <p class="note">The four boxes filter the table instantly in your browser.
  All four must match (AND). Filtering never writes to any CSV.</p>
</div>

<div class="card">
  <h2>Normalized historical performance</h2>
  {chart}
  <div class="legend">{legend}</div>
  <p class="note">
    Each coin's first recorded price is rebased to 100 and the lines are
    compared from there, because raw prices range from about $1 to about
    $85,000 and cannot share an axis. The dashed line is the starting value of
    100. This chart is built from your own recorded runs in
    <b>crypto_history.csv</b> and is <b>not</b> CoinMarketCap's 24h change,
    which is the separate "24h %" column above. With fewer than two runs there
    is nothing to compare.
  </p>
</div>

{_portfolio_section(data)}
{_watchlist_section(data)}

<footer>
  Generated by dashboard.py &middot; figures are indicative and are not
  investment advice.
</footer>
<script>{_JS}</script>
</body>
</html>
"""


def write_dashboard(data: dict) -> Tuple[Path, Path]:
    """Write dashboard.html and dashboard_data.json. Returns both paths."""
    try:
        config.DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
        html_path = config.DASHBOARD_DIR / config.DASHBOARD_FILENAME
        json_path = config.DASHBOARD_DIR / config.DASHBOARD_DATA_FILENAME

        html_path.write_text(render_html(data), encoding="utf-8")
        # allow_nan=False turns any stray NaN into an error rather than writing
        # invalid JSON such as "NaN".
        json_path.write_text(
            json.dumps(data, indent=2, allow_nan=False), encoding="utf-8"
        )
    except (OSError, ValueError) as exc:
        raise DashboardError(f"Could not write the dashboard: {exc}") from exc

    logger.info("Dashboard written: %s", html_path)
    logger.info("Dashboard data written: %s", json_path)
    return html_path, json_path
