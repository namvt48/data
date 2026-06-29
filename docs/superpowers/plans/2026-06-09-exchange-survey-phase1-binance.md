# Exchange Survey — Phase 1 (Binance Vertical Slice) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working end-to-end survey pipeline for ONE exchange (Binance USDT-M perp) — collect order books + latency + funding/fees over a few hours, store to Parquet, and produce a Markdown report.

**Architecture:** Hand-rolled async clients (no CCXT). A per-exchange `ExchangeAdapter` returns normalized records; a `Collector` engine runs WS book tasks + REST probe tasks + a periodic sampler; samples land in Parquet; an analysis layer reads Parquet and renders a Markdown report. Binance is the first vertical slice that locks the framework so Phase 2 adapters just plug in.

**Tech Stack:** Python 3.12, `asyncio`, `aiohttp` (REST), `websockets` (WS), `pandas` + `pyarrow` (Parquet), `pytest`.

**Spec:** `docs/superpowers/specs/2026-06-09-exchange-survey-design.md`

---

## Progress (updated 2026-06-09)

- [x] **Task 1** — scaffold + models (`commit efd4d96`, +`.gitignore` cleanup)
- [x] **Task 2** — OrderBook mid/spread/depth (`e4c4a11`)
- [x] **Task 3** — impact-cost slippage proxy (`1746d4f`)
- [x] **Task 4** — adapter base Protocol + Binance parsers + fixtures (`c91787b`)
- [x] **Task 5** — Binance REST/WS network methods + `network` marker (`2fd9d45`); live ping verified
- [x] **Task 6** — Parquet storage (`baccb0d`)
- [x] **Task 7** — Config + async Collector (`2d0f802`)
- [x] **Task 8** — analysis summaries + rankings (`a62ecda`)
- [x] **Task 9** — Markdown report + slippage caveat (`905231b`)
- [x] **Task 10** — adapter registry + multi-exchange CLI (`40c0d6f`); 30s live run produced real Binance report
- [x] **Bugfix** — Binance WS `@depth20` uses `b`/`a` keys, not `bids`/`asks` (`28d8f7e`)

**Phase 1 framework is complete and proven end-to-end live.** 19 offline tests green.

### Scope change after Phase 1 (approved by user)
User wants all 6 exchanges. Two design decisions taken mid-flight:
1. **Uniform deep REST orderbook snapshots** (polled at sample cadence) for ALL exchanges, replacing per-exchange WS. Reason: cross-exchange impact-cost ranking must use one methodology + one depth; the 20-level WS stream can't fill the `$1M` ladder (was NaN). WS freshness stays deferred.
2. Each adapter returns book sizes already in **coin units**, applying its own contract multiplier (OKX `ctVal`, KuCoin `multiplier`) — verified by curl sweep.

### Phase 2 progress (completed 2026-06-09)
- [x] **Task R** — refactor base/binance/collector to `fetch_orderbook` (deep REST)
- [x] **Task 11** — Bybit adapter (`BTCUSDT`, coins, 8h funding)
- [x] **Task 12** — OKX adapter (`BTC-USDT-SWAP`, contracts×`ctVal`, 8h)
- [x] **Task 13** — KuCoin adapter (`XBTUSDTM`, contracts×`multiplier`, 8h, fees from API)
- [x] **Task 14** — BingX adapter (`BTC-USDT`, coins, 8h)
- [x] **Task 15** — Hyperliquid adapter (`BTC`, coins, hourly funding ×8, POST `/info`)
- [x] **Task 16** — live run all 6 + final whole-implementation review

**Verification:** 36 offline tests green. Two 15-second live runs completed without
events/errors: first with BTC, then with BTC + ETH across all 6 exchanges. Both runs
produced Parquet datasets and Markdown reports.

> The Task 1–10 step checkboxes below are left as-authored for reference; the Progress list above is the source of truth for status.

---

## File Structure

```
exchange-survey/
  requirements.txt
  survey/
    __init__.py
    models.py          # dataclasses: ContractSpec, BookUpdate, LatencySample, FundingRecord, FeeRecord, OrderbookSample
    config.py          # SurveyConfig: defaults (ladder, intervals, depth bands, symbols)
    orderbook.py       # OrderBook: maintain top-of-book, spread, depth, impact cost
    adapters/
      __init__.py
      base.py          # ExchangeAdapter Protocol + shared parse helpers
      binance.py       # BinanceAdapter: normalize, contract spec, parse REST + WS payloads
    collector.py       # Collector: orchestrate WS/REST/sampler tasks, buffer rows
    storage.py         # ParquetStore: write the 4 parquet files + run_meta.json
    analysis.py        # load parquet -> summary tables + rankings
    report.py          # render Markdown report from analysis result
  tests/
    fixtures/          # recorded real Binance payloads (committed)
    test_models.py
    test_orderbook.py
    test_binance_adapter.py
    test_storage.py
    test_collector.py
    test_analysis.py
    test_report.py
  main.py              # CLI entry point
```

All paths below are relative to `exchange-survey/` unless stated otherwise. The project lives at
`/home/namvt/Desktop/quant-space/alphas/exchange-survey/`.

---

## Task 1: Project scaffold + data models

**Files:**
- Create: `exchange-survey/requirements.txt`
- Create: `exchange-survey/survey/__init__.py` (empty)
- Create: `exchange-survey/survey/adapters/__init__.py` (empty)
- Create: `exchange-survey/survey/models.py`
- Test: `exchange-survey/tests/test_models.py`

- [ ] **Step 1: Init project + git**

