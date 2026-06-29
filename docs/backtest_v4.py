#!/usr/bin/env python3
"""Alpha-1 Backtest V4 — Fee 0.0357% (0.07%×51%), HTML report
   Sharpe, WR co/khong fee, Long/Short breakdown
"""

import urllib.request, json, time, sys, math, os
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "SOLUSDT", "DOTUSDT", "MATICUSDT", "AVAXUSDT",
    "LINKUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT", "ETCUSDT",
    "XLMUSDT", "NEARUSDT", "FILUSDT", "TRXUSDT", "SHIBUSDT",
]
TIMEFRAMES = [
    {"id": "1h",  "label": "H1",  "mins": 60},
    {"id": "15m", "label": "M15", "mins": 15},
]

START    = datetime(2023, 1, 1, tzinfo=timezone.utc)
END      = datetime(2026, 4, 1, tzinfo=timezone.utc)

CAPITAL  = 10_000.0
SIZE     = 1000.0
MIN_SIZE = 500.0

# ── FEE DA FIX: 0.07% × (1 - 49%) = 0.0357% ──
FEE_RATE_RAW  = 0.0007     # 0.07%
FEE_DISCOUNT  = 0.49       # 49% giam
FEE_RATE      = FEE_RATE_RAW * (1 - FEE_DISCOUNT)   # = 0.000357

SMA_LEN     = 50
ATR_LEN     = 200
GR_LOOKBACK = 30
GR_PCT      = 70.0
TRAIL_ATR   = 0.5
TP_RATIO    = 3
NORM_WINDOW = 252
WARMUP      = 310
THRESHOLD   = 0.15


# ── FETCH ─────────────────────────────────────────────
def fetch_all(symbol, interval, start_ms, end_ms):
    candles = []
    cur = start_ms
    while cur < end_ms:
        url = (f"https://api.binance.com/api/v3/klines"
               f"?symbol={symbol}&interval={interval}"
               f"&startTime={cur}&endTime={end_ms}&limit=1000")
        req = urllib.request.Request(url, headers={"User-Agent": "A1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        if not data:
            break
        for k in data:
            candles.append({
                "time": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]),  "close": float(k[4]),
            })
        cur = data[-1][0] + 1
        sys.stdout.write(f"\r    Tai... {len(candles)} nen")
        sys.stdout.flush()
        time.sleep(0.05)
    sys.stdout.write(f"\r    {len(candles)} nen OK            \n")
    return candles


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

def calc_atr(hi, lo, cl, p):
    n = len(cl)
    trs = [0.0] * n
    for i in range(1, n):
        trs[i] = max(hi[i] - lo[i], abs(hi[i] - cl[i-1]), abs(lo[i] - cl[i-1]))
    out = [None] * n
    s = 0.0
    for i in range(1, n):
        s += trs[i]
        if i > p:
            s -= trs[i - p]
        if i >= p:
            out[i] = s / p
    return out

def calc_median(vals, p):
    n = len(vals)
    out = [None] * n
    for i in range(p - 1, n):
        w = sorted(vals[i - p + 1 : i + 1])
        m = p // 2
        out[i] = (w[m-1] + w[m]) / 2 if p % 2 == 0 else w[m]
    return out


# ── BACKTEST ──────────────────────────────────────────
def calc_pnl(side, entry_p, exit_p, size, fee_rate):
    """Tinh P&L thuc ($), tru fee."""
    qty = size / entry_p
    if side == "LONG":
        gross = qty * (exit_p - entry_p)
    else:
        gross = qty * (entry_p - exit_p)
    fee_in  = fee_rate * size
    fee_out = fee_rate * (qty * exit_p)
    net = gross - fee_in - fee_out
    return net, fee_in + fee_out, gross


