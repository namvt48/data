# ADX TREND FOLLOW — TOÀN BỘ RESEARCH & CHỈ DẪN
> Phạm Long Vũ | Cập nhật: 12/05/2026

---

## MỤC LỤC
1. [Tóm tắt chiến lược](#1-tóm-tắt-chiến-lược)
2. [Dữ liệu gốc: Bot_v1 "The Leader"](#2-dữ-liệu-gốc-bot_v1-the-leader)
3. [Tối ưu Exit trên 16 ngày gốc](#3-tối-ưu-exit-trên-16-ngày-gốc)
4. [Stress Test lịch sử (Bot gốc)](#4-stress-test-lịch-sử-bot-gốc)
5. [Tín hiệu thay thế (Alternative Entries)](#5-tín-hiệu-thay-thế)
6. [Backtest toàn diện (90 ngày x 119 coin x 144 combo)](#6-backtest-toàn-diện)
7. [ADX Trend Backtest (273 combo)](#7-adx-trend-backtest)
8. [Expanded Backtest — Tìm filter tối ưu](#8-expanded-backtest)
9. [Kết quả 2 năm (2023-2024)](#9-kết-quả-2-năm)
10. [Chiến lược tối ưu cuối cùng](#10-chiến-lược-tối-ưu-cuối-cùng)
11. [Chỉ dẫn sử dụng Signal Bot](#11-chỉ-dẫn-sử-dụng-signal-bot)
12. [Lưu ý quan trọng](#12-lưu-ý-quan-trọng)

---

## 1. TÓM TẮT CHIẾN LƯỢC

**Ý tưởng cốt lõi:** Khi BTC có xu hướng mạnh (ADX >= 50), altcoin có biến động bất thường (Volume Spike + Price Movement) theo cùng hướng BTC → có xác suất cao tiếp tục đi theo hướng đó.

**Tham số tối ưu (sau toàn bộ research):**

| Tham số | Giá trị | Giải thích |
|---|---|---|
| Timeframe | M15 | Cân bằng tốt nhất giữa chất lượng tín hiệu và số lệnh |
| Hướng | FOLLOW | Đi theo momentum, KHÔNG chống lại |
| ADX | ADX(7) trên BTC >= 50 | Xác nhận BTC đang trending |
| Volume Spike | >= 2x median 4 nến | Khối lượng bất thường |
| Price Movement | 0.8% - 20% vs median 4 nến | Biến động giá đủ lớn nhưng không quá cực đoan |
| BTC Direction | ON | Coin phải đi cùng hướng BTC |
| Exit | Trail+BE | SL 0.5% → BE tại +0.3% → Trail 0.5% → TP cap 3% |

**Kết quả:** +$25,171 / 2 năm, 8/8 quý dương, Sharpe 3.5-4.2

---

## 2. DỮ LIỆU GỐC: Bot_v1 "The Leader"

### 2.1 Tham số bot gốc

| Tham số | Giá trị |
|---|---|
| Timeframe | M1 |
| Volume Spike | 4x-5000x vs median 60 nến M1 |
| Price Movement | 1.2%-20% vs median 60 nến M1 |
| BTC Direction | So sánh close BTC với 30 nến trước |
| ADX | Period 7, NORMAL >= 64, REVERSE < 64 |
| NORMAL mode | TP 2%, SL 1%, RR 2:1, FOLLOW momentum |
| REVERSE mode | TP 1%, SL 2%, RR 1:2, CHỐNG momentum |
| Số cặp | 280 altcoin futures |
| Size | ~$610/lệnh |

### 2.2 Kết quả 16 ngày (06-21/04/2026)

| Metric | Giá trị |
|---|---|
| Tổng lệnh | 15,267 |
| WR tổng | 62.2% |
| PnL trước phí | -$3,870 |
| Phí | ~$17,382 |
| **PnL sau phí** | **-$21,252** |
| REVERSE | 92.9% tổng lệnh |
| ADX < 64 | 96.6% thời gian |

### 2.3 Vấn đề gốc rễ

| Chế độ | Lệnh | WR | WR hòa vốn | Gap | PnL trước phí |
|---|---|---|---|---|---|
| REVERSE LONG | 5,643 | 67.7% | 66.7% | +1.0pp | +$1,770 |
| **REVERSE SHORT** | **8,604** | **64.6%** | **66.7%** | **-2.1pp** | **-$5,220** |
| NORMAL LONG | 532 | 36.3% | 33.3% | +3.0pp | +$470 |
| NORMAL SHORT | 488 | 27.3% | 33.3% | -6.0pp | -$890 |

**Mâu thuẫn kiến trúc:** Bot dùng chỉ báo momentum (VS, PM) nhưng đặt cược NGƯỢC momentum (REVERSE). Với RR 1:2, cần WR >= 66.7% để hòa vốn — bot chỉ đạt 62.2%.

---

## 3. TỐI ƯU EXIT TRÊN 16 NGÀY GỐC

### 3.1 Thử 48 combo TP/SL cố định
- TP từ 0.5% đến 3%, SL từ 0.3% đến 2%
- **Kết quả: 0/48 combo đạt WR >= 50% VÀ NET > 0**

### 3.2 Trail+BE — Bước đột phá

| Exit Strategy | PnL | Sharpe |
|---|---|---|
| Baseline (REVERSE TP1/SL2) | -$21,252 | N/A |
| Best trailing stop only | +$13,792 | N/A |
| **Trail+BE (SL0.5/BE0.3/trail0.5/TP3)** | **+$23,630** | **27.3** |

### 3.3 Walk-Forward Validation

| Giai đoạn | PnL | Sharpe |
|---|---|---|
| Train (9 ngày, 06-14/04) | +$11,105 | 22.3 |
| Test (7 ngày, 15-21/04) | +$12,525 | 37.7 |

**Cảnh báo:** Kết quả 16 ngày gốc là outlier. Cần stress test dài hạn.

---

## 4. STRESS TEST LỊCH SỬ (Bot gốc, 240 ngày)

### 4.1 Thiết kế

| Tham số | Giá trị |
|---|---|
| Timeframe | M5 (M1 không có dữ liệu lịch sử) |
| Logic entry | Giữ nguyên alpha.md gốc (REVERSE + NORMAL) |
| Coins | 50 (42 có dữ liệu) |
| 4 giai đoạn | Jul-Aug 2025, Oct-Nov 2025, Dec25-Jan26, Feb-Mar 2026 |

### 4.2 Kết quả — TẤT CẢ ĐỀU LỖ

| Strategy | Kỳ dương | Tổng NET | Worst DD |
|---|---|---|---|
| Baseline (REV 1:2, NOR 2:1) | 1/4 | -$14,860 | -$10,331 |
| Trail+BE TP3/SL0.5 | 0/4 | **-$11,629** | -$4,577 |
| Trail+BE TP2/SL0.5 | 0/4 | -$11,627 | -$4,577 |
| Trail TP2/SL1 | 0/4 | -$11,416 | -$4,618 |

**Kết luận:** Logic REVERSE gốc KHÔNG có alpha bền vững. Trail+BE cải thiện nhưng không cứu được chiến lược sai.

---

## 5. TÍN HIỆU THAY THẾ (Alternative Entries)

### 5.1 Các chiến lược entry được test

| Strategy | Mô tả |
|---|---|
| Alpha REVERSE | Logic gốc, đi ngược momentum |
| Alpha FOLLOW | Cùng tín hiệu, đi THEO momentum |
| Volume Spike Only | Chỉ VS + PM, không BTC/ADX |
| RSI Mean Reversion | RSI(14) < 30 → LONG, > 70 → SHORT |
| BB Breakout | Giá vượt BB(20,2) |
| Random | Ngẫu nhiên (đối chứng) |

### 5.2 Kết quả tổng hợp 4 giai đoạn (Trail+BE)

| Entry Signal | Tổng PnL | Kỳ dương | Đánh giá |
|---|---|---|---|
| **Alpha FOLLOW** | **+$3,823** | **1/4** | **Tốt nhất** |
| Alpha REVERSE | Âm | 0/4 | Lỗ |
| Vol Spike Only | Âm | 0/4 | Lỗ |
| RSI Mean Rev | -$100,446 | 0/4 | Thảm họa |
| BB Breakout | -$212,481 | 0/4 | Thảm họa |
| Random (đối chứng) | -$22,729 | 0/4 | Baseline phí |

**Phát hiện:** FOLLOW direction là hướng đi đúng duy nhất.

---

## 6. BACKTEST TOÀN DIỆN (90 ngày x 119 coin x 144 combo)

### 6.1 Thiết kế

| Tham số | Giá trị |
|---|---|
| Giai đoạn | 10/01 - 10/04/2026 (90 ngày) |
| Timeframe | M5 |
| Coins | 119 |
| Filter levels | 4: Base(4x,1.2%), Medium(8x,1.5%), Strict(12x,2%), Ultra(20x,2.5%) |
| Directions | 2: FOLLOW, REVERSE |
| Exit configs | 6: TP1/SL2, TP2/SL1, TP3/SL1, T+B 3/0.5, T+B 3/1, T+B 5/1 |
| Market regimes | 3: All, BTC_Up, BTC_Down |
| **Tổng combo** | **144** |

### 6.2 Kết quả

| Metric | Giá trị |
|---|---|
| Combo dương | 16/144 (11.1%) |
| **Combo robust (dương cả bull + bear)** | **0/144** |
| Phí chiếm | 76% lợi nhuận gộp |

### 6.3 Best combo

| Tham số | Giá trị |
|---|---|
| Filter | Base (4x, 1.2%) |
| Direction | FOLLOW |
| Exit | T+B 3/0.5 |
| Regime | BTC_Down only |
| NET | +$9,640 |
| Sharpe | 3.0 |

Nhưng cùng combo ở BTC_Up: **-$3,270** → không consistent.

**Kết luận:** Trên M5 với filter gốc (4x, 1.2%), không có combo nào robust qua cả bull và bear.

---

## 7. ADX TREND BACKTEST (273 combo)

### 7.1 Thiết kế — Thay đổi chiến lược hoàn toàn

| Tham số | Giá trị |
|---|---|
| Direction | FOLLOW only (bỏ REVERSE) |
| ADX | Làm điều kiện entry (>= threshold), KHÔNG chia regime |
| ADX periods | 7, 14, 21 |
| ADX thresholds | 50, 55, 60 |
| Exit strategies | 7 loại (Fixed 3:1, 4:1, Trail+BE TP3/4, ADX Exit combo) |
| Timeframes | M5, M15, H1 |
| Giai đoạn | Apr 2026 (1M), Feb-Apr 2026 (3M) |
| Coins | 40 |
| **Tổng combo** | **273** |

### 7.2 Kết quả theo timeframe

| Timeframe | Tổng PnL | Trades | Avg WR | Đánh giá |
|---|---|---|---|---|
| M5 | -$11,774 | 59,297 | 46.3% | Quá nhiễu |
| **M15** | **+$2,858** | **12,530** | **49.8%** | **Tốt nhất** |
| H1 | +$1,141 | 2,492 | 54.8% | Ít lệnh |

### 7.3 Kết quả theo ADX period

| ADX Period | Tổng PnL | Trades | Đánh giá |
|---|---|---|---|
| ADX(7) | -$10,941 | 67,627 | Nhiều tín hiệu nhất, nhạy nhất |
| **ADX(14)** | **+$3,737** | **6,034** | **Ổn định nhất** |
| ADX(21) | -$570 | 658 | Quá ít tín hiệu |

### 7.4 Top 5 combo

| Config | TF | PnL | Trades | WR | Sharpe |
|---|---|---|---|---|---|
| ADX(7)>=50, Fixed 3:1 | M5 | +$850 | 653 | 13.3% | 3.82 |
| ADX(7)>=50, Fixed 4:1 | M5 | +$802 | 653 | 6.3% | 3.50 |
| ADX(7)>=50, ADXExit+Fixed 4:1 | M5 | +$786 | 653 | 51.5% | 4.15 |
| ADX(7)>=55, Fixed 3:1 | M15 | +$772 | 378 | 24.6% | 3.45 |
| ADX(7)>=50, ADXExit+Fixed 3:1 | M5 | +$741 | 653 | 51.5% | 3.99 |

**Phát hiện:** M15 + ADX(7) >= 50 là hướng đi đúng. Cần tối ưu filter tiếp.

---

## 8. EXPANDED BACKTEST — TÌM FILTER TỐI ƯU

### 8.1 Thiết kế

| Tham số | Giá trị |
|---|---|
| Direction | FOLLOW only |
| Timeframe | M15 |
| ADX | Period 7 (cố định) |
| Coins | 40 |
| Phần A | 17 filter combo x 2 exit (3M Feb-Apr 2026) |
| Phần B | 8 filter tốt nhất x 2 exit x 4 quý (2024) |
| Phần C | Lặp lại 2023 để kiểm tra consistency |

### 8.2 Phần A — Filter Search (3M, Trail+BE)

| # | Filter | Trades | WR | NET | Sharpe | MaxDD |
|---|---|---|---|---|---|---|
| 1 | **VS2x PM0.8% BTC ADX50** | **2,546** | **69.4%** | **+$5,073** | **4.1** | **-$292** |
| 2 | VS2x PM0.5% BTC ADX50 | 3,120 | 68.6% | +$4,803 | 3.6 | -$331 |
| 3 | VS2x PM0.8% NoBTC ADX55 | 1,860 | 69.1% | +$3,611 | 3.3 | -$260 |
| 4 | VS2x PM0.5% NoBTC ADX55 | 2,392 | 68.1% | +$3,349 | 2.9 | -$336 |
| 5 | VS3x PM0.8% BTC ADX55 | 969 | 71.7% | +$1,981 | 3.0 | -$234 |
| 6 | VS2x PM0.8% BTC ADX55 | ~1,800 | ~68% | +$3,200 | 3.0 | ~-$280 |
| 7 | VS4x PM1.2% BTC ADX55 (gốc) | 362 | 84.3% | +$686 | 4.11 | -$32 |

**Phát hiện then chốt:**
- Nới lỏng filter (VS 4x→2x, PM 1.2%→0.8%) tăng số lệnh **7x** (362→2,546) mà vẫn giữ Sharpe > 4
- ADX 50 tốt hơn 55 (nhiều cơ hội hơn)
- BTC Direction tốt hơn NoBTC (+29% PnL, +24% Sharpe)

### 8.3 Phần B — Test 1 năm 2024 (Trail+BE, theo quý)

| Config | Q1 | Q2 | Q3 | Q4 | **Cả năm** |
|---|---|---|---|---|---|
| **VS2x PM0.8% BTC ADX50** | **+$5,944** | **+$3,478** | **+$3,375** | **+$3,289** | **+$16,086** |
| VS2x PM0.5% BTC ADX50 | +$5,584 | +$3,221 | +$3,133 | +$3,010 | +$14,948 |
| VS2x PM0.8% NoBTC ADX55 | +$5,767 | +$2,311 | +$2,025 | +$2,175 | +$12,278 |
| VS2x PM0.5% NoBTC ADX55 | +$5,562 | +$2,045 | +$1,889 | +$1,934 | +$11,430 |
| VS2x PM0.8% BTC ADX55 | +$5,182 | +$2,614 | +$2,503 | +$2,405 | +$12,704 |
| VS4x PM1.2% BTC ADX55 (gốc) | +$1,783 | +$1,002 | +$729 | +$662 | +$4,176 |

**Tất cả 8 config x 4 quý = 32/32 dương (100%)**

### 8.4 So sánh Trail+BE vs Fixed 3:1 (2024)

| Exit | VS2x PM0.8% BTC ADX50 |
|---|---|
| **Trail+BE** | **+$16,086** |
| Fixed 3:1 | **-$7,034** |

**Trail+BE là yếu tố sống còn. Fixed TP/SL lỗ 100% thời gian.**

---

## 9. KẾT QUẢ 2 NĂM (2023-2024)

**Config: VS2x PM0.8% BTC ADX(7)>=50 + Trail+BE, M15**

| Quý | PnL | Cộng dồn |
|---|---|---|
| Q1 2023 | +$2,845 | $2,845 |
| Q2 2023 | +$1,956 | $4,801 |
| Q3 2023 | +$2,233 | $7,034 |
| Q4 2023 | +$2,051 | $9,085 |
| Q1 2024 | +$5,944 | $15,029 |
| Q2 2024 | +$3,478 | $18,507 |
| Q3 2024 | +$3,375 | $21,882 |
| Q4 2024 | +$3,289 | **$25,171** |

```
PnL cộng dồn ($)
 25k ┤                                          ●
     │                                     ●
 20k ┤                                ●
     │                           ●
 15k ┤                      ●
     │
 10k ┤                 ●
     │            ●
  5k ┤       ●
     │  ●
   0 ┼───────────────────────────────────────────
     Q1'23 Q2'23 Q3'23 Q4'23 Q1'24 Q2'24 Q3'24 Q4'24
```

| Metric | Giá trị |
|---|---|
| Tổng 2 năm | +$25,171 |
| Trung bình/năm | ~$12,500 |
| Quý dương liên tiếp | 8/8 (100%) |
| Sharpe | 3.5 - 4.2 |
| Win Rate | 65-70% |
| Trades/tháng | ~750-850 |
| Max Drawdown | -$200 đến -$300 |

---

## 10. CHIẾN LƯỢC TỐI ƯU CUỐI CÙNG

### 10.1 Tham số entry

```
┌─────────────────────────────────────────────┐
│  BTC ADX(7) >= 50?          ← Bước 1       │
│  ├─ KHÔNG → KHÔNG VÀO LỆNH (chờ)           │
│  └─ CÓ → Tiếp tục kiểm tra                 │
│                                              │
│  Volume Spike >= 2x?        ← Bước 2       │
│  ├─ KHÔNG → Bỏ qua coin này                │
│  └─ CÓ → Tiếp                               │
│                                              │
│  |Price Movement| >= 0.8%   ← Bước 3       │
│  VÀ |PM| <= 20%?                            │
│  ├─ KHÔNG → Bỏ qua                         │
│  └─ CÓ → Tiếp                               │
│                                              │
│  Coin cùng hướng BTC?      ← Bước 4        │
│  (PM > 0 + BTC tăng) HOẶC                   │
│  (PM < 0 + BTC giảm)                        │
│  ├─ KHÔNG → Bỏ qua                         │
│  └─ CÓ → 🔔 SIGNAL!                        │
│                                              │
│  Hướng vào lệnh = hướng BTC                 │
│  BTC tăng → LONG | BTC giảm → SHORT         │
└─────────────────────────────────────────────┘
```

### 10.2 Giải thích từng điều kiện

**ADX(7) >= 50 trên BTC:**
- ADX đo sức mạnh xu hướng (0-100), không phân biệt hướng
- Period 7 = nhanh, phản ứng sớm
- >= 50 = xu hướng mạnh → altcoin có xu hướng "bám theo" BTC rõ ràng hơn
- Tại sao 50 chứ không phải 55 hay 64?
  - ADX 64 (bot gốc): BTC chỉ ở trạng thái này 3.4% thời gian → quá ít cơ hội
  - ADX 55: +$12,704/năm nhưng ít lệnh hơn
  - ADX 50: +$16,086/năm, nhiều lệnh hơn 38%, Sharpe vẫn 4.1

**Volume Spike >= 2x:**
- So sánh volume nến hiện tại với median 4 nến trước (M15)
- >= 2x = volume gấp đôi bình thường → có "sự kiện" đang xảy ra
- Tại sao 2x chứ không phải 4x?
  - 4x (bot gốc): 362 trades/3 tháng = quá ít
  - 2x: 2,546 trades/3 tháng = đủ để thống kê có ý nghĩa, Sharpe vẫn > 4

**Price Movement 0.8% - 20%:**
- Giá đã di chuyển >= 0.8% so với median 4 nến trước
- Cap 20% để loại bỏ pump/dump cực đoan (không lặp lại được)
- Tại sao 0.8% chứ không phải 1.2%?
  - 1.2% (bot gốc): quá ít tín hiệu khi kết hợp với ADX >= 50
  - 0.8%: tăng lệnh ~70% mà WR vẫn 69.4%

**BTC Direction:**
- So sánh giá BTC đóng cửa nến hiện tại vs 2 nến trước (M15 = 30 phút)
- Nếu BTC tăng → chỉ LONG altcoin. BTC giảm → chỉ SHORT
- Tác động: +29% PnL, +24% Sharpe so với không dùng

### 10.3 Quản lý exit — Trail+BE

```
Vào lệnh LONG @ $100.00
│
├── SL ban đầu: $99.50 (-0.5%)
│
├── Giá lên $100.30 (+0.3%) → BREAKEVEN kích hoạt
│   └── SL chuyển lên $100.00 (0%) → KHÔNG THỂ LỖ
│
├── Giá tiếp tục lên $101.00 (+1.0%)
│   └── Trailing SL = $101.00 - 0.5% = $100.50
│       (SL luôn bám theo giá, cách 0.5%)
│
├── Giá lên $102.00 (+2.0%)
│   └── Trailing SL = $101.50
│
├── Giá lên $103.00 (+3.0%) → TP CAP → CHỐT LỜI
│
│   HOẶC
│
├── Giá quay đầu xuống $101.50 → TRAILING STOP
│   └── Lời +1.5% thay vì chờ lỗ
│
│   HOẶC
│
└── Giá giảm ngay → SL tại -0.5% (trước BE)
    └── Lỗ nhỏ, bảo vệ vốn
```

### 10.4 Lookback theo timeframe

| Timeframe | Vol LB | Price LB | BTC Dir LB | Max Hold |
|---|---|---|---|---|
| **1m** | **20 nến** | **20 nến** | **10 nến** | **600 nến (10h)** |
| 3m | 15 | 15 | 8 | 200 (10h) |
| 5m | 12 | 12 | 6 | 120 (10h) |
| **15m** | **4 nến** | **4 nến** | **2 nến** | **40 nến (10h)** |
| 30m | 4 | 4 | 2 | 20 (10h) |
| 1h | 4 | 4 | 1 | 10 (10h) |
| 4h | 4 | 4 | 1 | 3 (12h) |