```bash
mkdir -p /home/namvt/Desktop/quant-space/alphas/exchange-survey/survey/adapters
mkdir -p /home/namvt/Desktop/quant-space/alphas/exchange-survey/tests/fixtures
cd /home/namvt/Desktop/quant-space/alphas/exchange-survey
git init
touch survey/__init__.py survey/adapters/__init__.py
printf "aiohttp\nwebsockets\npandas\npyarrow\npytest\n" > requirements.txt
```

- [ ] **Step 2: Write the failing test**

`tests/test_models.py`:

```python
from survey.models import (
    ContractSpec, BookLevel, BookUpdate, LatencySample,
    FundingRecord, FeeRecord, OrderbookSample,
)


def test_book_update_holds_normalized_levels():
    bu = BookUpdate(
        exchange="binance", symbol="BTC-PERP", ts_event_ms=1000,
        bids=[BookLevel(100.0, 2.0)], asks=[BookLevel(101.0, 1.0)],
        is_snapshot=True,
    )
    assert bu.bids[0].price == 100.0
    assert bu.asks[0].size == 1.0


def test_contract_spec_notional_of_coin_units():
    # Binance USDT-M: quantity already in coin units, multiplier 1
    spec = ContractSpec(symbol="BTCUSDT", multiplier=1.0, contract_unit="coin", tick_size=0.1)
    assert spec.coin_qty(3.0) == 3.0


def test_latency_sample_computes_skew():
    s = LatencySample(exchange="binance", ts_ms=2000, rtt_ms=12.0,
                       server_time_ms=2100, local_time_ms=2000)
    assert s.clock_skew_ms == 100
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd exchange-survey && python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'survey.models'`

- [ ] **Step 4: Write the models**

`survey/models.py`:

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ContractSpec:
    symbol: str                       # native symbol, e.g. "BTCUSDT"
    multiplier: float                 # native qty -> coin units factor
    contract_unit: Literal["coin", "usd"]
    tick_size: float

    def coin_qty(self, native_qty: float) -> float:
        """Convert a native quantity to coin units."""
        return native_qty * self.multiplier


@dataclass(frozen=True)
class BookLevel:
    price: float                      # quote (USDT)
    size: float                       # coin units


@dataclass
class BookUpdate:
    exchange: str
    symbol: str                       # canonical, e.g. "BTC-PERP"
    ts_event_ms: int                  # exchange-stamped event time
    bids: list[BookLevel]
    asks: list[BookLevel]
    is_snapshot: bool


@dataclass(frozen=True)
class LatencySample:
    exchange: str
    ts_ms: int
    rtt_ms: float
    server_time_ms: int
    local_time_ms: int

    @property
    def clock_skew_ms(self) -> int:
        return self.server_time_ms - self.local_time_ms


@dataclass(frozen=True)
class FundingRecord:
    exchange: str
    symbol: str
    ts_ms: int
    funding_rate_8h: float            # normalized to a per-8h rate


@dataclass(frozen=True)
class FeeRecord:
    exchange: str
    symbol: str
    ts_ms: int
    maker: float
    taker: float


@dataclass(frozen=True)
class OrderbookSample:
    ts_ms: int
    exchange: str
    symbol: str
    vantage: str
    mid: float
    spread_bps: float
    depth_bid_0p1pct: float           # notional USD within 0.1% of mid on bid side
    depth_ask_0p1pct: float
    depth_bid_0p5pct: float
    depth_ask_0p5pct: float
    impact: dict[str, float] = field(default_factory=dict)  # e.g. {"1k_buy": 1.2, "1k_sell": 1.1, ...}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd exchange-survey && python -m pytest tests/test_models.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
cd exchange-survey
git add -A
git commit -m "feat: project scaffold + normalized data models"
```

---

## Task 2: OrderBook — spread + depth

**Files:**
- Create: `exchange-survey/survey/orderbook.py`
- Test: `exchange-survey/tests/test_orderbook.py`

- [ ] **Step 1: Write the failing test**

`tests/test_orderbook.py`:

```python
from survey.models import BookUpdate, BookLevel
from survey.orderbook import OrderBook


def make_book():
    ob = OrderBook()
    ob.apply(BookUpdate(
        exchange="binance", symbol="BTC-PERP", ts_event_ms=1,
        bids=[BookLevel(100.0, 5.0), BookLevel(99.9, 10.0)],
        asks=[BookLevel(100.1, 5.0), BookLevel(100.2, 10.0)],
        is_snapshot=True,
    ))
    return ob


def test_mid_and_spread_bps():
    ob = make_book()
    assert ob.mid() == 100.05
    # spread = 0.1 / 100.05 * 1e4
    assert round(ob.spread_bps(), 2) == round(0.1 / 100.05 * 1e4, 2)


