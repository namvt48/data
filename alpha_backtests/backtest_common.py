#!/usr/bin/env python3
"""Shared utilities for the alpha backtest scripts.

Default data source is the local parquet directory:
../data/binance_futures_5m.
"""

from __future__ import annotations

import base64
import csv
import html as html_lib
import json
import math
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT if (ROOT / "data").exists() else ROOT.parent
DATA_DIR = Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data" / "binance_futures_5m"))
SYMBOLS_CSV = PROJECT_ROOT / "binance_futures_active_symbols.csv"
LEVERAGE_JSON = PROJECT_ROOT / "binance_futures_leverage.json"


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


START = parse_utc(os.getenv("START", "2024-01-01T00:00:00+00:00"))
END = parse_utc(os.getenv("END", "2025-01-01T00:00:00+00:00"))

CAPITAL = float(os.getenv("CAPITAL", "10000"))
SIZE = float(os.getenv("SIZE", "1000"))
MIN_SIZE = float(os.getenv("MIN_SIZE", "500"))

FEE_RATE_RAW = float(os.getenv("FEE_RATE_RAW", "0.0007"))
FEE_DISCOUNT = float(os.getenv("FEE_DISCOUNT", "0.49"))
FEE_RATE = FEE_RATE_RAW * (1.0 - FEE_DISCOUNT)

MAX_WORKERS = int(os.getenv("MAX_WORKERS", "16"))
DATA_SOURCE = os.getenv("DATA_SOURCE", "local").lower()

TF_SPECS = {
    "5m": {
        "id": "5m",
        "label": "M5",
        "mins": 5,
        "pandas_freq": "5min",
        "sql_fn": "toStartOfInterval(open_time, INTERVAL 5 MINUTE)",
    },
    "15m": {
        "id": "15m",
        "label": "M15",
        "mins": 15,
        "pandas_freq": "15min",
        "sql_fn": "toStartOfInterval(open_time, INTERVAL 15 MINUTE)",
    },
    "1h": {
        "id": "1h",
        "label": "H1",
        "mins": 60,
        "pandas_freq": "1h",
        "sql_fn": "toStartOfHour(open_time)",
    },
    "4h": {
        "id": "4h",
        "label": "H4",
        "mins": 240,
        "pandas_freq": "4h",
        "sql_fn": "toStartOfInterval(open_time, INTERVAL 4 HOUR)",
    },
}

_CH_HOST = os.getenv("CLICKHOUSE_HOST", "194.163.187.250")
_CH_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
_CH_DB = os.getenv("CLICKHOUSE_DATABASE", "quant")
_CH_USER = os.getenv("CLICKHOUSE_USER", "quant_user")
_CH_PASS = os.getenv("CLICKHOUSE_PASSWORD", "quant_staging_123")


def _ch_query(sql: str, timeout: int = 120) -> str:
    params = urllib.parse.urlencode({"database": _CH_DB, "default_format": "JSONEachRow"})
    url = f"http://{_CH_HOST}:{_CH_PORT}/?{params}"
    token = base64.b64encode(f"{_CH_USER}:{_CH_PASS}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=sql.encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def load_symbols() -> list[str]:
    raw = os.getenv("SYMBOLS", "").strip()
    if raw:
        return [s.strip().upper() for s in raw.split(",") if s.strip()]

    if DATA_DIR.exists():
        symbols = sorted(p.stem for p in DATA_DIR.glob("*.parquet"))
        if symbols:
            return symbols

    if SYMBOLS_CSV.exists():
        with SYMBOLS_CSV.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return [row["symbol"].strip().strip('"') for row in reader if row.get("symbol")]

    if LEVERAGE_JSON.exists():
        with LEVERAGE_JSON.open(encoding="utf-8") as f:
            rows = json.load(f)
        return [row["symbol"] for row in rows if row.get("symbol")]

    return []


def _local_parquet_path(symbol: str) -> Path:
    return DATA_DIR / f"{symbol}.parquet"


def ensure_local_data_available() -> None:
    if DATA_SOURCE not in ("local", "auto"):
        return
    if not DATA_DIR.exists():
        raise FileNotFoundError(f"Local data directory not found: {DATA_DIR}")
    if not any(DATA_DIR.glob("*.parquet")):
        raise FileNotFoundError(f"No parquet files found in local data directory: {DATA_DIR}")


def _fetch_local(symbol: str, tf_id: str, warmup_bars: int) -> list[dict]:
    import pandas as pd

    spec = TF_SPECS[tf_id]
    path = _local_parquet_path(symbol)
    if not path.exists():
        return []

    warmup_start = START - timedelta(minutes=warmup_bars * spec["mins"] + spec["mins"] * 2)
    df = pd.read_parquet(path)
    if df.empty:
        return []

    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df[(df["time"] >= warmup_start) & (df["time"] < END)].copy()
    if df.empty:
        return []

    df = df.sort_values("time").set_index("time")
    if tf_id != "5m":
        df = (
            df.resample(spec["pandas_freq"], label="left", closed="left")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna()
        )

    out = []
    for ts, row in df.iterrows():
        out.append(
            {
                "time": ts.to_pydatetime().astimezone(timezone.utc),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0)),
            }
        )
    return out


