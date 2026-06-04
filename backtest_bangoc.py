#!/usr/bin/env python3
"""Alpha-1 Bangoc Backtest
   Logic: paper-trade-system/alphas/alpha-1-bangoc
   Entry: Indi1 and Indi2 have the same color.
   Risk: hard SL -3.5%, TP1 +2% closes 33% and moves SL to breakeven,
         TP2 +3.5% closes another 33%, remainder exits on a new signal.
   Re-entry: after SL/BE, wait for an opposite signal before entering again.
   Data: Binance Futures REST API (fapi.binance.com/fapi/v1/klines)
   Symbols: fixed 15-coin universe requested for this backtest
   Timeframes: M15 and H1
   Outputs: separate M15/H1 trade history CSVs per coin and one combined HTML
"""

import csv, json, time, math, os, random, zipfile
import urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Semaphore
from io import BytesIO

# ── CONFIG ────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(BASE_DIR, "backtest_bangoc_output")

START = datetime(2025, 8, 1, tzinfo=timezone.utc)
END   = datetime(2026, 6, 4, tzinfo=timezone.utc)

CAPITAL  = 10_000.0
SIZE     = 1_000.0

FEE_RATE = 0.000357

HARD_SL_PCT = 0.035
TP1_PCT     = 0.020
TP2_PCT     = 0.035
TP1_CLOSE   = 0.33
TP2_CLOSE   = 0.33

INDI1_SMA_LEN     = 85
INDI1_NORM_WINDOW = 500
INDI1_THRESHOLD   = 0.1

INDI2_LOOKBACK   = 85
INDI2_PERCENTILE = 65.0  # Metadata bands in live alpha; does not affect side.

WARMUP = 700

MAX_WORKERS  = 8
MAX_API_CONC = 5

TIMEFRAMES = [
    {"id": "15m", "label": "M15", "mins": 15},
    {"id": "1h",  "label": "H1",  "mins": 60},
]

SYMBOLS = [
    "AVAXUSDT", "SUIUSDT", "NEARUSDT", "APTUSDT", "DOTUSDT",
    "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT",
    "SEIUSDT", "TIAUSDT", "INJUSDT", "ENAUSDT", "ARBUSDT",
]

# ── BINANCE FUTURES API ───────────────────────────────
_BN_BASE      = "https://fapi.binance.com/fapi/v1/klines"
_BN_LIMIT     = 1500
_BN_SEMAPHORE = Semaphore(MAX_API_CONC)   # max concurrent HTTP calls

_BV_BASE       = "https://data.binance.vision"
_HTTP_HEADERS  = {"User-Agent": "python-backtest/1.0"}


def _bn_fetch_batch(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    params = urllib.parse.urlencode({
        "symbol":    symbol,
        "interval":  interval,
        "startTime": start_ms,
        "endTime":   end_ms - 1,
        "limit":     _BN_LIMIT,
    })
    url = f"{_BN_BASE}?{params}"
    req = urllib.request.Request(url, headers=_HTTP_HEADERS)
    with _BN_SEMAPHORE:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))


def _warmup_bounds(tf: dict) -> tuple[datetime, int, int]:
    warmup_delta = timedelta(minutes=WARMUP * tf["mins"] + tf["mins"] * 2)
    warmup_start = START - warmup_delta
    start_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    return warmup_start, start_ms, end_ms


def _month_range(start_dt: datetime, end_dt: datetime) -> list[tuple[int, int]]:
    months = []
    y, m = start_dt.year, start_dt.month
    end_y, end_m = end_dt.year, end_dt.month
    while (y, m) <= (end_y, end_m):
        months.append((y, m))
        m += 1
        if m > 12:
            y += 1
            m = 1
    return months


def _date_range(start_dt: datetime, end_dt: datetime) -> list[datetime.date]:
    days = []
    d = start_dt.date()
    last = (end_dt - timedelta(milliseconds=1)).date()
    while d <= last:
        days.append(d)
        d += timedelta(days=1)
    return days


def _read_url_bytes(url: str, timeout: int = 120) -> bytes | None:
    req = urllib.request.Request(url, headers=_HTTP_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _parse_vision_zip(zip_bytes: bytes) -> list[dict]:
    candles = []
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        if not names:
            return candles
        text = zf.read(names[0]).decode("utf-8")

    for row in csv.reader(text.splitlines()):
        if len(row) < 5:
            continue
        try:
            open_ms = int(row[0])
        except ValueError:
            continue
        candles.append({
            "time":  datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc),
            "open":  float(row[1]),
            "high":  float(row[2]),
            "low":   float(row[3]),
            "close": float(row[4]),
        })
    return candles


def _vision_monthly_url(symbol: str, interval: str, year: int, month: int) -> str:
    filename = f"{symbol}-{interval}-{year}-{month:02d}.zip"
    return f"{_BV_BASE}/data/futures/um/monthly/klines/{symbol}/{interval}/{filename}"


def _vision_daily_url(symbol: str, interval: str, day) -> str:
    date_s = day.strftime("%Y-%m-%d")
    filename = f"{symbol}-{interval}-{date_s}.zip"
    return f"{_BV_BASE}/data/futures/um/daily/klines/{symbol}/{interval}/{filename}"


def fetch_from_binance_vision(symbol: str, tf: dict) -> list:
    """Fetch public Binance Futures klines from data.binance.vision.

    This avoids fapi.binance.com, which can return HTTP 418 from some networks.
    Monthly files are tried first; daily files fill gaps when monthly data is not
    published yet.
    """
    warmup_start, start_ms, end_ms = _warmup_bounds(tf)
    rows_by_time = {}

    for year, month in _month_range(warmup_start, END):
        data = _read_url_bytes(_vision_monthly_url(symbol, tf["id"], year, month))
        if not data:
            continue
        for candle in _parse_vision_zip(data):
            ts = int(candle["time"].timestamp() * 1000)
            if start_ms <= ts < end_ms:
                rows_by_time[candle["time"]] = candle

    expected_last = end_ms - tf["mins"] * 60 * 1000
    has_recent_end = rows_by_time and int(max(rows_by_time).timestamp() * 1000) >= expected_last
    if not has_recent_end:
        available_ms = {int(t.timestamp() * 1000) for t in rows_by_time}
        step_ms = tf["mins"] * 60 * 1000
        for day in _date_range(warmup_start, END):
            day_start = max(
                int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000),
                start_ms,
            )
            day_end = min(day_start - day_start % 86_400_000 + 86_400_000, end_ms)
            if day_start < day_end and all(
                ts in available_ms for ts in range(day_start, day_end, step_ms)
            ):
                continue
            data = _read_url_bytes(_vision_daily_url(symbol, tf["id"], day))
            if not data:
                continue
            for candle in _parse_vision_zip(data):
                ts = int(candle["time"].timestamp() * 1000)
                if start_ms <= ts < end_ms:
                    rows_by_time[candle["time"]] = candle
                    available_ms.add(ts)

    if not rows_by_time:
        raise RuntimeError(f"No Binance Vision data for {symbol} {tf['id']}")

    return [rows_by_time[t] for t in sorted(rows_by_time)]


