# ADX Trend Follow — Chiến lược tối ưu cuối cùng

> Nguồn: [researchadx.md](../researchadx.md) | Cập nhật: 25/05/2026

---

## 1. Ý tưởng cốt lõi

Khi BTC có xu hướng mạnh (ADX >= 50), các altcoin có biến động bất thường (Volume Spike + Price Movement) cùng chiều BTC → xác suất cao tiếp tục đi theo hướng đó trong ngắn hạn.

**Nguyên tắc:** Đi THEO momentum (FOLLOW), không chống lại.

---

## 2. Điều kiện vào lệnh (Entry)

Kiểm tra tuần tự 4 bước — thiếu bất kỳ bước nào thì bỏ qua:

```
Bước 1: BTC ADX(7) >= 50?
         └─ KHÔNG → Chờ, không vào lệnh

Bước 2: Volume Spike >= 2x median 4 nến trước (M15)?
         └─ KHÔNG → Bỏ qua coin này

Bước 3: |Price Movement| >= 0.8% VÀ <= 20% so với median 4 nến trước?
         └─ KHÔNG → Bỏ qua coin này

Bước 4: Coin cùng hướng BTC?
         (PM > 0 khi BTC tăng) HOẶC (PM < 0 khi BTC giảm)
         └─ KHÔNG → Bỏ qua coin này
         └─ CÓ   → 🔔 VÀO LỆNH theo hướng BTC
```

| Tham số | Giá trị | Lý do chọn |
|---|---|---|
| Timeframe | M15 | Cân bằng tốt nhất: tín hiệu chất lượng + đủ số lệnh |
| BTC ADX period | 7 | Nhanh, phản ứng sớm |
| BTC ADX threshold | >= 50 | ADX 64 (gốc) chỉ active 3.4% thời gian — quá ít cơ hội; ADX 50 active nhiều hơn 38%, Sharpe vẫn 4.1 |
| Volume Spike | >= 2x | Bot gốc dùng 4x → chỉ 362 trades/3 tháng; 2x → 2,546 trades, Sharpe > 4 |
| Price Movement | 0.8% – 20% | 0.8% tăng lệnh ~70% so với 1.2% (gốc), WR vẫn 69.4%; cap 20% loại pump/dump cực đoan |
| BTC Direction | Bật | +29% PnL, +24% Sharpe so với không dùng |
| Lookback Vol/Price | 4 nến (M15) | — |
| Lookback BTC Dir | 2 nến (M15 = 30 phút) | — |

---

## 3. Quản lý lệnh (Exit — Trail+BE)

> Trail+BE là yếu tố sống còn: Fixed TP/SL lỗ 100% thời gian ở cùng điều kiện.

### Thông số

| Tham số | Giá trị |
|---|---|
| Stop Loss ban đầu | -0.5% |
| BreakEven kích hoạt tại | +0.3% |
| Trailing Stop khoảng cách | 0.5% |
| Take Profit cap | +3.0% |
| Max hold | 40 nến M15 (~10 giờ) |

### Minh họa (LONG @ $100.00)

```
Vào LONG @ $100.00
│
├── SL ban đầu: $99.50  (-0.5%)
│
├── Giá lên $100.30 (+0.3%) → BREAKEVEN kích hoạt
│   └── SL dịch lên $100.00 → không thể lỗ từ đây
│
├── Giá lên $101.00 → Trailing SL = $100.50
├── Giá lên $102.00 → Trailing SL = $101.50
├── Giá lên $103.00 (+3.0%) → TP CAP → chốt lời
│
│   HOẶC
│
├── Giá quay đầu từ $102.00 xuống $101.50 → TRAILING STOP
│   └── Chốt +1.5% thay vì để chạy về 0
│
│   HOẶC
│
└── Giá giảm ngay từ đầu → SL -0.5% → lỗ nhỏ, bảo vệ vốn
```

---

## 4. Lookback theo timeframe

Nếu chạy trên timeframe khác, dùng bảng sau:

| Timeframe | Vol LB | Price LB | BTC Dir LB | Max Hold |
|---|---|---|---|---|
| 1m | 20 nến | 20 nến | 10 nến | 600 nến (~10h) |
| 3m | 15 | 15 | 8 | 200 (~10h) |
| 5m | 12 | 12 | 6 | 120 (~10h) |
| **15m** | **4 nến** | **4 nến** | **2 nến** | **40 nến (~10h)** |
| 30m | 4 | 4 | 2 | 20 (~10h) |
| 1h | 4 | 4 | 1 | 10 (~10h) |
| 4h | 4 | 4 | 1 | 3 (~12h) |

---

## 5. Kết quả backtest

### 2 năm (2023–2024) — Config tối ưu

| Quý | PnL | Cộng dồn |
|---|---|---|
| Q1 2023 | +$2,845 | $2,845 |
| Q2 2023 | +$1,956 | $4,801 |
| Q3 2023 | +$2,233 | $7,034 |
| Q4 2023 | +$2,051 | $9,085 |
| Q1 2024 | +$5,944 | $15,029 |
| Q2 2024 | +$3,478 | $18,507 |
| Q3 2024 | +$3,375 | $21,882 |
| Q4 2024 | +$3,289 | $25,171 |

### Tổng hợp

| Metric | Giá trị |
|---|---|
| Tổng 2 năm | **+$25,171** |
| Trung bình/năm | ~$12,500 |
| Quý dương liên tiếp | **8/8 (100%)** |
| Sharpe | 3.5 – 4.2 |
| Win Rate | 65 – 70% |
| Trades/tháng | ~750 – 850 |
| Max Drawdown | -$200 đến -$300 |

---

## 6. Những điều không làm

| Không làm | Lý do |
|---|---|
| Dùng REVERSE mode | Momentum indicator + đặt cược ngược momentum = mâu thuẫn kiến trúc |
| Dùng Fixed TP/SL | Lỗ 100% thời gian ở cùng điều kiện |
| ADX threshold > 55 | Quá ít tín hiệu, không đủ thống kê |
| Volume Spike > 4x | 362 trades/3 tháng — không đủ để scale |
| Bỏ BTC Direction filter | Giảm 29% PnL và 24% Sharpe |
| Chạy M5 | Quá nhiễu, tổng PnL âm |