def test_depth_within_band_returns_notional_usd():
    ob = make_book()
    # within 0.5% of mid (100.05 +- 0.50025): ask 100.1 (size 5) and 100.2 (size 10) both inside upper band
    # notional = price*size summed
    depth = ob.depth_notional("ask", 0.005)
    assert round(depth, 1) == round(100.1 * 5 + 100.2 * 10, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd exchange-survey && python -m pytest tests/test_orderbook.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'survey.orderbook'`

- [ ] **Step 3: Write the OrderBook (spread + depth only)**

`survey/orderbook.py`:

```python
from __future__ import annotations
from survey.models import BookUpdate, BookLevel


class OrderBook:
    """Maintains a normalized top-of-book snapshot for one symbol.

    Phase 1 feeds full snapshots (Binance partial-depth stream), so apply()
    just replaces the levels. Delta merging is intentionally deferred.
    """

    def __init__(self) -> None:
        self.bids: list[BookLevel] = []      # sorted high -> low
        self.asks: list[BookLevel] = []      # sorted low -> high
        self.ts_event_ms: int = 0

    def apply(self, update: BookUpdate) -> None:
        if update.is_snapshot:
            self.bids = sorted(update.bids, key=lambda l: l.price, reverse=True)
            self.asks = sorted(update.asks, key=lambda l: l.price)
            self.ts_event_ms = update.ts_event_ms

    def best_bid(self) -> float:
        return self.bids[0].price

    def best_ask(self) -> float:
        return self.asks[0].price

    def mid(self) -> float:
        return (self.best_bid() + self.best_ask()) / 2

    def spread_bps(self) -> float:
        mid = self.mid()
        return (self.best_ask() - self.best_bid()) / mid * 1e4

    def depth_notional(self, side: str, band_pct: float) -> float:
        """Sum notional USD (price*size) on `side` within band_pct of mid."""
        mid = self.mid()
        if side == "bid":
            floor = mid * (1 - band_pct)
            return sum(l.price * l.size for l in self.bids if l.price >= floor)
        ceil = mid * (1 + band_pct)
        return sum(l.price * l.size for l in self.asks if l.price <= ceil)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd exchange-survey && python -m pytest tests/test_orderbook.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd exchange-survey && git add -A && git commit -m "feat: OrderBook with mid/spread/depth"
```

---

## Task 3: Impact cost (slippage proxy)

**Files:**
- Modify: `exchange-survey/survey/orderbook.py`
- Test: `exchange-survey/tests/test_orderbook.py` (add cases)

- [ ] **Step 1: Write the failing test (append to `tests/test_orderbook.py`)**

```python
def test_impact_cost_buy_walks_asks():
    ob = make_book()
    # buy $1000 notional. mid=100.05. Best ask 100.1 size 5 -> notional 500.5 not enough,
    # need 1000: take all of 100.1 (500.5) then part of 100.2.
    # remaining 499.5 / 100.2 = 4.985 coins at 100.2
    # filled coins: 5 + 4.985..., vwap = 1000 / coins
    bps = ob.impact_cost_bps(1000.0, "buy")
    coins = 5 + (1000 - 100.1 * 5) / 100.2
    vwap = 1000 / coins
    expected = (vwap - ob.mid()) / ob.mid() * 1e4
    assert round(bps, 4) == round(expected, 4)


def test_impact_cost_returns_nan_when_book_too_thin():
    ob = make_book()
    # ask side total notional ~ 100.1*5 + 100.2*10 = 1502.5; ask for 1e9 -> not fillable
    import math
    assert math.isnan(ob.impact_cost_bps(1e9, "buy"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd exchange-survey && python -m pytest tests/test_orderbook.py -k impact -v`
Expected: FAIL with `AttributeError: 'OrderBook' object has no attribute 'impact_cost_bps'`

- [ ] **Step 3: Add `impact_cost_bps` to `survey/orderbook.py`**

Add this method to the `OrderBook` class (and `import math` at top of file):

```python
    def impact_cost_bps(self, notional_usd: float, side: str) -> float:
        """Theoretical impact cost in bps vs mid for taking `notional_usd`.

        Static L2 book, sole taker, no fees, no latency. Returns NaN if the
        visible book cannot absorb the size.
        """
        levels = self.asks if side == "buy" else self.bids
        mid = self.mid()
        remaining = notional_usd
        coins = 0.0
        for lvl in levels:
            level_notional = lvl.price * lvl.size
            take = min(remaining, level_notional)
            coins += take / lvl.price
            remaining -= take
            if remaining <= 1e-9:
                break
        if remaining > 1e-9:
            return math.nan
        vwap = notional_usd / coins
        if side == "buy":
            return (vwap - mid) / mid * 1e4
        return (mid - vwap) / mid * 1e4
```

Also add `import math` to the top of `survey/orderbook.py` (after `from __future__`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd exchange-survey && python -m pytest tests/test_orderbook.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
cd exchange-survey && git add -A && git commit -m "feat: impact-cost slippage proxy on OrderBook"
```

---

## Task 4: Adapter base + Binance parsers (REST + WS, pure functions)

**Files:**
- Create: `exchange-survey/survey/adapters/base.py`
- Create: `exchange-survey/survey/adapters/binance.py`
- Create fixtures: `exchange-survey/tests/fixtures/binance_time.json`, `binance_premium_index.json`, `binance_depth20.json`
- Test: `exchange-survey/tests/test_binance_adapter.py`

> **Why pure functions:** the network-touching methods (Task 5) are thin wrappers over
> these parsers. Testing the parsers against recorded payloads gives full coverage of the
> risky normalization logic with zero network.

- [ ] **Step 1: Record real payloads into fixtures**

Run these to capture real shapes (committed as fixtures). If offline, hand-create the files with the literal JSON shown below.

```bash
cd exchange-survey
curl -s "https://fapi.binance.com/fapi/v1/time" > tests/fixtures/binance_time.json
curl -s "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT" > tests/fixtures/binance_premium_index.json
curl -s "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=20" > tests/fixtures/binance_depth20.json
```

Expected literal shapes (use these if you hand-create the files):
- `binance_time.json`: `{"serverTime": 1717900000000}`
- `binance_premium_index.json`: `{"symbol":"BTCUSDT","markPrice":"60000.0","lastFundingRate":"0.00010000","nextFundingTime":1717920000000}`
- `binance_depth20.json`: `{"lastUpdateId":1,"E":1717900000123,"bids":[["60000.0","2.5"],["59999.0","3.0"]],"asks":[["60001.0","1.5"],["60002.0","4.0"]]}`

> Note: the REST `/depth` payload is only a fixture for parser tests. At runtime the WS
> stream `<symbol>@depth20@100ms` delivers the same `bids`/`asks` shape with field `E`.

- [ ] **Step 2: Write the failing test**

`tests/test_binance_adapter.py`:

```python
import json
import math
from pathlib import Path
from survey.adapters.binance import BinanceAdapter

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_normalize_symbol():
    a = BinanceAdapter()
    assert a.normalize_symbol("BTC-PERP") == "BTCUSDT"
    assert a.normalize_symbol("ETH-PERP") == "ETHUSDT"


def test_contract_spec_is_coin_unit():
    a = BinanceAdapter()
    spec = a.contract_spec("BTC-PERP")
    assert spec.symbol == "BTCUSDT"
    assert spec.contract_unit == "coin"
    assert spec.multiplier == 1.0


def test_parse_server_time():
    a = BinanceAdapter()
    assert a.parse_server_time(load("binance_time.json")) == 1717900000000


def test_parse_funding_normalized_to_8h():
    a = BinanceAdapter()
    rec = a.parse_funding("BTC-PERP", load("binance_premium_index.json"), ts_ms=5)
    # Binance funding interval is already 8h -> passthrough
    assert rec.funding_rate_8h == 0.0001
    assert rec.symbol == "BTC-PERP"


def test_parse_book_message_to_snapshot():
    a = BinanceAdapter()
    bu = a.parse_book_message("BTC-PERP", load("binance_depth20.json"))
    assert bu.is_snapshot is True
    assert bu.ts_event_ms == 1717900000123
    assert bu.bids[0].price == 60000.0
    assert bu.asks[0].size == 1.5
    assert bu.symbol == "BTC-PERP"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd exchange-survey && python -m pytest tests/test_binance_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'survey.adapters.binance'`

- [ ] **Step 4: Write the base Protocol**

`survey/adapters/base.py`:

```python
from __future__ import annotations
from typing import Protocol, AsyncIterator
from survey.models import (
    ContractSpec, BookUpdate, LatencySample, FundingRecord, FeeRecord,
)


class ExchangeAdapter(Protocol):
    name: str

    def normalize_symbol(self, canonical: str) -> str: ...
    def contract_spec(self, canonical: str) -> ContractSpec: ...

    async def stream_orderbook(self, canonical_symbols: list[str]) -> AsyncIterator[BookUpdate]: ...
    async def ping(self) -> LatencySample: ...
    async def fetch_funding(self, canonical: str) -> FundingRecord: ...
    async def fetch_fees(self, canonical: str) -> FeeRecord: ...
    async def server_time(self) -> int: ...
```

- [ ] **Step 5: Write Binance parsers (pure, no network yet)**

`survey/adapters/binance.py`:

```python
from __future__ import annotations
from survey.models import (
    ContractSpec, BookLevel, BookUpdate, FundingRecord, FeeRecord,
)

# Binance USDT-M VIP0 default fees (no public per-symbol endpoint). Documented assumption.
_DEFAULT_MAKER = 0.0002
_DEFAULT_TAKER = 0.0005
# Binance USDT-M funding interval is 8h, so lastFundingRate is already per-8h.
_FUNDING_INTERVAL_HOURS = 8


class BinanceAdapter:
    name = "binance"
    REST_BASE = "https://fapi.binance.com"
    WS_BASE = "wss://fstream.binance.com/stream"

    def normalize_symbol(self, canonical: str) -> str:
        # "BTC-PERP" -> "BTCUSDT"
        base = canonical.replace("-PERP", "")
        return f"{base}USDT"

    def contract_spec(self, canonical: str) -> ContractSpec:
        return ContractSpec(
            symbol=self.normalize_symbol(canonical),
            multiplier=1.0,
            contract_unit="coin",
            tick_size=0.1,
        )

    # --- pure parsers ---
    def parse_server_time(self, payload: dict) -> int:
        return int(payload["serverTime"])

    def parse_funding(self, canonical: str, payload: dict, ts_ms: int) -> FundingRecord:
        rate = float(payload["lastFundingRate"])
        rate_8h = rate * (8 / _FUNDING_INTERVAL_HOURS)  # passthrough for Binance
        return FundingRecord(self.name, canonical, ts_ms, rate_8h)

    def fees(self, canonical: str, ts_ms: int) -> FeeRecord:
        return FeeRecord(self.name, canonical, ts_ms, _DEFAULT_MAKER, _DEFAULT_TAKER)

    def parse_book_message(self, canonical: str, payload: dict) -> BookUpdate:
        bids = [BookLevel(float(p), float(s)) for p, s in payload["bids"]]
        asks = [BookLevel(float(p), float(s)) for p, s in payload["asks"]]
        return BookUpdate(
            exchange=self.name, symbol=canonical,
            ts_event_ms=int(payload["E"]),
            bids=bids, asks=asks, is_snapshot=True,
        )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd exchange-survey && python -m pytest tests/test_binance_adapter.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: Commit**

```bash
cd exchange-survey && git add -A && git commit -m "feat: adapter base Protocol + Binance parsers with fixtures"
```

---

## Task 5: Binance network methods (REST + WS)

**Files:**
- Modify: `exchange-survey/survey/adapters/binance.py`
- Test: `exchange-survey/tests/test_binance_adapter.py` (add an integration-marked test)

> These methods are thin wrappers over Task 4 parsers. They are integration-tested behind
> a `network` marker so the default suite stays offline.

- [ ] **Step 1: Add network methods to `survey/adapters/binance.py`**

Add at top of file: `import time`, `import json`, `import aiohttp`, `import websockets`, `from typing import AsyncIterator`, and `from survey.models import LatencySample`. Then add these methods to `BinanceAdapter`:

```python
    async def server_time(self) -> int:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self.REST_BASE}/fapi/v1/time") as r:
                return self.parse_server_time(await r.json())

    async def ping(self) -> LatencySample:
        async with aiohttp.ClientSession() as s:
            t0 = time.time()
            async with s.get(f"{self.REST_BASE}/fapi/v1/time") as r:
                payload = await r.json()
            t1 = time.time()
        local_ms = int((t0 + t1) / 2 * 1000)
        return LatencySample(
            exchange=self.name, ts_ms=int(t1 * 1000),
            rtt_ms=(t1 - t0) * 1000,
            server_time_ms=self.parse_server_time(payload),
            local_time_ms=local_ms,
        )

    async def fetch_funding(self, canonical: str) -> FundingRecord:
        sym = self.normalize_symbol(canonical)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self.REST_BASE}/fapi/v1/premiumIndex",
                             params={"symbol": sym}) as r:
                payload = await r.json()
        return self.parse_funding(canonical, payload, ts_ms=int(time.time() * 1000))

    async def fetch_fees(self, canonical: str) -> FeeRecord:
        return self.fees(canonical, ts_ms=int(time.time() * 1000))

    async def stream_orderbook(self, canonical_symbols: list[str]) -> "AsyncIterator[BookUpdate]":
        streams = "/".join(f"{self.normalize_symbol(c).lower()}@depth20@100ms"
                           for c in canonical_symbols)
        by_native = {self.normalize_symbol(c): c for c in canonical_symbols}
        url = f"{self.WS_BASE}?streams={streams}"
        async with websockets.connect(url, ping_interval=20) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                stream = msg["stream"]            # e.g. "btcusdt@depth20@100ms"
                native = stream.split("@")[0].upper()
                yield self.parse_book_message(by_native[native], msg["data"])
