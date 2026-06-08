#!/usr/bin/env python3
"""Backtest Alpha B - XS-Reversal beta-neutral from alpha-logic.md.

Signal TF: H4.
Order handling/fills/rebalance: M5 candles at the H4 boundary.

Score:
  z_s = -(ret_24h_s - cross_section_mean(ret_24h)) / std_20bar_s

Universe:
  enough lookback and 7d ADV >= $2M.

Portfolio:
  long top-k relative losers, equal weight, rebalance every H4 bar,
  rotate at most one coin when an active coin falls top-k+3 or worse,
  hedge BTC beta with 60-bar causal beta.
"""

from __future__ import annotations

import csv
import json
import math
import os
from datetime import timedelta
from pathlib import Path

from backtest_common import (
    CAPITAL,
    END,
    FEE_RATE,
    ROOT,
    START,
    compute_stats,
    ensure_local_data_available,
    fetch_candles,
    load_symbols,
    money,
    write_html_report,
)

TOP_K = int(os.getenv("TOP_K", "10"))
MIN_ADV_7D = float(os.getenv("MIN_ADV_7D", "2000000"))
LOOKBACK_BARS = int(os.getenv("LOOKBACK_BARS", "25"))
BETA_LOOKBACK = int(os.getenv("BETA_LOOKBACK", "60"))
HYSTERESIS_RANKS = int(os.getenv("HYSTERESIS_RANKS", "3"))


def std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def covariance(a: list[float], b: list[float]) -> float:
    if len(a) < 2 or len(a) != len(b):
        return 0.0
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) - 1)


def h4_index(candles: list[dict]) -> dict:
    closes = [c["close"] for c in candles]
    rets_1 = [None] * len(candles)
    rets_24 = [None] * len(candles)
    std_20 = [None] * len(candles)
    adv_7d = [None] * len(candles)
    quote = [c["close"] * max(c.get("volume", 0.0), 0.0) for c in candles]

    for i in range(1, len(candles)):
        if closes[i - 1] > 0:
            rets_1[i] = closes[i] / closes[i - 1] - 1.0
    for i in range(6, len(candles)):
        if closes[i - 6] > 0:
            rets_24[i] = closes[i] / closes[i - 6] - 1.0
    for i in range(20, len(candles)):
        window = [r for r in rets_1[i - 19 : i + 1] if r is not None]
        if len(window) >= 20:
            std_20[i] = std(window)
    for i in range(42, len(candles)):
        adv_7d[i] = sum(quote[i - 41 : i + 1]) / 7.0

    by_time = {}
    for i, c in enumerate(candles):
        by_time[c["time"]] = {
            "i": i,
            "close": closes[i],
            "ret_1": rets_1[i],
            "ret_24": rets_24[i],
            "std_20": std_20[i],
            "adv_7d": adv_7d[i],
        }
    return by_time


def m5_open_map(candles: list[dict]) -> dict:
    return {c["time"]: c["open"] for c in candles}


def rank_scores(symbols: list[str], h4_by_symbol: dict, signal_time) -> tuple[list[str], dict[str, float]]:
    rows = []
    for sym in symbols:
        row = h4_by_symbol.get(sym, {}).get(signal_time)
        if not row:
            continue
        if row["i"] < LOOKBACK_BARS:
            continue
        if row["ret_24"] is None or row["std_20"] is None or row["std_20"] <= 1e-12:
            continue
        if row["adv_7d"] is None or row["adv_7d"] < MIN_ADV_7D:
            continue
        rows.append((sym, row["ret_24"], row["std_20"]))

    if not rows:
        return [], {}

    mean_ret = sum(r[1] for r in rows) / len(rows)
    scores = {sym: -((ret_24 - mean_ret) / sigma) for sym, ret_24, sigma in rows}
    ranked = sorted(scores, key=lambda s: scores[s], reverse=True)
    return ranked, scores


def hysteresis_select(active: list[str], ranked: list[str], top_k: int) -> list[str]:
    if not active:
        return ranked[:top_k]

    rank = {sym: i for i, sym in enumerate(ranked)}
    selected = [sym for sym in active if sym in rank]
    while len(selected) < min(top_k, len(ranked)):
        for sym in ranked:
            if sym not in selected:
                selected.append(sym)
                break

    rotate_out = None
    for sym in selected:
        if rank.get(sym, 10**9) >= top_k + HYSTERESIS_RANKS:
            rotate_out = sym
            break

    if rotate_out is not None:
        for candidate in ranked:
            if candidate not in selected:
                selected = [s for s in selected if s != rotate_out]
                selected.append(candidate)
                break

    selected = [s for s in selected if s in rank]
    selected.sort(key=lambda s: rank[s])
    return selected[:top_k]


