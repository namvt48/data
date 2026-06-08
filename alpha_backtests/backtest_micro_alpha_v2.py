#!/usr/bin/env python3
"""Backtest MicroAlpha V2 from alpha-logic.md.

Signal TF: M5.
Order handling/fills/exits: M5 candles.

Entry:
  VPIN proxy < 0.45, close <= BB lower(20, 2), RSI(14) < 35,
  volume-delta z > 0.5, H1 trend up, BTC 12-bar M5 return < 0,
  close < VWAP(48).

Exit priority:
  emergency SL = entry - 3*ATR(14), VWAP reversion, 1*ATR trail, 30 M5 bars.
"""

from __future__ import annotations

import bisect
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
    calc_vwap,
    compute_stats,
    fetch_candles,
    rolling_z,
    run_symbol_backtests,
)

VPIN_MAX = float(os.getenv("VPIN_MAX", "0.45"))
VWAP_PERIOD = int(os.getenv("VWAP_PERIOD", "48"))
BB_PERIOD = int(os.getenv("BB_PERIOD", "20"))
BB_STD = float(os.getenv("BB_STD", "2.0"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_MAX = float(os.getenv("RSI_MAX", "35"))
VDELTA_MIN_Z = float(os.getenv("VDELTA_MIN_Z", "0.5"))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", "14"))
MAX_HOLD_M5 = int(os.getenv("MAX_HOLD_M5", "30"))
VPIN_PERIOD = int(os.getenv("VPIN_PERIOD", "48"))
H1_SMA = int(os.getenv("H1_SMA", "50"))


def calc_vpin_proxy(candles: list[dict], p: int) -> tuple[list[float | None], list[float | None]]:
    signed = []
    volumes = []
    for c in candles:
        vol = max(float(c.get("volume", 0.0)), 0.0)
        sign = 1.0 if c["close"] >= c["open"] else -1.0
        signed.append(sign * vol)
        volumes.append(vol)

    vdelta_z = rolling_z(signed, p)
    vpin: list[float | None] = [None] * len(candles)
    signed_sum = 0.0
    vol_sum = 0.0
    for i, value in enumerate(signed):
        signed_sum += value
        vol_sum += volumes[i]
        if i >= p:
            signed_sum -= signed[i - p]
            vol_sum -= volumes[i - p]
        if i >= p - 1 and vol_sum > 0:
            vpin[i] = abs(signed_sum) / vol_sum
    return vpin, vdelta_z


def h1_trend_lookup(symbol: str) -> tuple[list, list[bool | None]]:
    h1 = fetch_candles(symbol, "1h", warmup_bars=H1_SMA + 10)
    cl = [c["close"] for c in h1]
    sma = calc_sma(cl, H1_SMA)
    times = [c["time"] + timedelta(hours=1) for c in h1]
    trend: list[bool | None] = [None] * len(h1)
    for i in range(1, len(h1)):
        if sma[i] is not None and sma[i - 1] is not None:
            trend[i] = cl[i] > sma[i] and sma[i] >= sma[i - 1]
    return times, trend


def value_at_or_before(times: list, values: list, at_time):
    j = bisect.bisect_right(times, at_time) - 1
    if j < 0:
        return None
    return values[j]


def btc_seesaw_lookup() -> dict:
    btc = fetch_candles("BTCUSDT", "5m", warmup_bars=60)
    ret_by_time = {}
    cl = [c["close"] for c in btc]
    for i in range(12, len(btc)):
        if cl[i - 12] > 0:
            ret_by_time[btc[i]["time"]] = cl[i] / cl[i - 12] - 1.0
    return ret_by_time


BTC_RET_BY_TIME: dict | None = None


def run_backtest(symbol: str, m5: list[dict]) -> tuple[list[dict], int]:
    global BTC_RET_BY_TIME
    if BTC_RET_BY_TIME is None:
        BTC_RET_BY_TIME = btc_seesaw_lookup()

    n = len(m5)
    cl = [c["close"] for c in m5]
    hi = [c["high"] for c in m5]
    lo = [c["low"] for c in m5]

    _, _, bb_lower = calc_bbands(cl, BB_PERIOD, BB_STD)
    rsi = calc_rsi(cl, RSI_PERIOD)
    atr = calc_atr(hi, lo, cl, ATR_PERIOD)
    vwap = calc_vwap(m5, VWAP_PERIOD)
    vpin, vdelta_z = calc_vpin_proxy(m5, VPIN_PERIOD)
    h1_times, h1_trend = h1_trend_lookup(symbol)

    time_to_idx = {c["time"]: i for i, c in enumerate(m5)}
    entry_signal_by_idx: dict[int, int] = {}
    filtered = 0

    for i in range(n):
        needed = (bb_lower[i], rsi[i], atr[i], vwap[i], vpin[i], vdelta_z[i])
        if any(v is None for v in needed):
            continue
        signal_close = m5[i]["time"] + timedelta(minutes=5)
        h1_up = value_at_or_before(h1_times, h1_trend, signal_close)
        btc_ret = BTC_RET_BY_TIME.get(m5[i]["time"])
        is_signal = (
            vpin[i] < VPIN_MAX
            and cl[i] <= bb_lower[i]
            and rsi[i] < RSI_MAX
            and vdelta_z[i] > VDELTA_MIN_Z
            and h1_up is True
            and btc_ret is not None
            and btc_ret < 0
            and cl[i] < vwap[i]
        )
        if is_signal:
            entry_idx = time_to_idx.get(signal_close)
            if entry_idx is not None:
                entry_signal_by_idx[entry_idx] = i
        elif START <= m5[i]["time"] < END:
            filtered += 1

    trades = []
    in_trade = False
    ep = 0.0
    et = None
    entry_idx = -1
    signal_idx = -1
    trade_size = SIZE
    cur_size = SIZE
    cur_eq = CAPITAL
    trail_armed = False
    high_since_entry = 0.0

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
            entry_atr = atr[signal_idx] or atr[i] or 0.0
            sl = ep - 3.0 * entry_atr
            high_since_entry = max(high_since_entry, candle["high"])

            if candle["low"] <= sl:
                close_trade(sl, "EMERGENCY_SL", candle["time"])
            elif vwap[i] is not None and candle["high"] >= vwap[i]:
                close_trade(vwap[i], "VWAP_REVERT", candle["time"])
            else:
                if entry_atr > 0 and candle["high"] > ep + entry_atr:
                    trail_armed = True
                if trail_armed:
                    trail_stop = high_since_entry - entry_atr
                    if candle["low"] <= trail_stop:
                        close_trade(trail_stop, "TRAIL", candle["time"])
                if in_trade and i - entry_idx >= MAX_HOLD_M5:
                    close_trade(candle["close"], "TIMEOUT", candle["time"])

        if in_trade or candle["time"] >= END:
            continue

        if i in entry_signal_by_idx:
            signal_idx = entry_signal_by_idx[i]
            if atr[signal_idx] is None:
                continue
            trade_size = cur_size
            ep = candle["open"]
            et = candle["time"]
            entry_idx = i
            high_since_entry = candle["high"]
            trail_armed = False
            in_trade = True

    if in_trade:
        close_trade(m5[-1]["close"], "OPEN", m5[-1]["time"])

    trades = [t for t in trades if START <= t["entry_time"] < END]
    return trades, filtered


def run_coin(symbol: str, min_bars: int) -> dict:
    t0 = __import__("time").time()
    m5 = fetch_candles(symbol, "5m", warmup_bars=min_bars)
    label = symbol.replace("USDT", "")
    if len(m5) < min_bars:
        return {"label": label, "trades": 0}

    trades, filtered = run_backtest(symbol, m5)
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
            "timeframe": "M5",
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
        "MICROALPHA V2 BACKTEST - signal M5, handle M5",
        run_coin,
        min_bars=420,
        out_prefix="backtest_micro_alpha_v2",
    )
