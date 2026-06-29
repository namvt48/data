# Khảo sát đặc tính sàn giao dịch — Design Spec

**Ngày:** 2026-06-09
**Trạng thái:** Đã duyệt thiết kế, chờ duyệt spec

## 1. Mục tiêu

Xây một công cụ **nghiên cứu/báo cáo** đo và so sánh đặc tính của 6 sàn:
**Binance, OKX, KuCoin, BingX, Bybit, Hyperliquid**.

Không đặt lệnh thật. Daemon chạy liên tục trong **vài tiếng**, ghi time-series, rồi
tổng hợp thành báo cáo so sánh + dữ liệu thô + dashboard.

### Phạm vi thị trường
- Loại sản phẩm: **USDT Perpetual / Futures** (Hyperliquid chỉ có perp).
- Tập cặp: **top 20–50 theo volume** (gồm cả coin thanh khoản thấp để thấy khác biệt).

### Môi trường chạy
- Chạy được ở nhiều nơi (máy cá nhân VN, VPS gần sàn).
- Mỗi record gắn nhãn `vantage` (vd `vn-home`, `vps-tokyo`) để so sánh latency theo location.

## 2. Chỉ số đo (Metrics)

| Nhóm | Metric | Cách tính |
|------|--------|-----------|
| Latency | REST RTT p50/p95/p99 | round-trip `/time` hoặc `/ping` |
| Latency | WS freshness | `now − exchange_event_ts` (sau khi trừ clock-skew) |
| Latency | Clock skew | `server_time − local_time` từ REST `/time` |
| Latency | Update rate | số book update / giây |
| Ổn định | Error rate, WS disconnects, data gaps | đếm trong phiên |
| Slippage | **Impact cost** ở ladder **$1k / $10k / $100k / $1M** | walk book L2, VWAP thực hiện vs mid-price, cả buy & sell |
| Thanh khoản | Bid-ask spread (bps) | từ snapshot book |
| Thanh khoản | Depth ±0.1% / ±0.5% | tổng notional quanh mid |
| Phí | Maker / taker fee | REST |
| Phí | Funding rate | REST, **quy về %/8h** để so sánh công bằng |

### Caveat bắt buộc ghi trong report
Slippage ở đây là **impact cost lý thuyết từ snapshot L2**: book tĩnh, mình là taker
duy nhất, chưa tính phí và latency drift. **Không phải** slippage khớp lệnh thật.

## 3. Kiến trúc

Pipeline một chiều:

```
[6 Exchange Adapters] → [Collector Engine] → [Raw Parquet store] → [Analysis] → {Markdown report, PNG charts, Streamlit}
```

Quyết định kiến trúc client: **hand-rolled toàn bộ** (tự viết REST + WS cho từng sàn,
không dùng CCXT). Đánh đổi: tốn công gấp ~6 lần và toàn bộ gánh nặng chuẩn hoá nằm ở
mình, bù lại số latency sạch nhất và kiểm soát tối đa. Để kiểm soát rủi ro, mỗi sàn là
một adapter độc lập, test riêng được.

### 3.1 Adapter contract (trái tim dự án)

Mỗi sàn = module `adapters/<exchange>.py` cài đặt cùng interface `ExchangeAdapter`.
Collector không cần biết chi tiết từng sàn.

```python
class ExchangeAdapter(Protocol):
    name: str
    # --- chuẩn hoá ---
    def normalize_symbol(self, canonical: str) -> str      # "BTC-PERP" -> tên native
    def contract_spec(self, symbol) -> ContractSpec        # multiplier, coin-unit vs USD-contract, tick size
    # --- WS: duy trì orderbook + đo freshness ---
    async def stream_orderbook(self, symbols) -> AsyncIterator[BookUpdate]
    # --- REST: latency probe + funding/fee ---
    async def ping(self) -> LatencySample                  # round-trip /time hoặc /ping
    async def fetch_funding(self, symbol) -> FundingRecord
    async def fetch_fees(self, symbol) -> FeeRecord
    async def server_time(self) -> int                     # cho clock-skew
```

Mọi dữ liệu adapter trả ra đều ở **dạng record chuẩn hoá nội bộ** (cùng đơn vị:
giá USDT, size quy ra notional USD, funding quy về %/8h). Đây là nơi xử lý khác biệt:
- **Tên symbol:** `BTCUSDT` (Binance/Bybit) / `BTC-USDT-SWAP` (OKX) / `BTC` (Hyperliquid) …
- **Contract spec:** multiplier, contract = X USD vs coin-unit (ảnh hưởng trực tiếp quy đổi notional).
- **Funding:** chu kỳ 8h vs Hyperliquid 1h.

Sai ở lớp này → slippage và funding so sánh sai âm thầm. Đây là rủi ro số một của dự án.