```

- [ ] **Step 2: Add an opt-in network test (append to `tests/test_binance_adapter.py`)**

```python
import asyncio
import pytest


@pytest.mark.network
def test_ping_hits_binance():
    a = BinanceAdapter()
    sample = asyncio.run(a.ping())
    assert sample.rtt_ms > 0
    assert sample.server_time_ms > 0
```

Register the marker — create `exchange-survey/pytest.ini`:

```ini
[pytest]
markers =
    network: hits live exchange endpoints (deselect with -m "not network")
```

- [ ] **Step 3: Run offline suite (network test deselected)**

Run: `cd exchange-survey && python -m pytest -m "not network" -v`
Expected: PASS, the `test_ping_hits_binance` shows as deselected.

- [ ] **Step 4: Optionally verify live (requires internet)**

Run: `cd exchange-survey && python -m pytest -m network -v`
Expected: PASS if online; acceptable to skip if offline.

- [ ] **Step 5: Commit**

```bash
cd exchange-survey && git add -A && git commit -m "feat: Binance REST/WS network methods + network test marker"
```

---

## Task 6: Storage — Parquet writers

**Files:**
- Create: `exchange-survey/survey/storage.py`
- Test: `exchange-survey/tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

`tests/test_storage.py`:

```python
import json
import pandas as pd
from survey.storage import ParquetStore
from survey.models import LatencySample, OrderbookSample


def test_store_writes_latency_and_meta(tmp_path):
    store = ParquetStore(base_dir=tmp_path, run_id="run1", vantage="vn-home")
    store.add_latency(LatencySample("binance", 1000, 12.0, 1100, 1000))
    store.add_sample(OrderbookSample(
        ts_ms=1000, exchange="binance", symbol="BTC-PERP", vantage="vn-home",
        mid=100.0, spread_bps=1.0,
        depth_bid_0p1pct=10.0, depth_ask_0p1pct=11.0,
        depth_bid_0p5pct=20.0, depth_ask_0p5pct=21.0,
        impact={"1k_buy": 1.2, "1k_sell": 1.1},
    ))
    store.flush()
    store.write_meta({"symbols": ["BTC-PERP"]})

    rundir = tmp_path / "run1"
    lat = pd.read_parquet(rundir / "latency.parquet")
    assert lat.iloc[0]["clock_skew_ms"] == 100
    samples = pd.read_parquet(rundir / "orderbook_samples.parquet")
    assert samples.iloc[0]["impact_1k_buy"] == 1.2
    assert samples.iloc[0]["vantage"] == "vn-home"
    meta = json.loads((rundir / "run_meta.json").read_text())
    assert meta["run_id"] == "run1"
    assert meta["vantage"] == "vn-home"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd exchange-survey && python -m pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'survey.storage'`