def run_backtest(candles):
    n = len(candles)
    cl = [c["close"] for c in candles]
    hi = [c["high"] for c in candles]
    lo = [c["low"] for c in candles]

    avg   = calc_sma(cl, SMA_LEN)
    atr_v = calc_atr(hi, lo, cl, ATR_LEN)
    poc   = calc_median(cl, GR_LOOKBACK)

    adiff = [None] * n
    for i in range(5, n):
        if avg[i] is not None and avg[i-5] is not None:
            adiff[i] = avg[i] - avg[i-5]

    acol = [None] * n
    for i in range(NORM_WINDOW, n):
        ds = [d for d in adiff[i - NORM_WINDOW + 1 : i + 1] if d is not None]
        if ds:
            mx = max(ds)
            if abs(mx) > 1e-12 and adiff[i] is not None:
                acol[i] = adiff[i] / mx

    trend     = None
    in_trade  = False
    tlong     = False
    ep = sl = tp = td = 0.0
    et        = None
    hse       = 0.0
    lse       = 1e18
    trades    = []
    filtered  = 0
    cur_size  = SIZE
    cur_eq    = CAPITAL
    trade_size = SIZE

    def close_trade(side, entry_p, exit_p, result, exit_time):
        nonlocal in_trade, cur_size, cur_eq
        net, fee, gross = calc_pnl(side, entry_p, exit_p, trade_size, FEE_RATE)
        # Also calc without fee for WR comparison
        net_nofee = gross
        trades.append({
            "side": side, "entry": entry_p, "exit": exit_p,
            "result": result, "net": net, "fee": fee, "gross": gross,
            "net_nofee": net_nofee, "size": trade_size,
            "entry_time": et, "exit_time": exit_time,
        })
        in_trade = False
        cur_eq += net
        cur_size += 0.30 * net
        max_size = 0.30 * cur_eq
        cur_size = max(MIN_SIZE, min(cur_size, max_size))

    for i in range(1, n):
        ac, acp, ai, pi = acol[i], acol[i-1], atr_v[i], poc[i]
        if None in (ac, acp, ai, pi):
            continue

        pt = trend
        if acp <= THRESHOLD and ac > THRESHOLD and trend is not True:
            trend = True
        if acp >= -THRESHOLD and ac < -THRESHOLD and trend is True:
            trend = False
        tc = trend != pt

        if in_trade and not tc:
            cut = False
            if tlong:
                if ac < -THRESHOLD or cl[i] < pi * 0.98:
                    cut = True
            else:
                if ac > THRESHOLD or cl[i] > pi * 1.02:
                    cut = True
            if cut:
                close_trade("LONG" if tlong else "SHORT", ep, cl[i], "CUT", candles[i]["time"])

        if in_trade and not tc:
            if tlong:
                hse = max(hse, hi[i - 1])
                sl = max(sl, hse - td)
                if lo[i] <= sl:
                    close_trade("LONG", ep, sl, "SL", candles[i]["time"])
                elif hi[i] >= tp:
                    close_trade("LONG", ep, tp, "TP", candles[i]["time"])
            else:
                lse = min(lse, lo[i - 1])
                sl = min(sl, lse + td)
                if hi[i] >= sl:
                    close_trade("SHORT", ep, sl, "SL", candles[i]["time"])
                elif lo[i] <= tp:
                    close_trade("SHORT", ep, tp, "TP", candles[i]["time"])

        if tc and trend is not None:
            gb = cl[i] > pi * 1.02
            gbe = cl[i] < pi * 0.98
            ce = (trend and gb) or (not trend and gbe)
            if ce:
                if in_trade:
                    close_trade("LONG" if tlong else "SHORT", ep, cl[i], "REV", candles[i]["time"])
                trade_size = cur_size
                ep = cl[i]
                et = candles[i]["time"]
                in_trade = True
                tlong = trend
                td = TRAIL_ATR * ai
                if trend:
                    sl = ep - td; tp = ep + td * TP_RATIO
                    hse = hi[i]; lse = lo[i]
                else:
                    sl = ep + td; tp = ep - td * TP_RATIO
                    lse = lo[i]; hse = hi[i]
            else:
                if candles[i]["time"] >= START:
                    filtered += 1

    if in_trade:
        close_trade("LONG" if tlong else "SHORT", ep, cl[-1], "OPEN", candles[-1]["time"])

    trades = [t for t in trades if t["entry_time"] >= START and t["entry_time"] < END]
    return trades, filtered