### 3.2 Collector Engine

Async, mỗi sàn một nhóm task độc lập (lỗi 1 sàn không làm chết phiên đo):

- **WS task / sàn:** duy trì orderbook local từ snapshot + delta. Nguồn cho slippage + spread + freshness.
- **REST probe task / sàn:** mỗi ~5–10s gọi `/time` + `/ping` đo round-trip; mỗi ~1 phút pull funding/fee. Tách khỏi WS để tránh rate-limit.
- **Sampler:** mỗi N giây (mặc định 5s) chụp trạng thái book hiện tại từng cặp → tính metrics → ghi 1 row Parquet.

**Vì sao duy trì book qua WS, không poll REST:** 30 cặp × 6 sàn = 180 sổ lệnh; poll REST
mỗi sàn ~1Hz sẽ bị throttle/ban trong phiên vài tiếng. REST chỉ dùng cho latency probe +
funding/fee định kỳ.

### 3.3 Xử lý lỗi
- Mỗi task có retry + exponential backoff.
- Mất WS → tự reconnect + đánh dấu `gap` trong `events`.
- Sàn fail hẳn → vẫn ra report với cờ "dữ liệu thiếu", không làm hỏng cả phiên.

## 4. Lưu trữ

Parquet là nguồn chính. Mỗi run một thư mục:

```
data-report/exchange-survey/<run-id>/
  orderbook_samples.parquet   # ts, exchange, symbol, spread_bps, depth_*, impact_{1k,10k,100k,1m}_{buy,sell}, vantage
  latency.parquet             # ts, exchange, rtt_ms, ws_freshness_ms, clock_skew_ms, update_rate, vantage
  funding_fees.parquet        # ts, exchange, symbol, funding_8h, maker, taker
  events.parquet              # ts, exchange, type(disconnect|error|gap), detail
  run_meta.json               # config, thời gian bắt đầu/kết thúc, vantage, danh sách symbol, version code
```

`run-id` + `vantage` cho phép chạy nhiều nơi/nhiều lần rồi gộp so sánh. Export CSV chỉ là
một dòng dump từ Parquet (đáp ứng yêu cầu "dữ liệu thô CSV/Parquet").

Hạ tầng giữ phẳng: file Parquet trên đĩa, **không** TSDB/Kafka cho công cụ chạy vài tiếng.

## 5. Phân tích & Đầu ra

Phân tích tách hẳn khỏi thu thập: đọc Parquet → tính bảng tổng hợp p50/p95/p99, trung vị
spread, đường cong impact theo size, ranking từng tiêu chí. Chạy lại được mà không thu thập lại.

Một pipeline, các lớp dẫn xuất mỏng:

1. **Markdown report + bảng xếp hạng** (deliverable chính): bảng so sánh 6 sàn, rank theo từng tiêu chí, kết luận, kèm caveat slippage.
2. **PNG charts:** latency p50/p95 theo sàn; đường cong slippage theo size; spread theo thời gian; funding so sánh.
3. **Streamlit dashboard:** lớp mỏng đọc cùng Parquet, lọc tương tác theo symbol/sàn/vantage.

## 6. Testing

- **Adapter:** test bằng **sample payload thật đã ghi lại** thành fixture → test normalize symbol/contract/funding mà không gọi mạng.
- **Impact cost:** test bằng order book giả đã biết đáp án.
- **Pipeline phân tích:** end-to-end bằng Parquet nhỏ giả lập.

## 7. Scope & Phasing

Vì hand-rolled cả 6 sàn là khối lượng lớn, build theo **lát cắt dọc**:

1. **Phase 1 — Binance trọn vẹn:** adapter → collector → storage → analysis → report. Chốt khung end-to-end, có kết quả chạy được sớm.
2. **Phase 2 — nhân bản adapter** cho Bybit, OKX, KuCoin, BingX, Hyperliquid (mỗi sàn: adapter + fixture test).
3. **Phase 3 — lớp đầu ra:** PNG charts + Streamlit dashboard.

## 8. Cấu hình mặc định (chốt sẵn, không hỏi lại)

- Slippage ladder: **$1k / $10k / $100k / $1M**, cả buy & sell.
- Sample interval: **5s**.
- REST latency probe: mỗi **5–10s**; funding/fee pull: mỗi **1 phút**.
- Funding chuẩn hoá về **%/8h**.
- Depth bands: **±0.1%** và **±0.5%** quanh mid.
- Public endpoints, **không** API key, **không** đặt lệnh thật.

## 9. Câu hỏi mở / chưa chốt

- Danh sách chính xác 20–50 cặp: lấy động theo top volume của Binance lúc khởi động, rồi
  map sang các sàn khác (cặp nào sàn không niêm yết thì bỏ qua, ghi nhận).