- [ ] **Step 3: Write the store**

`survey/storage.py`:

```python
from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
import pandas as pd
from survey.models import (
    LatencySample, OrderbookSample, FundingRecord, FeeRecord,
)


class ParquetStore:
    def __init__(self, base_dir, run_id: str, vantage: str):
        self.dir = Path(base_dir) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.vantage = vantage
        self._latency: list[dict] = []
        self._samples: list[dict] = []
        self._funding: list[dict] = []
        self._events: list[dict] = []

    def add_latency(self, s: LatencySample) -> None:
        d = asdict(s)
        d["clock_skew_ms"] = s.clock_skew_ms
        self._latency.append(d)

    def add_sample(self, s: OrderbookSample) -> None:
        d = asdict(s)
        impact = d.pop("impact")
        for k, v in impact.items():
            d[f"impact_{k}"] = v
        self._samples.append(d)

    def add_funding(self, f: FundingRecord, fee: FeeRecord) -> None:
        row = asdict(f)
        row["maker"] = fee.maker
        row["taker"] = fee.taker
        self._funding.append(row)

    def add_event(self, exchange: str, ts_ms: int, kind: str, detail: str) -> None:
        self._events.append({"exchange": exchange, "ts_ms": ts_ms,
                             "type": kind, "detail": detail})

    def flush(self) -> None:
        self._write("latency.parquet", self._latency)
        self._write("orderbook_samples.parquet", self._samples)
        self._write("funding_fees.parquet", self._funding)
        self._write("events.parquet", self._events)

    def _write(self, name: str, rows: list[dict]) -> None:
        if not rows:
            return
        pd.DataFrame(rows).to_parquet(self.dir / name, index=False)

    def write_meta(self, extra: dict) -> None:
        meta = {"run_id": self.run_id, "vantage": self.vantage, **extra}
        (self.dir / "run_meta.json").write_text(json.dumps(meta, indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd exchange-survey && python -m pytest tests/test_storage.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
cd exchange-survey && git add -A && git commit -m "feat: Parquet storage layer"
```