def compute_stats(trades):
    """Compute full stats dict from trades list."""
    if not trades:
        return None

    wins       = [t for t in trades if t["net"] > 0]
    losses     = [t for t in trades if t["net"] <= 0]
    wins_nofee = [t for t in trades if t["net_nofee"] > 0]
    longs      = [t for t in trades if t["side"] == "LONG"]
    shorts     = [t for t in trades if t["side"] == "SHORT"]

    total_net  = sum(t["net"] for t in trades)
    total_fee  = sum(t["fee"] for t in trades)
    total_gross = sum(t["gross"] for t in trades)
    wr         = len(wins) / len(trades) * 100
    wr_nofee   = len(wins_nofee) / len(trades) * 100

    gw = sum(t["net"] for t in wins) if wins else 0
    gl = abs(sum(t["net"] for t in losses)) if losses else 0.001
    pf = gw / gl

    # Equity curve + max DD (with timestamps for chart)
    equity = CAPITAL; peak = CAPITAL; max_dd = 0; max_dd_pct = 0
    eq_curve = [CAPITAL]
    eq_times = [trades[0]["entry_time"].strftime("%Y-%m-%d")]
    dd_curve = [0.0]
    for t in trades:
        equity += t["net"]
        eq_curve.append(equity)
        eq_times.append(t["exit_time"].strftime("%Y-%m-%d"))
        peak = max(peak, equity)
        dd = peak - equity
        dd_curve.append(-dd)
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak * 100

    # Sharpe Ratio (annualized)
    returns = []
    for t in trades:
        r = t["net"] / t["size"] if t["size"] > 0 else 0
        returns.append(r)
    if len(returns) > 1:
        avg_r = sum(returns) / len(returns)
        var_r = sum((r - avg_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 0.001
        # Annualize: assume ~252 trading days
        trades_per_year = len(trades) / ((END - START).days / 365.25)
        sharpe = (avg_r / std_r) * math.sqrt(trades_per_year) if std_r > 0 else 0
    else:
        sharpe = 0

    # Long/Short breakdown
    l_wins = sum(1 for t in longs if t["net"] > 0)
    s_wins = sum(1 for t in shorts if t["net"] > 0)
    l_wr = l_wins / max(len(longs), 1) * 100
    s_wr = s_wins / max(len(shorts), 1) * 100
    l_net = sum(t["net"] for t in longs)
    s_net = sum(t["net"] for t in shorts)
    l_wr_nofee = sum(1 for t in longs if t["net_nofee"] > 0) / max(len(longs), 1) * 100
    s_wr_nofee = sum(1 for t in shorts if t["net_nofee"] > 0) / max(len(shorts), 1) * 100

    # Exit type counts
    tp_n  = sum(1 for t in trades if t["result"] == "TP")
    sl_n  = sum(1 for t in trades if t["result"] == "SL")
    cut_n = sum(1 for t in trades if t["result"] == "CUT")
    rev_n = sum(1 for t in trades if t["result"] == "REV")

    # Streaks
    max_win_streak = max_lose_streak = cur_w = cur_l = 0
    for t in trades:
        if t["net"] > 0:
            cur_w += 1; cur_l = 0
            max_win_streak = max(max_win_streak, cur_w)
        else:
            cur_l += 1; cur_w = 0
            max_lose_streak = max(max_lose_streak, cur_l)

    # Monthly P&L
    months = {}
    for t in trades:
        m = t["entry_time"].strftime("%Y-%m")
        if m not in months:
            months[m] = {"net": 0, "cnt": 0, "wins": 0, "fee": 0}
        months[m]["net"] += t["net"]
        months[m]["cnt"] += 1
        months[m]["fee"] += t["fee"]
        if t["net"] > 0:
            months[m]["wins"] += 1

    return {
        "trades": len(trades), "wr": wr, "wr_nofee": wr_nofee,
        "net": total_net, "fee": total_fee, "gross": total_gross,
        "final": CAPITAL + total_net, "pf": pf,
        "dd": max_dd, "dd_pct": max_dd_pct, "sharpe": sharpe,
        "longs": len(longs), "l_wr": l_wr, "l_net": l_net, "l_wr_nofee": l_wr_nofee,
        "shorts": len(shorts), "s_wr": s_wr, "s_net": s_net, "s_wr_nofee": s_wr_nofee,
        "tp": tp_n, "sl": sl_n, "cut": cut_n, "rev": rev_n,
        "max_win_streak": max_win_streak, "max_lose_streak": max_lose_streak,
        "eq_curve": eq_curve, "eq_times": eq_times, "dd_curve": dd_curve,
        "months": months,
    }


# ── RUN 1 COIN ───────────────────────────────────────
def run_coin(sym, interval, mins):
    start_ms = int(START.timestamp() * 1000)
    end_ms   = int(END.timestamp() * 1000)
    warmup_ms = WARMUP * mins * 60 * 1000
    t0 = time.time()
    try:
        candles = fetch_all(sym, interval, start_ms - warmup_ms, end_ms)
    except Exception as e:
        print(f"      LOI: {e}")
        return None
    if len(candles) < WARMUP + 100:
        print(f"      Bo qua — {len(candles)} nen")
        return None
    trades, filtered = run_backtest(candles)
    secs = time.time() - t0
    if not trades:
        return {"label": sym.replace('USDT',''), "trades": 0, "secs": secs}
    stats = compute_stats(trades)
    stats["label"] = sym.replace('USDT', '')
    stats["secs"] = secs
    stats["filtered"] = filtered
    return stats


# ── HTML REPORT ───────────────────────────────────────
def generate_html(all_results):
    """Generate complete HTML report with equity curve charts."""

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

    # Downsample equity curve to max N points for chart performance
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
<title>Alpha-1 Backtest V4 — Report</title>
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
.neu { color: #94a3b8; }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }
.summary-card { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 14px; }
.summary-card .label { color: #64748b; font-size: 11px; text-transform: uppercase; }
.summary-card .value { font-size: 20px; font-weight: 700; margin-top: 4px; }
.summary-card .sub { color: #64748b; font-size: 11px; margin-top: 2px; }
.tf-section { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 20px; margin-bottom: 24px; }
.wr-compare { display: inline-block; margin-left: 8px; font-size: 11px; color: #64748b; }
.wr-compare .diff { font-weight: 600; }
.chart-container { position: relative; background: #0f172a; border: 1px solid #334155; border-radius: 8px; margin: 16px 0; padding: 0; overflow: hidden; }
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
<h1>ALPHA-1 BACKTEST V4</h1>
<p class="subtitle">Fee da fix: 0.07% &times; (1 - 49%) = <b style="color:#fbbf24">0.0357%</b> / lenh &nbsp;|&nbsp; """ + f'{START.strftime("%Y-%m-%d")} &rarr; {END.strftime("%Y-%m-%d")}' + """</p>

<div class="config">
<div><span>Von:</span> <b>$""" + f'{CAPITAL:,.0f}' + """</b></div>
<div><span>Size:</span> <b>$""" + f'{SIZE:,.0f}' + """ &rarr; dynamic</b></div>
<div><span>Fee goc:</span> <b>0.07%</b></div>
<div><span>Giam:</span> <b class="fee-highlight">49%</b></div>
<div><span>Fee thuc:</span> <b class="fee-highlight">0.0357%</b></div>
<div><span>SL:</span> <b>trail """ + f'{TRAIL_ATR}' + """&times;ATR</b></div>
<div><span>TP:</span> <b>""" + f'{TRAIL_ATR*TP_RATIO}' + """&times;ATR (R:R """ + f'{TP_RATIO}' + """:1)</b></div>
<div><span>Threshold:</span> <b>&plusmn;""" + f'{THRESHOLD}' + """</b></div>
</div>
"""

    chart_id = 0
    all_chart_data = {}  # store chart data for JS

    for tf_label, results in all_results.items():
        valid = [r for r in results if r and r.get("trades", 0) > 0]
        if not valid:
            continue

        # Aggregate stats
        t_trades = sum(r["trades"] for r in valid)
        t_net = sum(r["net"] for r in valid)
        t_fee = sum(r["fee"] for r in valid)
        t_gross = sum(r["gross"] for r in valid)
        t_wins = sum(int(r["wr"] * r["trades"] / 100) for r in valid)
        t_wins_nofee = sum(int(r["wr_nofee"] * r["trades"] / 100) for r in valid)
        t_longs = sum(r["longs"] for r in valid)
        t_shorts = sum(r["shorts"] for r in valid)
        t_wr = t_wins / max(t_trades, 1) * 100
        t_wr_nofee = t_wins_nofee / max(t_trades, 1) * 100
        profitable = sum(1 for r in valid if r["net"] > 0)
        avg_sharpe = sum(r["sharpe"] for r in valid) / len(valid)
        max_dd_all = max(r["dd_pct"] for r in valid)

        # Build aggregate equity curve (sum of all coins)
        max_len = max(len(r.get("eq_curve", [])) for r in valid)
        agg_eq = [CAPITAL * len(valid)]  # starting equity = capital × coins
        for i in range(1, max_len):
            val = 0
            for r in valid:
                ec = r.get("eq_curve", [CAPITAL])
                val += ec[i] if i < len(ec) else ec[-1]
            agg_eq.append(val)

        agg_id = f"agg_{tf_label}"
        all_chart_data[agg_id] = {
            "eq": downsample(agg_eq),
            "start": CAPITAL * len(valid),
        }

        html += f"""
<div class="tf-section">
<h2>{tf_label} — Timeframe</h2>

<div class="summary-grid">
<div class="summary-card">
  <div class="label">Tong lenh</div>
  <div class="value">{t_trades}</div>
  <div class="sub">Long: {t_longs} | Short: {t_shorts}</div>
</div>
<div class="summary-card">
  <div class="label">Win Rate (co fee)</div>
  <div class="value" style="color:{wr_color(t_wr)}">{t_wr:.1f}%</div>
  <div class="sub">Khong fee: {t_wr_nofee:.1f}% <span class="wr-compare">(<span class="diff" style="color:{color(t_wr_nofee - t_wr)}">{"+" if t_wr_nofee > t_wr else ""}{t_wr_nofee - t_wr:.1f}%</span>)</span></div>
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
  <div class="label">Von cuoi (tong)</div>
  <div class="value" style="color:{color(t_net)}">${CAPITAL + t_net:,.2f}</div>
  <div class="sub">ROI: {t_net/CAPITAL*100:+.2f}%</div>
</div>
</div>

<!-- AGGREGATE EQUITY CURVE -->
<div class="chart-container">
  <div class="chart-title">EQUITY CURVE TONG HOP — {tf_label} ({len(valid)} coins)</div>
  <canvas id="chart_{agg_id}" height="220"></canvas>
  <div class="chart-legend">
    <span><span class="dot" style="background:#38bdf8"></span> Equity</span>
    <span><span class="dot" style="background:#334155"></span> Von ban dau: ${CAPITAL * len(valid):,.0f}</span>
    <span>Von cuoi: <b style="color:{color(t_net)}">${agg_eq[-1]:,.2f}</b></span>
  </div>
  <div class="chart-tooltip" id="tip_{agg_id}"></div>
</div>

<!-- PER-COIN EQUITY CURVES -->
<h3>Equity Curve theo tung coin</h3>
<div class="coin-charts">
"""
        for r in sorted(valid, key=lambda x: x["net"], reverse=True):
            cid = f"coin_{tf_label}_{r['label']}"
            eq = r.get("eq_curve", [CAPITAL])
            all_chart_data[cid] = {"eq": downsample(eq), "start": CAPITAL}
            pnl_color = color(r["net"])
            html += f"""<div class="coin-chart-wrap">
  <div class="coin-header">
    <span><b>{r['label']}</b> — {r['trades']} lenh</span>
    <span style="color:{pnl_color}">${r['net']:+,.2f} ({r['net']/CAPITAL*100:+.1f}%)</span>
  </div>
  <canvas id="chart_{cid}" height="120"></canvas>
  <div class="chart-tooltip" id="tip_{cid}"></div>
</div>
"""
            chart_id += 1

        html += "</div><!-- /coin-charts -->\n"

        # Table
        html += f"""
<h3>Chi tiet theo coin</h3>
<table>
<thead>
<tr>
  <th>Coin</th><th>Lenh</th><th>Long</th><th>Short</th>
  <th>WR (co fee)</th><th>WR (khong fee)</th><th>WR chenh lech</th>
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
<tr style="border-top:2px solid #334155; font-weight:700">
  <td>TONG</td><td>{t_trades}</td><td>{t_longs}</td><td>{t_shorts}</td>
  <td style="color:{wr_color(t_wr)}">{t_wr:.1f}%</td>
  <td style="color:{wr_color(t_wr_nofee)}">{t_wr_nofee:.1f}%</td>
  <td style="color:{color(t_wr_nofee - t_wr)}">{"+" if t_wr_nofee > t_wr else ""}{t_wr_nofee - t_wr:.1f}%</td>
  <td style="color:{color(t_net)}">${t_net:+,.2f}</td>
  <td style="color:#94a3b8">${t_fee:,.2f}</td>
  <td>—</td>
  <td style="color:{sharpe_color(avg_sharpe)}">{avg_sharpe:.2f}</td>
  <td class="neg">{max_dd_all:.2f}%</td>
  <td>—</td>
</tr>
</tfoot>
</table>

<h3>Thoat lenh tong hop</h3>
<table style="max-width:500px">
<tr><td>TP (chot loi)</td><td>{sum(r['tp'] for r in valid)}</td></tr>
<tr><td>SL (trailing)</td><td>{sum(r['sl'] for r in valid)}</td></tr>
<tr><td>CUT (cat nguoc)</td><td>{sum(r['cut'] for r in valid)}</td></tr>
<tr><td>REV (dao chieu)</td><td>{sum(r['rev'] for r in valid)}</td></tr>
</table>

</div><!-- /tf-section -->
"""

    # Grand summary
    html += """
<h2>So sanh Timeframe</h2>
<table>
<thead>
<tr><th>TF</th><th>Lenh</th><th>Long</th><th>Short</th>
<th>WR (fee)</th><th>WR (no fee)</th><th>Net P&L</th><th>Fee</th>
<th>Sharpe TB</th><th>Co lai</th><th>Von cuoi</th></tr>
</thead>
<tbody>
"""
    for tf_label, results in all_results.items():
        valid = [r for r in results if r and r.get("trades", 0) > 0]
        if not valid:
            continue
        t_trades = sum(r["trades"] for r in valid)
        t_net = sum(r["net"] for r in valid)
        t_fee = sum(r["fee"] for r in valid)
        t_longs = sum(r["longs"] for r in valid)
        t_shorts = sum(r["shorts"] for r in valid)
        t_wins = sum(int(r["wr"] * r["trades"] / 100) for r in valid)
        t_wins_nofee = sum(int(r["wr_nofee"] * r["trades"] / 100) for r in valid)
        t_wr = t_wins / max(t_trades, 1) * 100
        t_wr_nofee = t_wins_nofee / max(t_trades, 1) * 100
        avg_sharpe = sum(r["sharpe"] for r in valid) / len(valid)
        profitable = sum(1 for r in valid if r["net"] > 0)

        html += f"""<tr>
  <td><b>{tf_label}</b></td><td>{t_trades}</td><td>{t_longs}</td><td>{t_shorts}</td>
  <td style="color:{wr_color(t_wr)}">{t_wr:.1f}%</td>
  <td style="color:{wr_color(t_wr_nofee)}">{t_wr_nofee:.1f}%</td>
  <td style="color:{color(t_net)}">${t_net:+,.2f}</td>
  <td>${t_fee:,.2f}</td>
  <td style="color:{sharpe_color(avg_sharpe)}">{avg_sharpe:.2f}</td>
  <td>{profitable}/{len(valid)}</td>
  <td style="color:{color(t_net)}">${CAPITAL+t_net:,.2f}</td>
</tr>"""

    html += """
</tbody>
</table>
"""

    # Inject chart data as JSON and drawing script
    chart_json = json.dumps(all_chart_data)
    html += f"""
<script>
const CHARTS = {chart_json};
const DPR = window.devicePixelRatio || 1;

function drawChart(canvasId, data, big) {{
  const canvas = document.getElementById('chart_' + canvasId);
  if (!canvas) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  const W = rect.width;
  const H = big ? 220 : 120;
  canvas.width = W * DPR;
  canvas.height = H * DPR;
  canvas.style.width = W + 'px';
  canvas.style.height = H + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(DPR, DPR);

  const eq = data.eq;
  const n = eq.length;
  if (n < 2) return;
  const startVal = data.start;
  const endVal = eq[eq.length - 1];

  const pad = {{ t: big ? 35 : 20, r: 60, b: big ? 28 : 16, l: 12 }};
  const cW = W - pad.l - pad.r;
  const cH = H - pad.t - pad.b;

  const minV = Math.min(...eq) * 0.9998;
  const maxV = Math.max(...eq) * 1.0002;
  const range = maxV - minV || 1;

  function x(i) {{ return pad.l + (i / (n - 1)) * cW; }}
  function y(v) {{ return pad.t + (1 - (v - minV) / range) * cH; }}

  // Grid lines
  ctx.strokeStyle = '#1e293b';
  ctx.lineWidth = 1;
  const gridLines = big ? 5 : 3;
  for (let i = 0; i <= gridLines; i++) {{
    const gY = pad.t + (i / gridLines) * cH;
    const gVal = maxV - (i / gridLines) * range;
    ctx.beginPath();
    ctx.moveTo(pad.l, gY);
    ctx.lineTo(W - pad.r, gY);
    ctx.stroke();
    ctx.fillStyle = '#475569';
    ctx.font = '10px monospace';
    ctx.textAlign = 'left';
    ctx.fillText('$' + gVal.toFixed(0), W - pad.r + 4, gY + 3);
  }}

  // Starting capital line
  ctx.strokeStyle = '#334155';
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(pad.l, y(startVal));
  ctx.lineTo(W - pad.r, y(startVal));
  ctx.stroke();
  ctx.setLineDash([]);

  // Gradient fill under curve
  const grad = ctx.createLinearGradient(0, pad.t, 0, H - pad.b);
  if (endVal >= startVal) {{
    grad.addColorStop(0, 'rgba(34,197,94,0.25)');
    grad.addColorStop(1, 'rgba(34,197,94,0.02)');
  }} else {{
    grad.addColorStop(0, 'rgba(239,68,68,0.02)');
    grad.addColorStop(1, 'rgba(239,68,68,0.25)');
  }}
  ctx.beginPath();
  ctx.moveTo(x(0), y(eq[0]));
  for (let i = 1; i < n; i++) ctx.lineTo(x(i), y(eq[i]));
  ctx.lineTo(x(n - 1), H - pad.b);
  ctx.lineTo(x(0), H - pad.b);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Equity line
  ctx.beginPath();
  ctx.moveTo(x(0), y(eq[0]));
  for (let i = 1; i < n; i++) ctx.lineTo(x(i), y(eq[i]));
  ctx.strokeStyle = endVal >= startVal ? '#22c55e' : '#ef4444';
  ctx.lineWidth = big ? 2 : 1.5;
  ctx.stroke();

  // Tooltip on hover
  const tip = document.getElementById('tip_' + canvasId);
  if (tip) {{
    canvas.addEventListener('mousemove', function(e) {{
      const br = canvas.getBoundingClientRect();
      const mx = e.clientX - br.left;
      const idx = Math.round(((mx - pad.l) / cW) * (n - 1));
      if (idx >= 0 && idx < n) {{
        const val = eq[idx];
        const pnl = val - startVal;
        const pct = (pnl / startVal * 100);
        tip.style.display = 'block';
        tip.style.left = (mx + 12) + 'px';
        tip.style.top = (y(val) - 20) + 'px';
        tip.innerHTML = '<b>$' + val.toFixed(2) + '</b><br>'
          + '<span style="color:' + (pnl >= 0 ? '#22c55e' : '#ef4444') + '">'
          + (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2)
          + ' (' + (pnl >= 0 ? '+' : '') + pct.toFixed(2) + '%)</span><br>'
          + '<span style="color:#64748b">Trade ' + idx + '/' + (n-1) + '</span>';
      }}
    }});
    canvas.addEventListener('mouseleave', function() {{
      tip.style.display = 'none';
    }});
  }}
}}

// Draw all charts
window.addEventListener('load', function() {{
  for (const [id, data] of Object.entries(CHARTS)) {{
    const big = id.startsWith('agg_');
    drawChart(id, data, big);
  }}
}});
window.addEventListener('resize', function() {{
  for (const [id, data] of Object.entries(CHARTS)) {{
    drawChart(id, data, id.startsWith('agg_'));
  }}
}});
</script>

<footer>
Alpha-1 Backtest V4 | Fee: 0.0357% (0.07% &times; 51%) | """ + f'{START.strftime("%Y-%m-%d")} &rarr; {END.strftime("%Y-%m-%d")}' + """ | Generated: """ + datetime.now().strftime("%Y-%m-%d %H:%M") + """
</footer>
</div>
</body>
</html>"""

    return html


# ── MAIN ──────────────────────────────────────────────
if __name__ == "__main__":
    fee_pct = FEE_RATE * 100
    print(f"\n  ALPHA-1 BACKTEST V4 — {len(SYMBOLS)} COINS × {len(TIMEFRAMES)} TF")
    print(f"  {START.strftime('%Y-%m-%d')} → {END.strftime('%Y-%m-%d')}")
    print(f"  Von: ${CAPITAL:,.0f} | Size: ${SIZE:,.0f}→dynamic")
    print(f"  Fee goc: 0.07% | Giam: 49% | Fee thuc: {fee_pct:.4f}%")
    print(f"  SL: trailing {TRAIL_ATR}×ATR | TP: {TRAIL_ATR*TP_RATIO}×ATR | R:R = {TP_RATIO}:1\n")

    all_results = {}

    for tf in TIMEFRAMES:
        tf_label = tf["label"]
        print(f"\n{'=' * 80}")
        print(f"  ══ {tf_label} ══")
        print(f"{'=' * 80}")
        results = []
        for sym in SYMBOLS:
            sys.stdout.write(f"    {sym:<12}")
            sys.stdout.flush()
            r = run_coin(sym, tf["id"], tf["mins"])
            if r and r.get("trades", 0) > 0:
                print(f"  {r['trades']:>5} lenh | WR {r['wr']:>5.1f}% (no fee: {r['wr_nofee']:.1f}%) | ${r['net']:>+9,.2f} | Sharpe {r['sharpe']:.2f} | DD {r['dd_pct']:.2f}%")
                results.append(r)
            elif r:
                print(f"  0 lenh")
                results.append(r)
            else:
                print()

        all_results[tf_label] = results

    # Generate HTML
    html = generate_html(all_results)
    out_path = os.path.join(os.path.dirname(__file__), "backtest_v4_report.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  >>> HTML report: {out_path}")
    print(f"  >>> Mo bang: open '{out_path}'\n")