def _fetch_clickhouse(symbol: str, tf_id: str, warmup_bars: int) -> list[dict]:
    spec = TF_SPECS[tf_id]
    warmup_start = START - timedelta(minutes=warmup_bars * spec["mins"] + spec["mins"] * 2)
    ws = warmup_start.strftime("%Y-%m-%d %H:%M:%S")
    end = END.strftime("%Y-%m-%d %H:%M:%S")
    sym = symbol.replace("'", "\\'")
    sql = f"""
SELECT
    {spec["sql_fn"]}            AS open_time,
    argMin(open,  open_time)    AS open,
    max(high)                   AS high,
    min(low)                    AS low,
    argMax(close, open_time)    AS close,
    sum(volume)                 AS volume
FROM ohlcv
WHERE exchange = 'binance'
  AND symbol   = '{sym}'
  AND open_time >= '{ws}'
  AND open_time <  '{end}'
GROUP BY open_time
ORDER BY open_time
"""
    raw = _ch_query(sql)
    candles = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        dt = datetime.strptime(row["open_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        candles.append(
            {
                "time": dt,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0)),
            }
        )
    return candles


def fetch_candles(symbol: str, tf_id: str, warmup_bars: int = 400) -> list[dict]:
    if DATA_SOURCE == "clickhouse":
        return _fetch_clickhouse(symbol, tf_id, warmup_bars)

    candles = _fetch_local(symbol, tf_id, warmup_bars)
    if candles or DATA_SOURCE == "local":
        return candles

    if DATA_SOURCE == "auto":
        return _fetch_clickhouse(symbol, tf_id, warmup_bars)

    raise ValueError(f"Unsupported DATA_SOURCE={DATA_SOURCE!r}; use local, auto, or clickhouse")