---

## Task 7: Config + Collector engine

**Files:**
- Create: `exchange-survey/survey/config.py`
- Create: `exchange-survey/survey/collector.py`
- Test: `exchange-survey/tests/test_collector.py`

> The collector is tested with a **fake adapter** that yields scripted book messages and a
> short run duration, so no network and no real waiting.

- [ ] **Step 1: Write config**

`survey/config.py`:

```python
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class SurveyConfig:
    symbols: list[str] = field(default_factory=lambda: ["BTC-PERP", "ETH-PERP"])
    ladder_usd: list[float] = field(default_factory=lambda: [1_000, 10_000, 100_000, 1_000_000])
    ladder_labels: list[str] = field(default_factory=lambda: ["1k", "10k", "100k", "1m"])
    depth_bands: list[float] = field(default_factory=lambda: [0.001, 0.005])
    sample_interval_s: float = 5.0
    ping_interval_s: float = 7.0
    funding_interval_s: float = 60.0
    duration_s: float = 3 * 3600
    vantage: str = "vn-home"
```

- [ ] **Step 2: Write the failing test**

`tests/test_collector.py`:

```python
import asyncio
from survey.models import BookUpdate, BookLevel, LatencySample, FundingRecord, FeeRecord
from survey.config import SurveyConfig
from survey.collector import Collector


class FakeAdapter:
    name = "fake"

    def normalize_symbol(self, c): return c
    def contract_spec(self, c): return None

    async def stream_orderbook(self, symbols):
        for i in range(100):
            yield BookUpdate("fake", "BTC-PERP", 1000 + i,
                             [BookLevel(100.0, 50.0)], [BookLevel(100.1, 50.0)], True)
            await asyncio.sleep(0.001)

    async def ping(self):
        return LatencySample("fake", 1000, 5.0, 1000, 1000)

    async def fetch_funding(self, c):
        return FundingRecord("fake", c, 1000, 0.0001)

    async def fetch_fees(self, c):
        return FeeRecord("fake", c, 1000, 0.0002, 0.0005)


def test_collector_produces_samples_and_latency(tmp_path):
    cfg = SurveyConfig(symbols=["BTC-PERP"], duration_s=0.2,
                       sample_interval_s=0.05, ping_interval_s=0.05,
                       funding_interval_s=0.05, vantage="test")
    store = Collector(adapters=[FakeAdapter()], config=cfg,
                      base_dir=tmp_path, run_id="t").run()
    rundir = tmp_path / "t"
    import pandas as pd
    samples = pd.read_parquet(rundir / "orderbook_samples.parquet")
    assert len(samples) >= 1
    assert samples.iloc[0]["spread_bps"] > 0
    lat = pd.read_parquet(rundir / "latency.parquet")
    assert len(lat) >= 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd exchange-survey && python -m pytest tests/test_collector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'survey.collector'`

- [ ] **Step 4: Write the collector**

`survey/collector.py`:

```python
from __future__ import annotations
import asyncio
import time
from survey.config import SurveyConfig
from survey.models import OrderbookSample
from survey.orderbook import OrderBook
from survey.storage import ParquetStore


class Collector:
    def __init__(self, adapters, config: SurveyConfig, base_dir, run_id: str):
        self.adapters = adapters
        self.cfg = config
        self.store = ParquetStore(base_dir, run_id, config.vantage)
        # books[(exchange, symbol)] = OrderBook
        self.books: dict[tuple[str, str], OrderBook] = {}

    def run(self) -> ParquetStore:
        asyncio.run(self._run())
        self.store.flush()
        self.store.write_meta({
            "symbols": self.cfg.symbols,
            "duration_s": self.cfg.duration_s,
            "started_ms": int(time.time() * 1000),
        })
        return self.store

    async def _run(self) -> None:
        tasks = []
        for a in self.adapters:
            tasks.append(asyncio.create_task(self._book_task(a)))
            tasks.append(asyncio.create_task(self._probe_task(a)))
        tasks.append(asyncio.create_task(self._sampler_task()))
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=self.cfg.duration_s)
        except asyncio.TimeoutError:
            pass
        finally:
            for t in tasks:
                t.cancel()

    async def _book_task(self, adapter) -> None:
        try:
            async for bu in adapter.stream_orderbook(self.cfg.symbols):
                self.books[(adapter.name, bu.symbol)] = self.books.get(
                    (adapter.name, bu.symbol), OrderBook())
                self.books[(adapter.name, bu.symbol)].apply(bu)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # keep the run alive; record the gap
            self.store.add_event(adapter.name, int(time.time() * 1000),
                                 "ws_error", repr(e))

    async def _probe_task(self, adapter) -> None:
        next_funding = 0.0
        while True:
            try:
                self.store.add_latency(await adapter.ping())
                now = time.time()
                if now >= next_funding:
                    for sym in self.cfg.symbols:
                        f = await adapter.fetch_funding(sym)
                        fee = await adapter.fetch_fees(sym)
                        self.store.add_funding(f, fee)
                    next_funding = now + self.cfg.funding_interval_s
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.store.add_event(adapter.name, int(time.time() * 1000),
                                     "rest_error", repr(e))
            await asyncio.sleep(self.cfg.ping_interval_s)

    async def _sampler_task(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.sample_interval_s)
            ts = int(time.time() * 1000)
            for (exch, sym), ob in list(self.books.items()):
                if not ob.bids or not ob.asks:
                    continue
                impact = {}
                for usd, label in zip(self.cfg.ladder_usd, self.cfg.ladder_labels):
                    impact[f"{label}_buy"] = ob.impact_cost_bps(usd, "buy")
                    impact[f"{label}_sell"] = ob.impact_cost_bps(usd, "sell")
                self.store.add_sample(OrderbookSample(
                    ts_ms=ts, exchange=exch, symbol=sym, vantage=self.cfg.vantage,
                    mid=ob.mid(), spread_bps=ob.spread_bps(),
                    depth_bid_0p1pct=ob.depth_notional("bid", self.cfg.depth_bands[0]),
                    depth_ask_0p1pct=ob.depth_notional("ask", self.cfg.depth_bands[0]),
                    depth_bid_0p5pct=ob.depth_notional("bid", self.cfg.depth_bands[1]),
                    depth_ask_0p5pct=ob.depth_notional("ask", self.cfg.depth_bands[1]),
                    impact=impact,
                ))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd exchange-survey && python -m pytest tests/test_collector.py -v`
Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
cd exchange-survey && git add -A && git commit -m "feat: async Collector engine + SurveyConfig"
```

---

## Task 8: Analysis — summary + rankings

**Files:**
- Create: `exchange-survey/survey/analysis.py`
- Test: `exchange-survey/tests/test_analysis.py`

- [ ] **Step 1: Write the failing test**

`tests/test_analysis.py`:

```python
import pandas as pd
from survey.analysis import summarize


def test_summarize_computes_latency_percentiles_and_ranking(tmp_path):
    rundir = tmp_path / "run"
    rundir.mkdir()
    pd.DataFrame([
        {"ts_ms": 1, "exchange": "binance", "rtt_ms": 10.0, "ws_freshness_ms": 0,
         "clock_skew_ms": 5, "server_time_ms": 1, "local_time_ms": 1},
        {"ts_ms": 2, "exchange": "binance", "rtt_ms": 30.0, "ws_freshness_ms": 0,
         "clock_skew_ms": 5, "server_time_ms": 2, "local_time_ms": 2},
    ]).to_parquet(rundir / "latency.parquet")
    pd.DataFrame([
        {"ts_ms": 1, "exchange": "binance", "symbol": "BTC-PERP", "vantage": "t",
         "mid": 100.0, "spread_bps": 1.0, "depth_bid_0p1pct": 5, "depth_ask_0p1pct": 5,
         "depth_bid_0p5pct": 9, "depth_ask_0p5pct": 9,
         "impact_1k_buy": 1.2, "impact_1k_sell": 1.0},
    ]).to_parquet(rundir / "orderbook_samples.parquet")

    res = summarize(rundir)
    row = res["latency"].set_index("exchange").loc["binance"]
    assert row["rtt_p50"] == 20.0
    assert "binance" in res["spread"]["exchange"].values
    assert "ranking" in res
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd exchange-survey && python -m pytest tests/test_analysis.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'survey.analysis'`

- [ ] **Step 3: Write the analysis**

`survey/analysis.py`:

```python
from __future__ import annotations
from pathlib import Path
import pandas as pd


def _pct(s: pd.Series, q: float) -> float:
    return float(s.quantile(q))


def summarize(rundir) -> dict:
    rundir = Path(rundir)
    lat = pd.read_parquet(rundir / "latency.parquet")
    samples = pd.read_parquet(rundir / "orderbook_samples.parquet")

    latency = (lat.groupby("exchange")["rtt_ms"]
               .agg(rtt_p50=lambda s: _pct(s, 0.50),
                    rtt_p95=lambda s: _pct(s, 0.95),
                    rtt_p99=lambda s: _pct(s, 0.99))
               .reset_index())

    spread = (samples.groupby("exchange")["spread_bps"]
              .median().reset_index().rename(columns={"spread_bps": "spread_bps_median"}))

    impact_cols = [c for c in samples.columns if c.startswith("impact_")]
    impact = samples.groupby("exchange")[impact_cols].median().reset_index()

    # ranking: lower is better for rtt_p50 and spread_bps_median
    ranking = latency.merge(spread, on="exchange")
    ranking["rank_latency"] = ranking["rtt_p50"].rank()
    ranking["rank_spread"] = ranking["spread_bps_median"].rank()
    ranking["rank_overall"] = (ranking["rank_latency"] + ranking["rank_spread"]).rank()

    return {"latency": latency, "spread": spread, "impact": impact, "ranking": ranking}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd exchange-survey && python -m pytest tests/test_analysis.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
cd exchange-survey && git add -A && git commit -m "feat: analysis summaries + rankings"
```

---

## Task 9: Markdown report

**Files:**
- Create: `exchange-survey/survey/report.py`
- Test: `exchange-survey/tests/test_report.py`

- [ ] **Step 1: Write the failing test**

`tests/test_report.py`:

```python
import pandas as pd
from survey.report import render_markdown