def fetch_from_binance_rest(symbol: str, tf: dict) -> list:
    """Fetch OHLCV from Binance Futures, paginating as needed.
    Retries up to 5 times on 429 with exponential backoff + jitter.
    """
    _, start_ms, end_ms = _warmup_bounds(tf)
    step_ms   = tf["mins"] * 60 * 1000
    candles   = []
    cur_start = start_ms

    while cur_start < end_ms:
        rows = None
        for attempt in range(5):
            try:
                rows = _bn_fetch_batch(symbol, tf["id"], cur_start, end_ms)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    sleep_s = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(sleep_s)
                else:
                    raise
        if rows is None:
            raise RuntimeError(f"Binance rate-limit for {symbol} after 5 retries")

        if not rows:
            break

        for row in rows:
            dt = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
            candles.append({
                "time":  dt,
                "open":  float(row[1]),
                "high":  float(row[2]),
                "low":   float(row[3]),
                "close": float(row[4]),
            })

        if len(rows) < _BN_LIMIT:
            break
        cur_start = int(rows[-1][0]) + step_ms

    return candles


def fetch_from_binance(symbol: str, tf: dict) -> list:
    try:
        return fetch_from_binance_rest(symbol, tf)
    except urllib.error.HTTPError as e:
        if e.code in (403, 418, 451):
            return fetch_from_binance_vision(symbol, tf)
        raise
    except urllib.error.URLError:
        return fetch_from_binance_vision(symbol, tf)


# ── INDICATORS ────────────────────────────────────────
def calc_sma(vals, p):
    n = len(vals)
    out = [None] * n
    s = 0.0
    for i in range(n):
        s += vals[i]
        if i >= p:
            s -= vals[i - p]
        if i >= p - 1:
            out[i] = s / p
    return out


def calc_median(vals, p):
    n = len(vals)
    out = [None] * n
    for i in range(p - 1, n):
        w = sorted(vals[i - p + 1: i + 1])
        m = p // 2
        out[i] = (w[m-1] + w[m]) / 2 if p % 2 == 0 else w[m]
    return out


def calc_acol(close):
    """Mirror alpha-1-bangoc Indi1 across the full candle series."""
    n = len(close)
    avg = calc_sma(close, INDI1_SMA_LEN)
    adiff = [None] * n
    for i in range(5, n):
        if avg[i] is not None and avg[i - 5] is not None:
            adiff[i] = avg[i] - avg[i - 5]

    acol = [None] * n
    for i in range(INDI1_NORM_WINDOW - 1, n):
        window = adiff[i - INDI1_NORM_WINDOW + 1: i + 1]
        if any(value is None for value in window):
            continue
        denom = max(abs(value) for value in window)
        if denom > 1e-12:
            acol[i] = adiff[i] / denom
    return acol


# ── BACKTEST ──────────────────────────────────────────
def calc_pnl(side, entry_p, exit_p, size, fee_rate):
    qty     = size / entry_p
    gross   = qty * (exit_p - entry_p) if side == "LONG" else qty * (entry_p - exit_p)
    fee_in  = fee_rate * size
    fee_out = fee_rate * (qty * exit_p)
    net     = gross - fee_in - fee_out
    return net, fee_in + fee_out, gross