def calc_sma(vals: list[float], p: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= p:
            s -= vals[i - p]
        if i >= p - 1:
            out[i] = s / p
    return out


def calc_std(vals: list[float], p: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    for i in range(p - 1, len(vals)):
        w = vals[i - p + 1 : i + 1]
        mean = sum(w) / p
        var = sum((x - mean) ** 2 for x in w) / p
        out[i] = math.sqrt(var)
    return out


def calc_bbands(vals: list[float], p: int, std_mult: float) -> tuple[list, list, list]:
    mid = calc_sma(vals, p)
    sd = calc_std(vals, p)
    upper = [None if mid[i] is None or sd[i] is None else mid[i] + std_mult * sd[i] for i in range(len(vals))]
    lower = [None if mid[i] is None or sd[i] is None else mid[i] - std_mult * sd[i] for i in range(len(vals))]
    return upper, mid, lower


def calc_atr(hi: list[float], lo: list[float], cl: list[float], p: int) -> list[float | None]:
    trs = [0.0] * len(cl)
    for i in range(1, len(cl)):
        trs[i] = max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
    out: list[float | None] = [None] * len(cl)
    s = 0.0
    for i in range(1, len(cl)):
        s += trs[i]
        if i > p:
            s -= trs[i - p]
        if i >= p:
            out[i] = s / p
    return out


def calc_rsi(cl: list[float], p: int) -> list[float | None]:
    out: list[float | None] = [None] * len(cl)
    for i in range(p, len(cl)):
        gains = 0.0
        losses = 0.0
        for j in range(i - p + 1, i + 1):
            d = cl[j] - cl[j - 1]
            if d >= 0:
                gains += d
            else:
                losses -= d
        if losses <= 1e-12:
            out[i] = 100.0
        else:
            rs = gains / losses
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def calc_vwap(candles: list[dict], p: int) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    pv = 0.0
    vv = 0.0
    for i, c in enumerate(candles):
        typical = (c["high"] + c["low"] + c["close"]) / 3.0
        vol = max(c.get("volume", 0.0), 0.0)
        pv += typical * vol
        vv += vol
        if i >= p:
            old = candles[i - p]
            old_typical = (old["high"] + old["low"] + old["close"]) / 3.0
            old_vol = max(old.get("volume", 0.0), 0.0)
            pv -= old_typical * old_vol
            vv -= old_vol
        if i >= p - 1 and vv > 0:
            out[i] = pv / vv
    return out


def rolling_z(vals: list[float], p: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    for i in range(p - 1, len(vals)):
        w = vals[i - p + 1 : i + 1]
        mean = sum(w) / p
        var = sum((x - mean) ** 2 for x in w) / p
        sd = math.sqrt(var)
        if sd > 1e-12:
            out[i] = (vals[i] - mean) / sd
    return out


def calc_pnl(side: str, entry_p: float, exit_p: float, size: float, fee_rate: float = FEE_RATE):
    qty = size / entry_p
    gross = qty * (exit_p - entry_p) if side == "LONG" else qty * (entry_p - exit_p)
    fee = fee_rate * size + fee_rate * (qty * exit_p)
    return gross - fee, fee, gross


def compute_stats(trades: list[dict], capital: float = CAPITAL) -> dict | None:
    if not trades:
        return None

    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    wins_nofee = [t for t in trades if t["net_nofee"] > 0]
    longs = [t for t in trades if t["side"] == "LONG"]
    shorts = [t for t in trades if t["side"] == "SHORT"]

    total_net = sum(t["net"] for t in trades)
    total_fee = sum(t["fee"] for t in trades)
    total_gross = sum(t["gross"] for t in trades)
    gw = sum(t["net"] for t in wins) if wins else 0.0
    gl = abs(sum(t["net"] for t in losses)) if losses else 0.001

    equity = capital
    peak = capital
    max_dd = 0.0
    max_dd_pct = 0.0
    eq_curve = [capital]
    for t in trades:
        equity += t["net"]
        eq_curve.append(equity)
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak * 100.0

    returns = [t["net"] / t["size"] for t in trades if t.get("size", 0) > 0]
    sharpe = 0.0
    if len(returns) > 1:
        avg_r = sum(returns) / len(returns)
        var_r = sum((r - avg_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 0.001
        days = max((END - START).total_seconds() / 86400.0, 1.0)
        trades_per_year = len(trades) / (days / 365.25)
        sharpe = (avg_r / std_r) * math.sqrt(trades_per_year)

    def wr(rows: list[dict]) -> float:
        return sum(1 for t in rows if t["net"] > 0) / max(len(rows), 1) * 100.0

    result_counts: dict[str, int] = {}
    for t in trades:
        result_counts[t["result"]] = result_counts.get(t["result"], 0) + 1

    return {
        "trades": len(trades),
        "wr": len(wins) / len(trades) * 100.0,
        "wr_nofee": len(wins_nofee) / len(trades) * 100.0,
        "net": total_net,
        "fee": total_fee,
        "gross": total_gross,
        "final": capital + total_net,
        "pf": gw / gl,
        "dd": max_dd,
        "dd_pct": max_dd_pct,
        "sharpe": sharpe,
        "longs": len(longs),
        "shorts": len(shorts),
        "l_wr": wr(longs),
        "s_wr": wr(shorts),
        "eq_curve": eq_curve,
        "result_counts": result_counts,
    }


def run_symbol_backtests(strategy_name: str, run_coin, min_bars: int, out_prefix: str) -> list[dict]:
    ensure_local_data_available()
    symbols = load_symbols()
    total = len(symbols)
    print(f"\n  {strategy_name}")
    print(f"  {START:%Y-%m-%d} -> {END:%Y-%m-%d} | symbols={total} | fee={FEE_RATE*100:.4f}%")
    print(f"  data_source={DATA_SOURCE} | data_dir={DATA_DIR} | workers={MAX_WORKERS}\n")

    results: list[dict] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(run_coin, sym, min_bars): sym for sym in symbols}
        for idx, fut in enumerate(as_completed(futures), 1):
            sym = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:
                result = {"label": sym.replace("USDT", ""), "error": str(exc)[:160], "trades": 0}
            results.append(result)
            label = result.get("label", sym.replace("USDT", ""))
            if result.get("error"):
                print(f"  [{idx:>4}/{total}] {label:<14} ERR {result['error']}")
            elif result.get("trades", 0) > 0:
                print(
                    f"  [{idx:>4}/{total}] {label:<14} {result['trades']:>4} lnh"
                    f" WR {result['wr']:>5.1f}% net ${result['net']:>+9,.2f}"
                    f" Sh {result['sharpe']:>5.2f} DD {result['dd_pct']:>5.2f}%"
                )
            else:
                print(f"  [{idx:>4}/{total}] {label:<14} skip")

    write_outputs(out_prefix, results)
    elapsed = time.time() - t0
    valid = [r for r in results if r.get("trades", 0) > 0]
    total_trades = sum(r["trades"] for r in valid)
    total_net = sum(r["net"] for r in valid)
    print(f"\n  DONE {elapsed/60:.1f}m | valid={len(valid)}/{total} | trades={total_trades} | net=${total_net:+,.2f}")
    return results


def write_outputs(prefix: str, results: list[dict]) -> None:
    trade_rows = []
    coin_rows = []
    for r in results:
        if not r or r.get("trades", 0) == 0:
            continue
        trade_rows.extend(r.get("trade_rows", []))
        coin_rows.append(
            {
                "symbol": r["label"],
                "trades": r["trades"],
                "longs": r["longs"],
                "shorts": r["shorts"],
                "wr_fee": round(r["wr"], 2),
                "wr_nofee": round(r["wr_nofee"], 2),
                "net": round(r["net"], 4),
                "fee": round(r["fee"], 4),
                "gross": round(r["gross"], 4),
                "profit_factor": round(r["pf"], 4),
                "sharpe": round(r["sharpe"], 4),
                "max_dd_pct": round(r["dd_pct"], 4),
                "results": json.dumps(r.get("result_counts", {}), sort_keys=True),
            }
        )

    trade_fields = [
        "symbol",
        "timeframe",
        "side",
        "entry_time",
        "exit_time",
        "entry",
        "exit",
        "size",
        "gross",
        "fee",
        "net",
        "result",
    ]
    coin_fields = [
        "symbol",
        "trades",
        "longs",
        "shorts",
        "wr_fee",
        "wr_nofee",
        "net",
        "fee",
        "gross",
        "profit_factor",
        "sharpe",
        "max_dd_pct",
        "results",
    ]

    trade_rows.sort(key=lambda x: (x["entry_time"], x["symbol"]))
    trade_path = ROOT / f"{prefix}_trades.csv"
    with trade_path.open("w", newline="", encoding="utf-8") as f:
        fields = list(trade_rows[0].keys()) if trade_rows else trade_fields
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(trade_rows)
    print(f"  >>> trades: {trade_path}")

    coin_rows.sort(key=lambda x: -x["net"])
    coin_path = ROOT / f"{prefix}_coins.csv"
    with coin_path.open("w", newline="", encoding="utf-8") as f:
        fields = list(coin_rows[0].keys()) if coin_rows else coin_fields
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(coin_rows)
    print(f"  >>> coins:  {coin_path}")

    html_path = write_html_report(
        prefix=prefix,
        title=prefix.replace("_", " ").upper(),
        summary_rows=coin_rows,
        trade_rows=trade_rows,
        cards=summary_cards_from_rows(coin_rows),
        chart_data=chart_data_from_results(results),
    )
    print(f"  >>> html:   {html_path}")


def summary_cards_from_rows(rows: list[dict]) -> list[tuple[str, str, str]]:
    if not rows:
        return [
            ("Trades", "0", "No trades in the selected sample"),
            ("Net P&L", "$+0.00", "After fee"),
            ("Symbols", "0", "Valid symbols"),
        ]

    trades = sum(int(r.get("trades", 0)) for r in rows)
    net = sum(float(r.get("net", 0.0)) for r in rows)
    fee = sum(float(r.get("fee", 0.0)) for r in rows)
    gross = sum(float(r.get("gross", 0.0)) for r in rows)
    wins = sum(float(r.get("wr_fee", 0.0)) * int(r.get("trades", 0)) / 100.0 for r in rows)
    wr = wins / max(trades, 1) * 100.0
    avg_sharpe = sum(float(r.get("sharpe", 0.0)) for r in rows) / max(len(rows), 1)
    max_dd = max(float(r.get("max_dd_pct", 0.0)) for r in rows)
    return [
        ("Trades", f"{trades:,}", f"{len(rows)} symbols with trades"),
        ("Win Rate", f"{wr:.1f}%", "After fee"),
        ("Net P&L", money(net), f"Gross {money(gross)} | Fee {money(fee)}"),
        ("Sharpe", f"{avg_sharpe:.2f}", "Average by symbol"),
        ("Max DD", f"{max_dd:.2f}%", "Worst symbol drawdown"),
    ]


def money(value: float) -> str:
    return f"${value:+,.2f}"


def html_color(value: float) -> str:
    if value > 0:
        return "#22c55e"
    if value < 0:
        return "#ef4444"
    return "#64748b"


def downsample(values: list[float], max_points: int = 500) -> list[float]:
    if len(values) <= max_points:
        return values
    step = len(values) / max_points
    return [values[int(i * step)] for i in range(max_points)]


def chart_data_from_results(results: list[dict]) -> dict | None:
    valid = [r for r in results if r and r.get("trades", 0) > 0 and r.get("eq_curve")]
    if not valid:
        return None

    max_len = max(len(r["eq_curve"]) for r in valid)
    agg = [CAPITAL * len(valid)]
    for i in range(1, max_len):
        agg.append(
            sum(
                r["eq_curve"][i] if i < len(r["eq_curve"]) else r["eq_curve"][-1]
                for r in valid
            )
        )
    return {"eq": downsample(agg), "start": CAPITAL * len(valid)}


def write_html_report(
    prefix: str,
    title: str,
    summary_rows: list[dict],
    trade_rows: list[dict],
    cards: list[tuple[str, str, str]] | None = None,
    chart_data: dict | None = None,
) -> Path:
    cards = cards or summary_cards_from_rows(summary_rows)
    summary_rows = summary_rows or []
    trade_rows = trade_rows or []

    def esc(value) -> str:
        return html_lib.escape(str(value))

    def table(headers: list[str], rows: list[dict], max_rows: int | None = None) -> str:
        if not rows:
            return '<p class="muted">No rows.</p>'
        shown = rows[:max_rows] if max_rows else rows
        head = "".join(f"<th>{esc(h)}</th>" for h in headers)
        body = []
        for row in shown:
            cells = []
            for h in headers:
                value = row.get(h, "")
                style = ""
                if h in ("net", "gross", "wr_fee", "wr", "sharpe") and isinstance(value, (int, float)):
                    style = f' style="color:{html_color(float(value))}"'
                cells.append(f"<td{style}>{esc(value)}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        note = ""
        if max_rows and len(rows) > max_rows:
            note = f'<p class="muted">Showing {max_rows:,} of {len(rows):,} rows.</p>'
        return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>{note}"

    card_html = "".join(
        f"""
        <section class="card">
          <div class="label">{esc(label)}</div>
          <div class="value">{esc(value)}</div>
          <div class="sub">{esc(sub)}</div>
        </section>
        """
        for label, value, sub in cards
    )

    summary_headers = list(summary_rows[0].keys()) if summary_rows else []
    trade_headers = list(trade_rows[0].keys()) if trade_rows else []
    top_summary = sorted(summary_rows, key=lambda r: float(r.get("net", 0.0)), reverse=True)
    top_trades = sorted(trade_rows, key=lambda r: str(r.get("entry_time", "")))
    chart_json = json.dumps(chart_data or {"eq": [CAPITAL], "start": CAPITAL})
    chart_block = ""
    if chart_data:
        chart_block = f"""
  <div class="chart-container">
    <div class="chart-title">EQUITY CURVE TONG HOP</div>
    <canvas id="chart_equity" height="220"></canvas>
    <div class="chart-legend">
      <span><span class="dot" style="background:#38bdf8"></span> Equity</span>
      <span>Von ban dau: ${chart_data.get("start", CAPITAL):,.0f}</span>
    </div>
    <div class="chart-tooltip" id="tip_equity"></div>
  </div>
"""

    path = ROOT / f"{prefix}_report.html"
    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0f172a; color: #e2e8f0; font-family: 'SF Mono', 'Fira Code', Consolas, monospace; font-size: 13px; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
h1 {{ color: #38bdf8; font-size: 22px; margin-bottom: 4px; }}
h2 {{ color: #f59e0b; font-size: 16px; margin: 30px 0 12px; border-bottom: 1px solid #334155; padding-bottom: 6px; }}
.subtitle {{ color: #64748b; font-size: 12px; margin-bottom: 20px; }}
.config {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 16px; margin-bottom: 24px; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }}
.config span {{ color: #94a3b8; }}
.config b {{ color: #f1f5f9; }}
.fee-highlight {{ color: #fbbf24 !important; }}
.summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.summary-card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 14px; }}
.summary-card .label {{ color: #64748b; font-size: 11px; text-transform: uppercase; }}
.summary-card .value {{ font-size: 20px; font-weight: 700; margin-top: 4px; }}
.summary-card .sub {{ color: #64748b; font-size: 11px; margin-top: 2px; }}
.tf-section {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 20px; margin-bottom: 24px; }}
table {{ width: 100%; border-collapse: collapse; margin-bottom: 16px; }}
th {{ background: #1e293b; color: #94a3b8; text-align: left; padding: 8px 10px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #334155; position: sticky; top: 0; }}
td {{ padding: 7px 10px; border-bottom: 1px solid #1e293b; white-space: nowrap; }}
tr:hover td {{ background: #1e293b; }}
.panel {{ overflow-x: auto; }}
.muted {{ color: #64748b; padding: 10px 0; }}
.chart-container {{ position: relative; background: #0f172a; border: 1px solid #334155; border-radius: 8px; margin: 16px 0 24px; overflow: hidden; }}
.chart-container canvas {{ display: block; width: 100%; }}
.chart-title {{ position: absolute; top: 12px; left: 16px; color: #94a3b8; font-size: 12px; z-index: 2; pointer-events: none; }}
.chart-legend {{ display: flex; gap: 16px; padding: 8px 16px; background: #1e293b; border-top: 1px solid #334155; font-size: 11px; }}
.chart-legend span {{ display: flex; align-items: center; gap: 4px; color: #94a3b8; }}
.chart-legend .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; }}
.chart-tooltip {{ position: absolute; background: #1e293b; border: 1px solid #475569; border-radius: 6px; padding: 8px 12px; font-size: 11px; pointer-events: none; display: none; z-index: 10; white-space: nowrap; }}
footer {{ text-align: center; color: #475569; margin-top: 40px; padding: 20px; border-top: 1px solid #1e293b; font-size: 11px; }}
</style>
</head>
<body>
<div class="container">
  <h1>{esc(title)}</h1>
  <p class="subtitle">
    Source: {esc(DATA_SOURCE)} / {esc(DATA_DIR)} &nbsp;|&nbsp;
    Fee: {FEE_RATE_RAW*100:.4f}% &times; (1 - {FEE_DISCOUNT*100:.0f}%) =
    <b class="fee-highlight">{FEE_RATE*100:.4f}%</b> &nbsp;|&nbsp;
    {START:%Y-%m-%d} &rarr; {END:%Y-%m-%d}
  </p>
  <div class="config">
    <div><span>Von:</span> <b>${CAPITAL:,.0f}</b></div>
    <div><span>Size:</span> <b>${SIZE:,.0f} &rarr; dynamic</b></div>
    <div><span>Min size:</span> <b>${MIN_SIZE:,.0f}</b></div>
    <div><span>Fee thuc:</span> <b class="fee-highlight">{FEE_RATE*100:.4f}%</b></div>
    <div><span>Workers:</span> <b>{MAX_WORKERS}</b></div>
  </div>
  <div class="summary-grid">{card_html}</div>
  {chart_block}
  <div class="tf-section">
  <h2>Summary</h2>
  <section class="panel">{table(summary_headers, top_summary)}</section>
  <h2>Trades</h2>
  <section class="panel">{table(trade_headers, top_trades, max_rows=500)}</section>
  </div>
  <footer>{esc(title)} | Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}</footer>
</div>
<script>
const CHART = {chart_json};
const DPR = window.devicePixelRatio || 1;

function drawChart() {{
  const canvas = document.getElementById('chart_equity');
  if (!canvas || !CHART.eq || CHART.eq.length < 2) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  const W = rect.width, H = 220;
  canvas.width = W * DPR; canvas.height = H * DPR;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  const eq = CHART.eq, n = eq.length, startVal = CHART.start, endVal = eq[n - 1];
  const pad = {{ t: 35, r: 70, b: 28, l: 14 }};
  const cW = W - pad.l - pad.r, cH = H - pad.t - pad.b;
  const minV = Math.min(...eq) * 0.9998, maxV = Math.max(...eq) * 1.0002;
  const range = maxV - minV || 1;
  const x = i => pad.l + (i / (n - 1)) * cW;
  const y = v => pad.t + (1 - (v - minV) / range) * cH;

  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = '#1e293b'; ctx.lineWidth = 1;
  for (let i = 0; i <= 5; i++) {{
    const gy = pad.t + (i / 5) * cH;
    const gv = maxV - (i / 5) * range;
    ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(W - pad.r, gy); ctx.stroke();
    ctx.fillStyle = '#475569'; ctx.font = '10px monospace'; ctx.textAlign = 'left';
    ctx.fillText('$' + gv.toFixed(0), W - pad.r + 5, gy + 3);
  }}
  ctx.strokeStyle = '#334155'; ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(pad.l, y(startVal)); ctx.lineTo(W - pad.r, y(startVal)); ctx.stroke();
  ctx.setLineDash([]);

  const grad = ctx.createLinearGradient(0, pad.t, 0, H - pad.b);
  if (endVal >= startVal) {{
    grad.addColorStop(0, 'rgba(34,197,94,0.25)');
    grad.addColorStop(1, 'rgba(34,197,94,0.02)');
  }} else {{
    grad.addColorStop(0, 'rgba(239,68,68,0.02)');
    grad.addColorStop(1, 'rgba(239,68,68,0.25)');
  }}
  ctx.beginPath(); ctx.moveTo(x(0), y(eq[0]));
  for (let i = 1; i < n; i++) ctx.lineTo(x(i), y(eq[i]));
  ctx.lineTo(x(n - 1), H - pad.b); ctx.lineTo(x(0), H - pad.b); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();

  ctx.beginPath(); ctx.moveTo(x(0), y(eq[0]));
  for (let i = 1; i < n; i++) ctx.lineTo(x(i), y(eq[i]));
  ctx.strokeStyle = endVal >= startVal ? '#22c55e' : '#ef4444';
  ctx.lineWidth = 2; ctx.stroke();

  const tip = document.getElementById('tip_equity');
  if (!tip) return;
  canvas.onmousemove = e => {{
    const br = canvas.getBoundingClientRect();
    const mx = e.clientX - br.left;
    const idx = Math.max(0, Math.min(n - 1, Math.round(((mx - pad.l) / cW) * (n - 1))));
    const val = eq[idx], pnl = val - startVal, pct = pnl / startVal * 100;
    tip.style.display = 'block';
    tip.style.left = (mx + 12) + 'px';
    tip.style.top = (y(val) - 20) + 'px';
    tip.innerHTML = '<b>$' + val.toFixed(2) + '</b><br>'
      + '<span style="color:' + (pnl >= 0 ? '#22c55e' : '#ef4444') + '">'
      + (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2)
      + ' (' + (pnl >= 0 ? '+' : '') + pct.toFixed(2) + '%)</span><br>'
      + '<span style="color:#64748b">Point ' + idx + '/' + (n - 1) + '</span>';
  }};
  canvas.onmouseleave = () => {{ tip.style.display = 'none'; }};
}}
window.addEventListener('load', drawChart);
window.addEventListener('resize', drawChart);
</script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
    return path