def calc_beta(active: list[str], signal_time, h4_by_symbol: dict, h4_times: list, btc_symbol: str = "BTCUSDT") -> float:
    if not active or btc_symbol not in h4_by_symbol:
        return 0.0
    try:
        idx = h4_times.index(signal_time)
    except ValueError:
        return 0.0
    if idx < BETA_LOOKBACK:
        return 0.0

    basket_returns = []
    btc_returns = []
    for t in h4_times[idx - BETA_LOOKBACK + 1 : idx + 1]:
        b = h4_by_symbol[btc_symbol].get(t)
        if not b or b["ret_1"] is None:
            continue
        vals = []
        for sym in active:
            row = h4_by_symbol.get(sym, {}).get(t)
            if row and row["ret_1"] is not None:
                vals.append(row["ret_1"])
        if vals:
            basket_returns.append(sum(vals) / len(vals))
            btc_returns.append(b["ret_1"])

    var_btc = covariance(btc_returns, btc_returns)
    if var_btc <= 1e-12:
        return 0.0
    return covariance(basket_returns, btc_returns) / var_btc


def long_turnover(prev_active: list[str], active: list[str]) -> float:
    prev_k = max(len(prev_active), 1)
    new_k = max(len(active), 1)
    symbols = set(prev_active) | set(active)
    turnover = 0.0
    for sym in symbols:
        old_w = 1.0 / prev_k if sym in prev_active else 0.0
        new_w = 1.0 / new_k if sym in active else 0.0
        turnover += abs(new_w - old_w)
    return CAPITAL * turnover


def run_portfolio() -> tuple[list[dict], list[dict]]:
    ensure_local_data_available()
    symbols = [s for s in load_symbols() if s != "BTCUSDT"]
    if "BTCUSDT" not in load_symbols():
        symbols.append("BTCUSDT")

    basket_symbols = [s for s in symbols if s != "BTCUSDT"]
    print(f"\n  ALPHA B XS-REVERSAL BACKTEST - signal H4, handle M5")
    print(f"  {START:%Y-%m-%d} -> {END:%Y-%m-%d} | basket_symbols={len(basket_symbols)} | top_k={TOP_K}")
    print(f"  fee={FEE_RATE*100:.4f}% | min_adv_7d=${MIN_ADV_7D:,.0f}\n")

    h4_raw = {}
    h4_by_symbol = {}
    m5_opens = {}
    for idx, sym in enumerate(["BTCUSDT"] + basket_symbols, 1):
        h4 = fetch_candles(sym, "4h", warmup_bars=max(BETA_LOOKBACK + 50, 120))
        m5 = fetch_candles(sym, "5m", warmup_bars=max(BETA_LOOKBACK * 48, 1200))
        if h4 and m5:
            h4_raw[sym] = h4
            h4_by_symbol[sym] = h4_index(h4)
            m5_opens[sym] = m5_open_map(m5)
            print(f"  [{idx:>3}] {sym:<14} h4={len(h4):>5} m5={len(m5):>6}")
        else:
            print(f"  [{idx:>3}] {sym:<14} skip no data")

    basket_symbols = [s for s in basket_symbols if s in h4_by_symbol and s in m5_opens]
    if "BTCUSDT" not in h4_by_symbol or not basket_symbols:
        return [], []

    h4_times = sorted(h4_by_symbol["BTCUSDT"].keys())
    top_k = min(TOP_K, len(basket_symbols))
    active: list[str] = []
    prev_active: list[str] = []
    prev_beta = 0.0
    trades = []
    detail_rows = []

    for signal_time in h4_times:
        start_fill = signal_time + timedelta(hours=4)
        end_fill = signal_time + timedelta(hours=8)
        if start_fill < START or end_fill > END:
            continue

        ranked, scores = rank_scores(basket_symbols, h4_by_symbol, signal_time)
        if not ranked:
            continue

        active = hysteresis_select(active, ranked, top_k)
        if not active:
            continue

        beta = calc_beta(active, signal_time, h4_by_symbol, h4_times)
        missing = [
            sym
            for sym in active + ["BTCUSDT"]
            if start_fill not in m5_opens.get(sym, {}) or end_fill not in m5_opens.get(sym, {})
        ]
        if missing:
            continue

        long_ret = 0.0
        for sym in active:
            sp = m5_opens[sym][start_fill]
            ep = m5_opens[sym][end_fill]
            if sp <= 0:
                continue
            long_ret += (ep / sp - 1.0) / len(active)

        btc_start = m5_opens["BTCUSDT"][start_fill]
        btc_end = m5_opens["BTCUSDT"][end_fill]
        btc_ret = btc_end / btc_start - 1.0 if btc_start > 0 else 0.0

        long_gross = CAPITAL * long_ret
        hedge_gross = -beta * CAPITAL * btc_ret
        gross = long_gross + hedge_gross
        turnover = long_turnover(prev_active, active)
        hedge_turnover = abs(beta - prev_beta) * CAPITAL
        fee = FEE_RATE * (turnover + hedge_turnover)
        net = gross - fee

        trades.append(
            {
                "side": "LONG",
                "entry": 1.0,
                "exit": 1.0 + gross / CAPITAL,
                "result": "REBALANCE",
                "net": net,
                "fee": fee,
                "gross": gross,
                "net_nofee": gross,
                "size": CAPITAL,
                "entry_time": start_fill,
                "exit_time": end_fill,
            }
        )
        detail_rows.append(
            {
                "entry_time": start_fill.strftime("%Y-%m-%d %H:%M"),
                "exit_time": end_fill.strftime("%Y-%m-%d %H:%M"),
                "symbols": ";".join(active),
                "top_scores": json.dumps({s: round(scores[s], 4) for s in active}, sort_keys=True),
                "beta": round(beta, 6),
                "long_ret": round(long_ret, 8),
                "btc_ret": round(btc_ret, 8),
                "gross": round(gross, 4),
                "fee": round(fee, 4),
                "net": round(net, 4),
                "turnover": round(turnover, 4),
                "hedge_turnover": round(hedge_turnover, 4),
            }
        )

        prev_active = list(active)
        prev_beta = beta

    return trades, detail_rows