def run_backtest(candles):
    close = [c["close"] for c in candles]
    acol = calc_acol(close)
    poc = calc_median(close, INDI2_LOOKBACK)

    position = None
    trades = []
    blocked_side = None
    next_trade_id = 1

    def open_position(side, entry, entry_time):
        nonlocal position, next_trade_id
        if side == "LONG":
            sl = entry * (1.0 - HARD_SL_PCT)
            tp1 = entry * (1.0 + TP1_PCT)
            tp2 = entry * (1.0 + TP2_PCT)
        else:
            sl = entry * (1.0 + HARD_SL_PCT)
            tp1 = entry * (1.0 - TP1_PCT)
            tp2 = entry * (1.0 - TP2_PCT)
        position = {
            "trade_id": next_trade_id,
            "side": side,
            "entry": entry,
            "entry_time": entry_time,
            "remaining_size": SIZE,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp1_done": False,
            "tp2_done": False,
        }
        next_trade_id += 1

    def close_part(exit_p, result, exit_time, close_size):
        nonlocal position
        close_size = min(close_size, position["remaining_size"])
        if close_size <= 1e-9:
            return
        net, fee, gross = calc_pnl(
            position["side"], position["entry"], exit_p, close_size, FEE_RATE
        )
        remaining_size = max(0.0, position["remaining_size"] - close_size)
        trades.append({
            "trade_id": position["trade_id"],
            "side": position["side"], "entry": position["entry"], "exit": exit_p,
            "result": result, "net": net, "fee": fee, "gross": gross,
            "net_nofee": gross, "size": close_size,
            "close_pct": close_size / SIZE * 100.0,
            "remaining_size": remaining_size,
            "entry_time": position["entry_time"], "exit_time": exit_time,
        })
        position["remaining_size"] = remaining_size
        if remaining_size <= 1e-9:
            position = None

    def close_remaining(exit_p, result, exit_time):
        close_part(exit_p, result, exit_time, position["remaining_size"])

    for i, candle in enumerate(candles):
        if candle["time"] < START:
            continue
        if candle["time"] >= END:
            break

        ac = acol[i]
        pi = poc[i]
        if ac is None or pi is None:
            continue

        indi1_green = None
        if ac > INDI1_THRESHOLD:
            indi1_green = True
        elif ac < -INDI1_THRESHOLD:
            indi1_green = False

        indi2_green = None
        if close[i] > pi:
            indi2_green = True
        elif close[i] < pi:
            indi2_green = False

        desired_side = None
        if indi1_green is not None and indi1_green == indi2_green:
            desired_side = "LONG" if indi1_green else "SHORT"

        if position is not None:
            side = position["side"]
            stop_hit = candle["low"] <= position["sl"] if side == "LONG" else candle["high"] >= position["sl"]
            if stop_hit:
                stop_result = "BE" if position["tp1_done"] else "SL"
                stop_price = position["sl"]
                blocked_side = side
                close_remaining(stop_price, stop_result, candle["time"])
            else:
                # The stop active at candle open has priority. A BE stop created
                # by TP1 becomes active from the next candle.
                tp1_hit = candle["high"] >= position["tp1"] if side == "LONG" else candle["low"] <= position["tp1"]
                if not position["tp1_done"] and tp1_hit:
                    tp1_price = position["tp1"]
                    close_part(tp1_price, "TP1", candle["time"], SIZE * TP1_CLOSE)
                    position["tp1_done"] = True
                    position["sl"] = position["entry"]

                tp2_hit = candle["high"] >= position["tp2"] if side == "LONG" else candle["low"] <= position["tp2"]
                if position is not None and not position["tp2_done"] and tp2_hit:
                    tp2_price = position["tp2"]
                    close_part(tp2_price, "TP2", candle["time"], SIZE * TP2_CLOSE)
                    position["tp2_done"] = True

        if desired_side is None:
            continue

        if position is not None and position["side"] != desired_side:
            close_remaining(close[i], "REV", candle["time"])
            blocked_side = None
            open_position(desired_side, close[i], candle["time"])
        elif position is None:
            if blocked_side is None:
                open_position(desired_side, close[i], candle["time"])
            elif desired_side != blocked_side:
                blocked_side = None
                open_position(desired_side, close[i], candle["time"])

    if position is not None:
        last = max((c for c in candles if c["time"] < END), key=lambda c: c["time"])
        close_remaining(last["close"], "OPEN", last["time"])

    return trades, 0