def test_render_markdown_contains_tables_and_caveat():
    res = {
        "latency": pd.DataFrame([{"exchange": "binance", "rtt_p50": 20.0,
                                  "rtt_p95": 28.0, "rtt_p99": 30.0}]),
        "spread": pd.DataFrame([{"exchange": "binance", "spread_bps_median": 1.0}]),
        "impact": pd.DataFrame([{"exchange": "binance", "impact_1k_buy": 1.2,
                                 "impact_1k_sell": 1.0}]),
        "ranking": pd.DataFrame([{"exchange": "binance", "rank_overall": 1.0}]),
    }
    md = render_markdown(res, meta={"run_id": "r1", "vantage": "vn-home"})
    assert "# Exchange Survey Report" in md
    assert "binance" in md
    assert "impact cost" in md.lower()      # the slippage caveat must be present
    assert "vn-home" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd exchange-survey && python -m pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'survey.report'`

- [ ] **Step 3: Write the report renderer**

`survey/report.py`:

```python
from __future__ import annotations
import pandas as pd

_CAVEAT = (
    "> **Slippage caveat:** the impact-cost figures are *theoretical impact cost "
    "from L2 snapshots* (static book, sole taker, no fees, no latency drift) — "
    "not realized fill slippage."
)


def render_markdown(res: dict, meta: dict) -> str:
    parts: list[str] = []
    parts.append("# Exchange Survey Report\n")
    parts.append(f"- **Run:** {meta.get('run_id')}")
    parts.append(f"- **Vantage:** {meta.get('vantage')}\n")

    parts.append("## Latency (REST round-trip, ms)\n")
    parts.append(res["latency"].to_markdown(index=False))
    parts.append("\n## Spread (median bps)\n")
    parts.append(res["spread"].to_markdown(index=False))
    parts.append("\n## Impact cost (median bps)\n")
    parts.append(_CAVEAT + "\n")
    parts.append(res["impact"].to_markdown(index=False))
    parts.append("\n## Overall ranking\n")
    parts.append(res["ranking"].to_markdown(index=False))
    return "\n".join(parts) + "\n"
```

> `to_markdown` needs `tabulate`. Add it: `cd exchange-survey && echo tabulate >> requirements.txt && pip install tabulate`

- [ ] **Step 4: Run test to verify it passes**

Run: `cd exchange-survey && pip install tabulate && python -m pytest tests/test_report.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
cd exchange-survey && git add -A && git commit -m "feat: Markdown report renderer with slippage caveat"
```

---

## Task 10: CLI entry point + end-to-end wiring

**Files:**
- Create: `exchange-survey/main.py`
- Test: manual end-to-end run

- [ ] **Step 1: Write the CLI**

`main.py`:

```python
from __future__ import annotations
import argparse
import json
import time
from pathlib import Path
from survey.config import SurveyConfig
from survey.collector import Collector
from survey.analysis import summarize
from survey.report import render_markdown
from survey.adapters.binance import BinanceAdapter


def main() -> None:
    p = argparse.ArgumentParser(description="Exchange survey — Phase 1 (Binance)")
    p.add_argument("--duration", type=float, default=300, help="seconds to collect")
    p.add_argument("--symbols", default="BTC-PERP,ETH-PERP")
    p.add_argument("--vantage", default="vn-home")
    p.add_argument("--out", default="../data-report/exchange-survey")
    args = p.parse_args()

    run_id = time.strftime("%Y%m%d-%H%M%S")
    cfg = SurveyConfig(
        symbols=args.symbols.split(","),
        duration_s=args.duration,
        vantage=args.vantage,
    )
    base_dir = Path(args.out)
    print(f"Collecting {cfg.duration_s}s into {base_dir / run_id} ...")
    Collector([BinanceAdapter()], cfg, base_dir, run_id).run()

    rundir = base_dir / run_id
    res = summarize(rundir)
    meta = json.loads((rundir / "run_meta.json").read_text())
    md = render_markdown(res, meta)
    (rundir / "report.md").write_text(md)
    print(f"Report written to {rundir / 'report.md'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run a short live end-to-end (requires internet)**

Run: `cd exchange-survey && python main.py --duration 30 --symbols BTC-PERP,ETH-PERP`
Expected: prints "Collecting ..." then "Report written to ...". Open the `report.md` and confirm latency/spread/impact tables are populated with Binance numbers.

- [ ] **Step 3: Run the full offline suite once more**

Run: `cd exchange-survey && python -m pytest -m "not network" -v`
Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
cd exchange-survey && git add -A && git commit -m "feat: CLI entry point wiring collector -> analysis -> report"
```

---

## Done criteria (Phase 1)

- `python main.py --duration 30` produces a `report.md` with real Binance latency, spread, and impact-cost tables plus the slippage caveat.
- Parquet files (`latency`, `orderbook_samples`, `funding_fees`, `events`) + `run_meta.json` written under `data-report/exchange-survey/<run-id>/`.
- `pytest -m "not network"` is green with zero network access.
- The `ExchangeAdapter` Protocol is the only thing Phase 2 adapters must implement — collector/storage/analysis/report are exchange-agnostic.

## Deferred to later plans

- **Phase 2:** Bybit, OKX, KuCoin, BingX, Hyperliquid adapters (each: parsers + fixtures + network methods), plus per-exchange WS freshness (`ws_freshness_ms`) and book **update rate** (updates/sec) populated from event timestamps, with a real clock-skew correction applied during sampling.
- **Phase 3:** PNG charts + Streamlit dashboard reading the same Parquet.
- **Robustness:** WS reconnect/backoff loop, depth-stream delta syncing if partial-depth proves insufficient, multi-vantage merge tooling.
