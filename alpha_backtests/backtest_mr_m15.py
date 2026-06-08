#!/usr/bin/env python3
"""Backtest MR M15 from alpha-logic.md.

Signal TF: M15.
Order handling/fills/exits: M5 candles.

Entry:
  SMA(50) slope >= 0, close < BB lower(20, 2), RSI(14) < 35.

Exit:
  TP = M15 BB middle at signal, SL = M15 BB lower - 0.5*ATR(14),
  timeout = 100 M15 bars = 300 M5 bars.
"""

from __future__ import annotations

import os
from datetime import timedelta

from backtest_common import (
    CAPITAL,
    END,
    FEE_RATE,
    MIN_SIZE,
    SIZE,
    START,
    calc_atr,
    calc_bbands,
    calc_pnl,
    calc_rsi,
    calc_sma,
    compute_stats,
    fetch_candles,
    run_symbol_backtests,
)

BB_PERIOD = int(os.getenv("BB_PERIOD", "20"))
BB_STD = float(os.getenv("BB_STD", "2.0"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_THRESHOLD = float(os.getenv("RSI_THRESHOLD", "35"))
SMA_FILTER = int(os.getenv("SMA_FILTER", "50"))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", "14"))
MAX_HOLD_M15 = int(os.getenv("MAX_HOLD_M15", "100"))
MAX_HOLD_M5 = MAX_HOLD_M15 * 3


def build_m15_entries(m15: list[dict], m5: list[dict]) -> tuple[dict[int, dict], int]:
    cl = [c["close"] for c in m15]
    hi = [c["high"] for c in m15]
    lo = [c["low"] for c in m15]
    _, bb_mid, bb_lower = calc_bbands(cl, BB_PERIOD, BB_STD)
    rsi = calc_rsi(cl, RSI_PERIOD)
    sma = calc_sma(cl, SMA_FILTER)
    atr = calc_atr(hi, lo, cl, ATR_PERIOD)

    time_to_m5 = {c["time"]: i for i, c in enumerate(m5)}
    entries: dict[int, dict] = {}
    filtered = 0

    for i in range(1, len(m15)):
        if any(v is None for v in (bb_mid[i], bb_lower[i], rsi[i], sma[i], sma[i - 1], atr[i])):
            continue
        slope = sma[i] - sma[i - 1]
        is_signal = slope >= 0 and cl[i] < bb_lower[i] and rsi[i] < RSI_THRESHOLD
        signal_close = m15[i]["time"] + timedelta(minutes=15)
        if is_signal:
            entry_idx = time_to_m5.get(signal_close)
            if entry_idx is not None:
                entries[entry_idx] = {
                    "signal_idx": i,
                    "tp": bb_mid[i],
                    "sl": bb_lower[i] - 0.5 * atr[i],
                }
        elif START <= m15[i]["time"] < END:
            filtered += 1

    return entries, filtered


def run_backtest(m15: list[dict], m5: list[dict]) -> tuple[list[dict], int]:
    entries, filtered = build_m15_entries(m15, m5)
    trades = []

    in_trade = False
    ep = 0.0
    et = None
    entry_idx = -1
    tp = 0.0
    sl = 0.0
    trade_size = SIZE
    cur_size = SIZE
    cur_eq = CAPITAL

    def close_trade(exit_p: float, reason: str, exit_time):
        nonlocal in_trade, cur_size, cur_eq
        net, fee, gross = calc_pnl("LONG", ep, exit_p, trade_size, FEE_RATE)
        trades.append(
            {
                "side": "LONG",
                "entry": ep,
                "exit": exit_p,
                "result": reason,
                "net": net,
                "fee": fee,
                "gross": gross,
                "net_nofee": gross,
                "size": trade_size,
                "entry_time": et,
                "exit_time": exit_time,
            }
        )
        in_trade = False
        cur_eq += net
        cur_size += 0.30 * net
        cur_size = max(MIN_SIZE, min(cur_size, 0.30 * cur_eq))

    for i, candle in enumerate(m5):
        if candle["time"] < START:
            continue

        if in_trade:
            if candle["low"] <= sl:
                close_trade(sl, "SL", candle["time"])
            elif candle["high"] >= tp:
                close_trade(tp, "TP", candle["time"])
            elif i - entry_idx >= MAX_HOLD_M5:
                close_trade(candle["close"], "TIMEOUT", candle["time"])

        if in_trade or candle["time"] >= END:
            continue

        entry = entries.get(i)
        if entry:
            trade_size = cur_size
            ep = candle["open"]
            et = candle["time"]
            entry_idx = i
            tp = float(entry["tp"])
            sl = float(entry["sl"])
            in_trade = True

    if in_trade:
        close_trade(m5[-1]["close"], "OPEN", m5[-1]["time"])

    trades = [t for t in trades if START <= t["entry_time"] < END]
    return trades, filtered


def run_coin(symbol: str, min_bars: int) -> dict:
    t0 = __import__("time").time()
    label = symbol.replace("USDT", "")
    m5 = fetch_candles(symbol, "5m", warmup_bars=min_bars * 3)
    m15 = fetch_candles(symbol, "15m", warmup_bars=min_bars)
    if len(m15) < min_bars or len(m5) < min_bars * 3:
        return {"label": label, "trades": 0}

    trades, filtered = run_backtest(m15, m5)
    if not trades:
        return {"label": label, "trades": 0, "filtered": filtered}

    stats = compute_stats(trades)
    assert stats is not None
    stats["label"] = label
    stats["filtered"] = filtered
    stats["secs"] = __import__("time").time() - t0
    stats["trade_rows"] = [
        {
            "symbol": label,
            "timeframe": "M15->M5",
            "side": t["side"],
            "entry_time": t["entry_time"].strftime("%Y-%m-%d %H:%M"),
            "exit_time": t["exit_time"].strftime("%Y-%m-%d %H:%M"),
            "entry": round(t["entry"], 8),
            "exit": round(t["exit"], 8),
            "size": round(t["size"], 2),
            "gross": round(t["gross"], 4),
            "fee": round(t["fee"], 4),
            "net": round(t["net"], 4),
            "result": t["result"],
        }
        for t in trades
    ]
    return stats


if __name__ == "__main__":
    run_symbol_backtests(
        "MR M15 BACKTEST - signal M15, handle M5",
        run_coin,
        min_bars=160,
        out_prefix="backtest_mr_m15",
    )