def compute_stats(trades):
    if not trades:
        return None

    positions_by_id = {}
    for leg in trades:
        position = positions_by_id.setdefault(leg["trade_id"], {
            "trade_id": leg["trade_id"],
            "side": leg["side"],
            "entry_time": leg["entry_time"],
            "exit_time": leg["exit_time"],
            "net": 0.0,
            "net_nofee": 0.0,
            "gross": 0.0,
            "fee": 0.0,
            "size": SIZE,
        })
        position["exit_time"] = max(position["exit_time"], leg["exit_time"])
        position["net"] += leg["net"]
        position["net_nofee"] += leg["net_nofee"]
        position["gross"] += leg["gross"]
        position["fee"] += leg["fee"]
    positions = list(positions_by_id.values())

    wins       = [t for t in positions if t["net"] > 0]
    losses     = [t for t in positions if t["net"] <= 0]
    wins_nofee = [t for t in positions if t["net_nofee"] > 0]
    longs      = [t for t in positions if t["side"] == "LONG"]
    shorts     = [t for t in positions if t["side"] == "SHORT"]

    total_net   = sum(t["net"]   for t in trades)
    total_fee   = sum(t["fee"]   for t in trades)
    total_gross = sum(t["gross"] for t in trades)
    wr          = len(wins)       / len(positions) * 100
    wr_nofee    = len(wins_nofee) / len(positions) * 100

    gw = sum(t["net"] for t in wins)              if wins   else 0
    gl = abs(sum(t["net"] for t in losses))        if losses else 0.001
    pf = gw / gl

    equity = CAPITAL; peak = CAPITAL; max_dd = 0; max_dd_pct = 0
    eq_curve = [CAPITAL]
    eq_times = [trades[0]["entry_time"].strftime("%Y-%m-%d")]
    dd_curve = [0.0]
    for t in trades:
        equity += t["net"]
        eq_curve.append(equity)
        eq_times.append(t["exit_time"].strftime("%Y-%m-%d"))
        peak = max(peak, equity)
        dd   = peak - equity
        dd_curve.append(-dd)
        if dd > max_dd:
            max_dd     = dd
            max_dd_pct = dd / peak * 100

    returns = [t["net"] / t["size"] for t in positions if t["size"] > 0]
    if len(returns) > 1:
        avg_r = sum(returns) / len(returns)
        var_r = sum((r - avg_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 0.001
        trades_per_year = len(positions) / ((END - START).days / 365.25)
        sharpe = (avg_r / std_r) * math.sqrt(trades_per_year)
    else:
        sharpe = 0

    l_wins     = sum(1 for t in longs  if t["net"] > 0)
    s_wins     = sum(1 for t in shorts if t["net"] > 0)
    l_wr       = l_wins / max(len(longs),  1) * 100
    s_wr       = s_wins / max(len(shorts), 1) * 100
    l_net      = sum(t["net"] for t in longs)
    s_net      = sum(t["net"] for t in shorts)
    l_wr_nofee = sum(1 for t in longs  if t["net_nofee"] > 0) / max(len(longs),  1) * 100
    s_wr_nofee = sum(1 for t in shorts if t["net_nofee"] > 0) / max(len(shorts), 1) * 100

    tp1_n  = sum(1 for t in trades if t["result"] == "TP1")
    tp2_n  = sum(1 for t in trades if t["result"] == "TP2")
    sl_n   = sum(1 for t in trades if t["result"] == "SL")
    be_n   = sum(1 for t in trades if t["result"] == "BE")
    cut_n  = sum(1 for t in trades if t["result"] == "CUT")
    rev_n  = sum(1 for t in trades if t["result"] == "REV")
    time_n = sum(1 for t in trades if t["result"] == "TIME")
    open_n = sum(1 for t in trades if t["result"] == "OPEN")

    max_win_streak = max_lose_streak = cur_w = cur_l = 0
    for t in positions:
        if t["net"] > 0:
            cur_w += 1; cur_l = 0
            max_win_streak = max(max_win_streak, cur_w)
        else:
            cur_l += 1; cur_w = 0
            max_lose_streak = max(max_lose_streak, cur_l)

    months = {}
    for t in positions:
        m = t["entry_time"].strftime("%Y-%m")
        if m not in months:
            months[m] = {"net": 0, "cnt": 0, "wins": 0, "fee": 0}
        months[m]["net"] += t["net"]
        months[m]["cnt"] += 1
        months[m]["fee"] += t["fee"]
        if t["net"] > 0:
            months[m]["wins"] += 1

    return {
        "trades": len(positions), "executions": len(trades),
        "wr": wr, "wr_nofee": wr_nofee,
        "net": total_net, "fee": total_fee, "gross": total_gross,
        "final": CAPITAL + total_net, "pf": pf,
        "dd": max_dd, "dd_pct": max_dd_pct, "sharpe": sharpe,
        "longs": len(longs), "l_wr": l_wr, "l_net": l_net, "l_wr_nofee": l_wr_nofee,
        "shorts": len(shorts), "s_wr": s_wr, "s_net": s_net, "s_wr_nofee": s_wr_nofee,
        "tp1": tp1_n, "tp2": tp2_n, "sl": sl_n, "be": be_n,
        "cut": cut_n, "rev": rev_n, "time": time_n,
        "open": open_n,
        "max_win_streak": max_win_streak, "max_lose_streak": max_lose_streak,
        "eq_curve": eq_curve, "eq_times": eq_times, "dd_curve": dd_curve,
        "equity_events": [(t["exit_time"], t["net"]) for t in trades],
        "months": months,
    }


def _make_trade_rows(trades: list, label: str, tf_label: str) -> list:
    return [
        {
            "trade_id":    t["trade_id"],
            "symbol":     label,
            "timeframe":  tf_label,
            "side":       t["side"],
            "entry_time": t["entry_time"].strftime("%Y-%m-%d %H:%M"),
            "exit_time":  t["exit_time"].strftime("%Y-%m-%d %H:%M"),
            "entry":      round(t["entry"],  6),
            "exit":       round(t["exit"],   6),
            "size":       round(t["size"],   2),
            "close_pct":  round(t["close_pct"], 2),
            "remaining_size": round(t["remaining_size"], 2),
            "is_partial": "YES" if t["remaining_size"] > 1e-9 else "NO",
            "gross":      round(t["gross"],  4),
            "fee":        round(t["fee"],    4),
            "net":        round(t["net"],    4),
            "result":     t["result"],
        }
        for t in trades
    ]


# ── HTML REPORT ───────────────────────────────────────
def generate_html(all_results):
    def color(v):
        return "#22c55e" if v > 0 else "#ef4444" if v < 0 else "#94a3b8"

    def wr_color(v):
        if v >= 50: return "#22c55e"
        if v >= 35: return "#eab308"
        return "#ef4444"

    def sharpe_color(v):
        if v >= 1.5: return "#22c55e"
        if v >= 0.5: return "#eab308"
        return "#ef4444"

    def downsample(arr, max_pts=500):
        if len(arr) <= max_pts:
            return arr
        step = len(arr) / max_pts
        return [arr[int(i * step)] for i in range(max_pts)]

    html = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Alpha-1 Bangoc Backtest - Report</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #0f172a; color: #e2e8f0; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; }
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }
h1 { color: #38bdf8; font-size: 22px; margin-bottom: 4px; }
h2 { color: #f59e0b; font-size: 16px; margin: 30px 0 12px; border-bottom: 1px solid #334155; padding-bottom: 6px; }
h3 { color: #94a3b8; font-size: 13px; margin: 20px 0 8px; }
.subtitle { color: #64748b; font-size: 12px; margin-bottom: 20px; }
.config { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 16px; margin-bottom: 24px; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }
.config span { color: #94a3b8; }
.config b { color: #f1f5f9; }
.fee-highlight { color: #fbbf24 !important; }
table { width: 100%; border-collapse: collapse; margin-bottom: 16px; }
th { background: #1e293b; color: #94a3b8; text-align: left; padding: 8px 10px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #334155; position: sticky; top: 0; }
td { padding: 7px 10px; border-bottom: 1px solid #1e293b; }
tr:hover td { background: #1e293b; }
.pos { color: #22c55e; }
.neg { color: #ef4444; }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }
.summary-card { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 14px; }
.summary-card .label { color: #64748b; font-size: 11px; text-transform: uppercase; }
.summary-card .value { font-size: 20px; font-weight: 700; margin-top: 4px; }
.summary-card .sub { color: #64748b; font-size: 11px; margin-top: 2px; }
.tf-section { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 20px; margin-bottom: 24px; }
.chart-container { position: relative; background: #0f172a; border: 1px solid #334155; border-radius: 8px; margin: 16px 0; overflow: hidden; }
.chart-container canvas { display: block; width: 100%; }
.chart-title { position: absolute; top: 12px; left: 16px; color: #94a3b8; font-size: 12px; z-index: 2; pointer-events: none; }
.chart-legend { display: flex; gap: 16px; padding: 8px 16px; background: #1e293b; border-top: 1px solid #334155; font-size: 11px; }
.chart-legend span { display: flex; align-items: center; gap: 4px; color: #94a3b8; }
.chart-legend .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.chart-tooltip { position: absolute; background: #1e293b; border: 1px solid #475569; border-radius: 6px; padding: 8px 12px; font-size: 11px; pointer-events: none; display: none; z-index: 10; white-space: nowrap; }
.coin-charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 12px; margin-top: 16px; }
.coin-chart-wrap { background: #0f172a; border: 1px solid #334155; border-radius: 8px; overflow: hidden; }
.coin-chart-wrap .coin-header { padding: 8px 12px; font-size: 12px; color: #e2e8f0; background: #1e293b; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; }
.coin-chart-wrap canvas { display: block; width: 100%; }
footer { text-align: center; color: #475569; margin-top: 40px; padding: 20px; border-top: 1px solid #1e293b; font-size: 11px; }
</style>
</head>
<body>
<div class="container">
<h1>ALPHA-1 BANGOC BACKTEST</h1>
<p class="subtitle">
  Source: Binance Futures &nbsp;|&nbsp;
  Fee: <b style="color:#fbbf24">0.0357% / side</b> &nbsp;|&nbsp;
  """ + f'{START.strftime("%Y-%m-%d")} &rarr; {END.strftime("%Y-%m-%d")}' + """
</p>
<div class="config">
<div><span>Von:</span> <b>$""" + f'{CAPITAL:,.0f}' + """</b></div>
<div><span>Size:</span> <b>$""" + f'{SIZE:,.0f}' + """ fixed</b></div>
<div><span>Fee thuc:</span> <b class="fee-highlight">0.0357%</b></div>
<div><span>Indi1:</span> <b>SMA(""" + f'{INDI1_SMA_LEN}' + """), norm """ + f'{INDI1_NORM_WINDOW}' + """</b></div>
<div><span>Indi1 threshold:</span> <b>&plusmn;""" + f'{INDI1_THRESHOLD}' + """</b></div>
<div><span>Indi2:</span> <b>POC/median(""" + f'{INDI2_LOOKBACK}' + """)</b></div>
<div><span>SL:</span> <b>-""" + f'{HARD_SL_PCT * 100:.1f}' + """%</b></div>
<div><span>TP1:</span> <b>+""" + f'{TP1_PCT * 100:.1f}' + """% close 33% + BE</b></div>
<div><span>TP2:</span> <b>+""" + f'{TP2_PCT * 100:.1f}' + """% close 33%</b></div>
<div><span>Remainder:</span> <b>close/reverse on opposite signal</b></div>
</div>
"""

    all_chart_data = {}

    for tf_label, results in all_results.items():
        valid = [r for r in results if r and r.get("trades", 0) > 0]
        if not valid:
            continue

        t_trades      = sum(r["trades"] for r in valid)
        t_net         = sum(r["net"]    for r in valid)
        t_fee         = sum(r["fee"]    for r in valid)
        t_gross       = sum(r["gross"]  for r in valid)
        t_longs       = sum(r["longs"]  for r in valid)
        t_shorts      = sum(r["shorts"] for r in valid)
        t_wins        = sum(int(r["wr"]       * r["trades"] / 100) for r in valid)
        t_wins_nofee  = sum(int(r["wr_nofee"] * r["trades"] / 100) for r in valid)
        t_wr          = t_wins       / max(t_trades, 1) * 100
        t_wr_nofee    = t_wins_nofee / max(t_trades, 1) * 100
        profitable    = sum(1 for r in valid if r["net"] > 0)
        avg_sharpe    = sum(r["sharpe"] for r in valid) / len(valid)
        max_dd_all    = max(r["dd_pct"] for r in valid)

        agg_eq  = [CAPITAL * len(valid)]
        equity = agg_eq[0]
        events = sorted(
            event
            for r in valid
            for event in r.get("equity_events", [])
        )
        for _, net in events:
            equity += net
            agg_eq.append(equity)

        agg_id = f"agg_{tf_label}"
        all_chart_data[agg_id] = {"eq": downsample(agg_eq), "start": CAPITAL * len(valid)}

        html += f"""
<div class="tf-section">
<h2>{tf_label} — {len(valid)} coins co du lieu</h2>
<div class="summary-grid">
<div class="summary-card">
  <div class="label">Tong lenh</div>
  <div class="value">{t_trades}</div>
  <div class="sub">Long: {t_longs} | Short: {t_shorts}</div>
</div>
<div class="summary-card">
  <div class="label">Win Rate (co fee)</div>
  <div class="value" style="color:{wr_color(t_wr)}">{t_wr:.1f}%</div>
  <div class="sub">Khong fee: {t_wr_nofee:.1f}% (delta: {"+" if t_wr_nofee > t_wr else ""}{t_wr_nofee - t_wr:.1f}%)</div>
</div>
<div class="summary-card">
  <div class="label">P&L Rong</div>
  <div class="value" style="color:{color(t_net)}">${t_net:+,.2f}</div>
  <div class="sub">Truoc fee: ${t_gross:+,.2f} | Fee: ${t_fee:,.2f}</div>
</div>
<div class="summary-card">
  <div class="label">Sharpe Ratio (TB)</div>
  <div class="value" style="color:{sharpe_color(avg_sharpe)}">{avg_sharpe:.2f}</div>
  <div class="sub">{'Tot' if avg_sharpe >= 1.5 else 'Trung binh' if avg_sharpe >= 0.5 else 'Yeu'}</div>
</div>
<div class="summary-card">
  <div class="label">Max DD (cao nhat)</div>
  <div class="value neg">{max_dd_all:.2f}%</div>
  <div class="sub">Co lai: {profitable}/{len(valid)} coins</div>
</div>
<div class="summary-card">
  <div class="label">Net P&L tong ({len(valid)} coins)</div>
  <div class="value" style="color:{color(t_net)}">${t_net:+,.2f}</div>
  <div class="sub">TB/coin: ${t_net/len(valid):+,.2f} &nbsp;|&nbsp; ROI TB: {t_net/len(valid)/CAPITAL*100:+.2f}%</div>
</div>
</div>

<div class="chart-container">
  <div class="chart-title">EQUITY CURVE TONG HOP — {tf_label} ({len(valid)} coins)</div>
  <canvas id="chart_{agg_id}" height="220"></canvas>
  <div class="chart-legend">
    <span><span class="dot" style="background:#38bdf8"></span> Equity</span>
    <span>Von ban dau/coin: ${CAPITAL:,.0f} &times; {len(valid)} coins</span>
    <span>Net P&L tong: <b style="color:{color(t_net)}">${t_net:+,.2f}</b> (TB ${t_net/len(valid):+,.2f}/coin)</span>
  </div>
  <div class="chart-tooltip" id="tip_{agg_id}"></div>
</div>

<h3>Equity Curve theo tung coin</h3>
<div class="coin-charts">
"""
        for r in sorted(valid, key=lambda x: x["net"], reverse=True):
            cid = f"coin_{tf_label}_{r['label']}"
            eq  = r.get("eq_curve", [CAPITAL])
            all_chart_data[cid] = {"eq": downsample(eq), "start": CAPITAL}
            pc  = color(r["net"])
            html += f"""<div class="coin-chart-wrap">
  <div class="coin-header">
    <span><b>{r['label']}</b> — {r['trades']} lenh</span>
    <span style="color:{pc}">${r['net']:+,.2f} ({r['net']/CAPITAL*100:+.1f}%)</span>
  </div>
  <canvas id="chart_{cid}" height="120"></canvas>
  <div class="chart-tooltip" id="tip_{cid}"></div>
</div>
"""

        html += "</div>\n"

        html += f"""
<h3>Chi tiet theo coin</h3>
<table>
<thead>
<tr>
  <th>Coin</th><th>Lenh</th><th>Long</th><th>Short</th>
  <th>WR (co fee)</th><th>WR (khong fee)</th><th>Delta</th>
  <th>Net P&L</th><th>Fee</th><th>PF</th><th>Sharpe</th>
  <th>Max DD</th><th>Win/Lose Streak</th>
</tr>
</thead>
<tbody>
"""
        for r in sorted(valid, key=lambda x: x["net"], reverse=True):
            wr_diff = r["wr_nofee"] - r["wr"]
            html += f"""<tr>
  <td><b>{r['label']}</b></td>
  <td>{r['trades']}</td>
  <td>{r['longs']} <span style="color:#64748b">({r['l_wr']:.0f}%)</span></td>
  <td>{r['shorts']} <span style="color:#64748b">({r['s_wr']:.0f}%)</span></td>
  <td style="color:{wr_color(r['wr'])}">{r['wr']:.1f}%</td>
  <td style="color:{wr_color(r['wr_nofee'])}">{r['wr_nofee']:.1f}%</td>
  <td style="color:{color(wr_diff)}">{"+" if wr_diff > 0 else ""}{wr_diff:.1f}%</td>
  <td style="color:{color(r['net'])}">${r['net']:+,.2f}</td>
  <td style="color:#94a3b8">${r['fee']:,.2f}</td>
  <td style="color:{color(r['pf'] - 1)}">{r['pf']:.2f}</td>
  <td style="color:{sharpe_color(r['sharpe'])}">{r['sharpe']:.2f}</td>
  <td class="neg">{r['dd_pct']:.2f}%</td>
  <td>{r['max_win_streak']}W / {r['max_lose_streak']}L</td>
</tr>"""

        html += f"""
</tbody>
<tfoot>
<tr style="border-top:2px solid #334155;font-weight:700">
  <td>TONG</td><td>{t_trades}</td><td>{t_longs}</td><td>{t_shorts}</td>
  <td style="color:{wr_color(t_wr)}">{t_wr:.1f}%</td>
  <td style="color:{wr_color(t_wr_nofee)}">{t_wr_nofee:.1f}%</td>
  <td style="color:{color(t_wr_nofee - t_wr)}">{"+" if t_wr_nofee > t_wr else ""}{t_wr_nofee - t_wr:.1f}%</td>
  <td style="color:{color(t_net)}">${t_net:+,.2f}</td>
  <td>${t_fee:,.2f}</td>
  <td>—</td>
  <td style="color:{sharpe_color(avg_sharpe)}">{avg_sharpe:.2f}</td>
  <td class="neg">{max_dd_all:.2f}%</td>
  <td>—</td>
</tr>
</tfoot>
</table>

<h3>Thoat lenh</h3>
<table style="max-width:480px">
<tr><td>TP1 (chot 33% + doi SL ve BE)</td><td>{sum(r['tp1'] for r in valid)}</td></tr>
<tr><td>TP2 (chot them 33%)</td><td>{sum(r['tp2'] for r in valid)}</td></tr>
<tr><td>SL cung (-{HARD_SL_PCT * 100:.1f}%)</td><td>{sum(r['sl'] for r in valid)}</td></tr>
<tr><td>BE sau TP1</td><td>{sum(r['be'] for r in valid)}</td></tr>
<tr><td>REV (dao chieu)</td><td>{sum(r['rev'] for r in valid)}</td></tr>
<tr><td>OPEN (mark-to-market cuoi ky)</td><td>{sum(r.get('open', 0) for r in valid)}</td></tr>
</table>

</div>
"""

    html += """
<h2>So sanh Timeframe</h2>
<table>
<thead>
<tr><th>TF</th><th>Lenh</th><th>Long</th><th>Short</th>
<th>WR (fee)</th><th>WR (no fee)</th><th>Net P&L</th><th>Fee</th>
<th>Sharpe TB</th><th>Co lai</th><th>TB Net/coin</th><th>ROI TB</th></tr>
</thead>
<tbody>
"""
    for tf_label, results in all_results.items():
        valid = [r for r in results if r and r.get("trades", 0) > 0]
        if not valid:
            continue
        t_trades     = sum(r["trades"]  for r in valid)
        t_net        = sum(r["net"]     for r in valid)
        t_fee        = sum(r["fee"]     for r in valid)
        t_longs      = sum(r["longs"]   for r in valid)
        t_shorts     = sum(r["shorts"]  for r in valid)
        t_wins       = sum(int(r["wr"]       * r["trades"] / 100) for r in valid)
        t_wins_nofee = sum(int(r["wr_nofee"] * r["trades"] / 100) for r in valid)
        t_wr         = t_wins       / max(t_trades, 1) * 100
        t_wr_nofee   = t_wins_nofee / max(t_trades, 1) * 100
        avg_sharpe   = sum(r["sharpe"] for r in valid) / len(valid)
        profitable   = sum(1 for r in valid if r["net"] > 0)

        html += f"""<tr>
  <td><b>{tf_label}</b></td><td>{t_trades}</td><td>{t_longs}</td><td>{t_shorts}</td>
  <td style="color:{wr_color(t_wr)}">{t_wr:.1f}%</td>
  <td style="color:{wr_color(t_wr_nofee)}">{t_wr_nofee:.1f}%</td>
  <td style="color:{color(t_net)}">${t_net:+,.2f}</td>
  <td>${t_fee:,.2f}</td>
  <td style="color:{sharpe_color(avg_sharpe)}">{avg_sharpe:.2f}</td>
  <td>{profitable}/{len(valid)}</td>
  <td style="color:{color(t_net)}">${t_net/len(valid):+,.2f}</td>
  <td style="color:{color(t_net)}">{t_net/len(valid)/CAPITAL*100:+.2f}%</td>
</tr>"""

    chart_json = json.dumps(all_chart_data)
    html += f"""
</tbody></table>

<script>
const CHARTS = {chart_json};
const DPR = window.devicePixelRatio || 1;

function drawChart(canvasId, data, big) {{
  const canvas = document.getElementById('chart_' + canvasId);
  if (!canvas) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  const W = rect.width, H = big ? 220 : 120;
  canvas.width = W * DPR; canvas.height = H * DPR;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(DPR, DPR);
  const eq = data.eq, n = eq.length;
  if (n < 2) return;
  const startVal = data.start, endVal = eq[n-1];
  const pad = {{ t: big ? 35 : 20, r: 65, b: big ? 28 : 16, l: 12 }};
  const cW = W - pad.l - pad.r, cH = H - pad.t - pad.b;
  const minV = Math.min(...eq) * 0.9998, maxV = Math.max(...eq) * 1.0002;
  const range = maxV - minV || 1;
  function x(i) {{ return pad.l + (i / (n-1)) * cW; }}
  function y(v) {{ return pad.t + (1 - (v - minV) / range) * cH; }}
  ctx.strokeStyle = '#1e293b'; ctx.lineWidth = 1;
  const gl = big ? 5 : 3;
  for (let i = 0; i <= gl; i++) {{
    const gY = pad.t + (i / gl) * cH, gV = maxV - (i / gl) * range;
    ctx.beginPath(); ctx.moveTo(pad.l, gY); ctx.lineTo(W - pad.r, gY); ctx.stroke();
    ctx.fillStyle = '#475569'; ctx.font = '10px monospace'; ctx.textAlign = 'left';
    ctx.fillText('$' + gV.toFixed(0), W - pad.r + 4, gY + 3);
  }}
  ctx.strokeStyle = '#334155'; ctx.setLineDash([4,4]);
  ctx.beginPath(); ctx.moveTo(pad.l, y(startVal)); ctx.lineTo(W - pad.r, y(startVal)); ctx.stroke();
  ctx.setLineDash([]);
  const grad = ctx.createLinearGradient(0, pad.t, 0, H - pad.b);
  if (endVal >= startVal) {{
    grad.addColorStop(0, 'rgba(34,197,94,0.25)'); grad.addColorStop(1, 'rgba(34,197,94,0.02)');
  }} else {{
    grad.addColorStop(0, 'rgba(239,68,68,0.02)'); grad.addColorStop(1, 'rgba(239,68,68,0.25)');
  }}
  ctx.beginPath(); ctx.moveTo(x(0), y(eq[0]));
  for (let i = 1; i < n; i++) ctx.lineTo(x(i), y(eq[i]));
  ctx.lineTo(x(n-1), H-pad.b); ctx.lineTo(x(0), H-pad.b);
  ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
  ctx.beginPath(); ctx.moveTo(x(0), y(eq[0]));
  for (let i = 1; i < n; i++) ctx.lineTo(x(i), y(eq[i]));
  ctx.strokeStyle = endVal >= startVal ? '#22c55e' : '#ef4444';
  ctx.lineWidth = big ? 2 : 1.5; ctx.stroke();
  const tip = document.getElementById('tip_' + canvasId);
  if (tip) {{
    canvas.addEventListener('mousemove', function(e) {{
      const br = canvas.getBoundingClientRect();
      const mx = e.clientX - br.left;
      const idx = Math.round(((mx - pad.l) / cW) * (n-1));
      if (idx >= 0 && idx < n) {{
        const val = eq[idx], pnl = val - startVal, pct = pnl / startVal * 100;
        tip.style.display = 'block';
        tip.style.left = (mx + 12) + 'px';
        tip.style.top  = (y(val) - 20) + 'px';
        tip.innerHTML  = '<b>$' + val.toFixed(2) + '</b><br>'
          + '<span style="color:' + (pnl >= 0 ? '#22c55e' : '#ef4444') + '">'
          + (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2)
          + ' (' + (pnl >= 0 ? '+' : '') + pct.toFixed(2) + '%)</span><br>'
          + '<span style="color:#64748b">Trade ' + idx + '/' + (n-1) + '</span>';
      }}
    }});
    canvas.addEventListener('mouseleave', function() {{ tip.style.display = 'none'; }});
  }}
}}

window.addEventListener('load', function() {{
  for (const [id, data] of Object.entries(CHARTS))
    drawChart(id, data, id.startsWith('agg_'));
}});
window.addEventListener('resize', function() {{
  for (const [id, data] of Object.entries(CHARTS))
    drawChart(id, data, id.startsWith('agg_'));
}});
</script>

<footer>
Alpha-1 Bangoc Backtest | Binance Futures | Fee: 0.0357% / side | """ + f'{START.strftime("%Y-%m-%d")} &rarr; {END.strftime("%Y-%m-%d")}' + """ | Generated: """ + datetime.now().strftime("%Y-%m-%d %H:%M") + """
</footer>
</div></body></html>"""

    return html


# ── MAIN ──────────────────────────────────────────────
def main():
    pairs = [(symbol, tf) for tf in TIMEFRAMES for symbol in SYMBOLS]
    total = len(pairs)

    print(f"\n  ALPHA-1 BANGOC BACKTEST - Binance Futures")
    print(f"  {START.strftime('%Y-%m-%d')} → {END.strftime('%Y-%m-%d')}")
    print(f"  Symbols: {len(SYMBOLS)} | TF: {', '.join(tf['label'] for tf in TIMEFRAMES)}")
    print(f"  Pairs: {total} | Capital/pair: ${CAPITAL:,.0f} | Fixed size: ${SIZE:,.0f}")
    print(f"  Fee: {FEE_RATE*100:.4f}% / side")
    print(f"  Indi1: SMA {INDI1_SMA_LEN} | norm {INDI1_NORM_WINDOW} | threshold ±{INDI1_THRESHOLD}")
    print(f"  Indi2: median/POC {INDI2_LOOKBACK} | same-color entry/reversal")
    print(f"  Risk: SL -{HARD_SL_PCT*100:.1f}% | TP1 +{TP1_PCT*100:.1f}% close 33% + BE"
          f" | TP2 +{TP2_PCT*100:.1f}% close 33%")
    print(f"  Workers: {MAX_WORKERS} | API concurrent: {MAX_API_CONC}\n")

    # ── PHASE 1: FETCH ALL ──────────────────────────────
    print(f"  ── PHASE 1: Fetching {total} symbol/timeframe pairs...")
    t0         = time.time()
    p_lock     = Lock()
    c_lock     = Lock()
    candles_map: dict[tuple[str, str], list] = {}
    counters   = {"done": 0, "ok": 0, "skip": 0, "err": 0}

    def fetch_one(pair):
        sym, tf = pair
        pair_label = f"{sym.replace('USDT', '')}/{tf['label']}"
        backtest_bars = int((END - START).total_seconds() / 60 / tf["mins"])
        min_bars = WARMUP + max(10, backtest_bars // 2)
        try:
            candles = fetch_from_binance(sym, tf)
        except Exception as e:
            with c_lock:
                counters["done"] += 1
                counters["err"]  += 1
                done = counters["done"]
            with p_lock:
                print(f"  [{done:>3}/{total}] ERR {pair_label:<14} {str(e)[:80]}")
            return

        with c_lock:
            counters["done"] += 1
            done = counters["done"]
            if len(candles) >= min_bars:
                candles_map[(sym, tf["label"])] = candles
                counters["ok"] += 1
            else:
                counters["skip"] += 1

        with p_lock:
            print(
                f"  [{done:>3}/{total}] {pair_label:<14} {len(candles):>6,} bars"
                f"  ok={counters['ok']} skip={counters['skip']} err={counters['err']}"
            )

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        list(executor.map(fetch_one, pairs))

    t_fetch = time.time() - t0
    ready   = len(candles_map)
    print(f"\n  Fetch done: {ready} pairs ready  {counters['skip']} skip  {counters['err']} err  {t_fetch:.1f}s")

    if not candles_map:
        print("  No symbol/timeframe pairs to backtest.")
        return

    # ── PHASE 2: BACKTEST ALL ───────────────────────────
    print(f"\n  ── PHASE 2: Backtesting {ready} pairs...")
    t1       = time.time()
    bt_lock  = Lock()
    bt_stats = {"done": 0, "trades": 0, "net": 0.0, "wins": 0}
    all_results: dict[str, list[dict]] = {tf["label"]: [] for tf in TIMEFRAMES}

    def bt_one(item: tuple):
        (sym, tf_label), candles = item
        label = sym.replace("USDT", "")
        t_s   = time.time()

        trades_list, filtered = run_backtest(candles)
        del candles
        elapsed = time.time() - t_s

        with bt_lock:
            bt_stats["done"] += 1
            done = bt_stats["done"]

        if not trades_list:
            with p_lock:
                print(f"  [{done:>3}/{ready}] {label}/{tf_label:<10}  0 trades  {elapsed:.2f}s")
            return

        stats = compute_stats(trades_list)
        stats["label"]      = label
        stats["tf"]         = tf_label
        stats["filtered"]   = filtered
        stats["trade_rows"] = _make_trade_rows(trades_list, label, tf_label)

        with bt_lock:
            all_results[tf_label].append(stats)
            bt_stats["trades"] += stats["trades"]
            bt_stats["net"]    += stats["net"]
            bt_stats["wins"]   += int(stats["wr"] * stats["trades"] / 100)

        with p_lock:
            print(
                f"  [{done:>3}/{ready}] {label}/{tf_label:<10}"
                f"  {stats['trades']:>4}lnh"
                f"  WR {stats['wr']:>5.1f}%(nf {stats['wr_nofee']:.1f}%)"
                f"  ${stats['net']:>+9,.2f}"
                f"  Sh {stats['sharpe']:>5.2f}"
                f"  DD {stats['dd_pct']:>5.2f}%"
                f"  {elapsed:.2f}s"
            )

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        list(executor.map(bt_one, list(candles_map.items())))
    candles_map.clear()

    t_bt   = time.time() - t1
    wr_all = bt_stats["wins"] / bt_stats["trades"] * 100 if bt_stats["trades"] else 0
    result_count = sum(len(results) for results in all_results.values())
    print(f"\n  Backtest done: {result_count} pairs  {bt_stats['trades']} lenh"
          f"  net=${bt_stats['net']:+,.2f}  WR {wr_all:.1f}%  {t_bt:.1f}s")

    # ── OUTPUTS ────────────────────────────────────────
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    _csv_fields = ["trade_id", "symbol", "timeframe", "side", "entry_time", "exit_time",
                   "entry", "exit", "size", "close_pct", "remaining_size", "is_partial",
                   "gross", "fee", "net", "result"]
    result_rows = [
        result
        for tf_results in all_results.values()
        for result in tf_results
    ]

    print("\n  Writing M15/H1 trade history CSVs per coin...")
    for sym in SYMBOLS:
        label = sym.replace("USDT", "")
        coin_dir = os.path.join(OUTPUT_DIR, label)
        os.makedirs(coin_dir, exist_ok=True)
        combined_path = os.path.join(coin_dir, "tradehistory.csv")
        if os.path.isfile(combined_path):
            os.remove(combined_path)

        for tf in TIMEFRAMES:
            tf_label = tf["label"]
            rows = [
                row
                for result in result_rows
                if result["label"] == label and result["tf"] == tf_label
                for row in result.get("trade_rows", [])
            ]
            rows.sort(key=lambda row: row["entry_time"])
            csv_path = os.path.join(coin_dir, f"tradehistory_{tf_label.lower()}.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_csv_fields)
                writer.writeheader()
                writer.writerows(rows)
            print(f"  >>> {label:<5} {tf_label:<3} {csv_path} ({len(rows):,} executions)")

    print("  Generating combined summary + equity HTML...")
    html     = generate_html(all_results)
    out_path = os.path.join(OUTPUT_DIR, "report.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  >>> HTML report:     {out_path}")
    print(f"  >>> Mo bang: open '{out_path}'\n")


if __name__ == "__main__":
    main()