def write_portfolio_outputs(trades: list[dict], detail_rows: list[dict]) -> None:
    if not trades:
        print("\n  No portfolio trades.")
        empty_summary = {
            "periods": 0,
            "wr": 0,
            "net": 0,
            "fee": 0,
            "gross": 0,
            "pf": 0,
            "sharpe": 0,
            "max_dd_pct": 0,
        }
        write_html_report(
            prefix="backtest_alpha_b_xs_reversal",
            title="ALPHA B XS-REVERSAL BACKTEST",
            summary_rows=[empty_summary],
            trade_rows=[],
            cards=[
                ("Periods", "0", "H4 rebalance periods"),
                ("Win Rate", "0.0%", "After fee"),
                ("Net P&L", "$+0.00", "Gross $+0.00 | Fee $+0.00"),
            ],
        )
        return

    stats = compute_stats(trades)
    assert stats is not None
    print(
        f"\n  DONE | periods={stats['trades']} | WR={stats['wr']:.1f}%"
        f" | gross=${stats['gross']:+,.2f} | fee=${stats['fee']:,.2f}"
        f" | net=${stats['net']:+,.2f} | Sharpe={stats['sharpe']:.2f}"
        f" | DD={stats['dd_pct']:.2f}%"
    )

    trade_path = ROOT / "backtest_alpha_b_xs_reversal_trades.csv"
    with trade_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(detail_rows[0].keys()))
        writer.writeheader()
        writer.writerows(detail_rows)
    print(f"  >>> trades: {trade_path}")

    summary_path = ROOT / "backtest_alpha_b_xs_reversal_summary.csv"
    summary_row = {
        "periods": stats["trades"],
        "wr": round(stats["wr"], 2),
        "net": round(stats["net"], 4),
        "fee": round(stats["fee"], 4),
        "gross": round(stats["gross"], 4),
        "pf": round(stats["pf"], 4),
        "sharpe": round(stats["sharpe"], 4),
        "max_dd_pct": round(stats["dd_pct"], 4),
    }
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        fields = ["periods", "wr", "net", "fee", "gross", "pf", "sharpe", "max_dd_pct"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerow(summary_row)
    print(f"  >>> summary: {summary_path}")

    html_path = write_html_report(
        prefix="backtest_alpha_b_xs_reversal",
        title="ALPHA B XS-REVERSAL BACKTEST",
        summary_rows=[summary_row],
        trade_rows=detail_rows,
        cards=[
            ("Periods", f"{stats['trades']:,}", "H4 rebalance periods"),
            ("Win Rate", f"{stats['wr']:.1f}%", "After fee"),
            ("Net P&L", money(stats["net"]), f"Gross {money(stats['gross'])} | Fee {money(stats['fee'])}"),
            ("Sharpe", f"{stats['sharpe']:.2f}", "Portfolio periods"),
            ("Max DD", f"{stats['dd_pct']:.2f}%", "Portfolio equity"),
        ],
        chart_data={"eq": stats["eq_curve"], "start": stats["eq_curve"][0]},
    )
    print(f"  >>> html:    {html_path}")


if __name__ == "__main__":
    portfolio_trades, portfolio_rows = run_portfolio()
    write_portfolio_outputs(portfolio_trades, portfolio_rows)
