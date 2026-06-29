# Wilder Paper Trader — Chiến lược chi tiết

> Nguồn: Wilder_Paper_Trader_ChiTiet.docx | Cập nhật: 25/05/2026

---

## 1. Ý tưởng cốt lõi

Hệ thống giao dịch tự động sử dụng **toàn bộ chỉ báo của J. Welles Wilder Jr.** (RSI, ADX, ATR, Parabolic SAR).

**Triết lý:** Thị trường luôn ở một trong hai trạng thái — xu hướng hoặc đi ngang. Mỗi trạng thái cần chiến lược riêng:

| Regime | ADX | Chiến lược |
|---|---|---|
| TRENDING | >= 35 | SAR Flip + DI Gap — bắt điểm đảo chiều trong xu hướng |
| TRANSITION | 25 – 35 | Không giao dịch — chờ xác nhận |
| RANGING | < 25 | RSI thoát vùng quá mua/bán — mua đáy bán đỉnh |

---

## 2. Cấu hình tham số

| Tham số | Giá trị | Giải thích |
|---|---|---|
| Timeframe | 1H | Khung thời gian chính |
| RSI Period | 14 | Chu kỳ Wilder chuẩn |
| ADX Period | 14 | Chu kỳ Wilder chuẩn |
| ATR Period | 14 | Chu kỳ Wilder chuẩn |
| SAR AF Init | 0.02 | Hệ số tăng tốc ban đầu |
| SAR AF Step | 0.02 | Bước tăng AF mỗi lần giá tạo đỉnh/đáy mới |
| SAR AF Max | 0.20 | Giới hạn tối đa AF |
| DI Gap tối thiểu | 5 điểm | Xác nhận xu hướng đủ mạnh |
| SL | 2 × ATR | Stop Loss ban đầu |
| TP | 6 × ATR (RR 3:1) | Take Profit |
| Trailing Stop | 0.5 × ATR | Chốt lời nhanh theo giá |
| Phí | 0.07% | Tương đương phí Maker Binance Futures VIP0 |
| Vốn ban đầu | $10,000 | — |
| Số coin | Top 120 theo volume 24h | — |

---

## 3. Điều kiện vào lệnh

### Bước 1 — Xác định Regime

```
ADX >= 35            → TRENDING  → dùng SAR Flip
25 <= ADX < 35       → TRANSITION → KHÔNG giao dịch
ADX < 25             → RANGING   → dùng RSI
```

### Bước 2A — Tín hiệu TRENDING (SAR Flip)

**LONG:**
```
1. SAR Flip lên: trend nến trước = DOWN (-1), nến hiện tại = UP (+1)
2. +DI > -DI  (phe mua mạnh hơn)
3. (+DI - -DI) >= 5  (khoảng cách đủ lớn)
```

**SHORT:**
```
1. SAR Flip xuống: trend nến trước = UP (+1), nến hiện tại = DOWN (-1)
2. -DI > +DI  (phe bán mạnh hơn)
3. (-DI - +DI) >= 5
```

### Bước 2B — Tín hiệu RANGING (RSI)

**LONG:**
```
1. RSI nến trước < 32  (quá bán)
2. RSI nến hiện tại >= 32  (thoát vùng quá bán)
3. (+DI - -DI) >= 5  (xác nhận lực mua)
```

**SHORT:**
```
1. RSI nến trước > 68  (quá mua)
2. RSI nến hiện tại <= 68  (thoát vùng quá mua)
3. (-DI - +DI) >= 5  (xác nhận lực bán)
```

> RSI ngưỡng 32/68 thay vì 30/70 truyền thống — hẹp hơn để tăng số tín hiệu.

---

## 4. Quản lý lệnh

### 4.1 Stop Loss & Take Profit (ATR-based)

```
LONG:
  SL = entry - 2 × ATR
  TP = entry + 6 × ATR  (RR 3:1)

SHORT:
  SL = entry + 2 × ATR
  TP = entry - 6 × ATR  (RR 3:1)
```

Với RR 3:1, chỉ cần thắng **25% lệnh** là hòa vốn (không tính phí).  
Thực tế win rate 43.8% → lợi thế rõ ràng: EV = 0.438 × $4.58 − 0.562 × $3.01 = **+$0.32/lệnh**.

### 4.2 Trailing Stop

Dịch SL mỗi nến theo hướng có lợi, khoảng cách 0.5 × ATR:

```
LONG — mỗi nến mới:
  new_sl = close - 0.5 × ATR
  Nếu new_sl > sl_hiện_tại → cập nhật (SL chỉ đi lên)

SHORT — mỗi nến mới:
  new_sl = close + 0.5 × ATR
  Nếu new_sl < sl_hiện_tại → cập nhật (SL chỉ đi xuống)

Kiểm tra trigger: so với low_t (LONG) hoặc high_t (SHORT)
```

Trailing 0.5 × ATR chặt hơn SL ban đầu (2 × ATR) → đa số lệnh đóng sau 1-2 nến với lợi nhuận nhỏ. Đây là lý do win rate thấp nhưng profit factor vẫn 1.52.

### 4.3 Thứ tự kiểm tra mỗi nến

```
1. Stop Loss     → ưu tiên bảo vệ vốn
2. Take Profit   → chốt lời mục tiêu
3. Trailing Stop → điều chỉnh SL
```

---

## 5. Step Sizing — Kích thước lệnh

Kích thước tăng theo lợi nhuận tích lũy, **tính trên vốn ban đầu $10,000** (không phải vốn hiện tại) để tránh gộp lãi khi đang lỗ:

```
profit = balance - $10,000
steps  = floor(profit / $500)   (nếu profit <= 0 → steps = 0)
pct    = 3% + steps × 0.5%
size   = $10,000 × pct
```

| Balance | Profit | Steps | Size/lệnh |
|---|---|---|---|
| $10,000 | $0 | 0 | $300 (3.0%) |
| $10,500 | $500 | 1 | $350 (3.5%) |
| $11,000 | $1,000 | 2 | $400 (4.0%) |
| $11,500 | $1,500 | 3 | $450 (4.5%) |
| $12,000 | $2,000 | 4 | $500 (5.0%) |
| $9,500 | -$500 | 0 | $300 (3.0%) — giữ nguyên khi lỗ |

---

## 6. Kết quả Backtest

**720 ngày (2 năm), 1H, 120 coin Binance Futures (54 coin đủ dữ liệu)**

| Chỉ số | Giá trị |
|---|---|
| Vốn ban đầu | $10,000 |
| Vốn cuối | $12,601 |
| Lợi nhuận ròng | **+$2,601 (+26.01%)** |
| Tổng giao dịch | 3,806 |
| Win Rate | **43.8%** |
| Profit Factor | **1.52** |
| Max Drawdown | **1.50%** |
| Coin có lãi | 48/54 (88.9%) |
| Avg win/lệnh | ~$4.58 |
| Avg loss/lệnh | ~$3.01 |
| Avg phí/lệnh | ~$0.21 – $0.35 |

---

## 7. Ví dụ tính toán (ETHUSDT)

**Dữ liệu đầu vào:**

| Chỉ báo | Giá trị |
|---|---|
| Close | $3,500 |
| ADX | 38.7 → TRENDING |
| +DI / -DI | 28.4 / 15.1 |
| ATR | $85 |
| SAR trend | +1 (vừa flip lên từ -1) |
| Balance | $11,200 |

**Kiểm tra tín hiệu:**
```
ADX 38.7 >= 35 → TRENDING ✓
SAR Flip lên ✓
+DI (28.4) > -DI (15.1) ✓
DI Gap = 13.3 >= 5 ✓
→ LONG ETHUSDT @ $3,500
```

**Tính lệnh:**
```
profit = $11,200 - $10,000 = $1,200
steps  = floor(1,200 / 500) = 2
size   = $10,000 × (3% + 2×0.5%) = $400

SL = $3,500 - 2×$85 = $3,330
TP = $3,500 + 6×$85 = $4,010
```

**Diễn biến Trailing Stop:**
```
Nến +1: close=$3,580 → new_sl = $3,580 - 0.5×$83 = $3,538.50  (cập nhật)
Nến +2: close=$3,560, low=$3,530 → $3,530 < $3,538.50 → ĐÓNG tại SL

PnL thô = ($3,538.50 - $3,500) / $3,500 × $400 = +$4.40
Phí     = $400 × 0.07% = $0.28
PnL ròng = +$4.12
```

---

## 8. Công thức Wilder's Smoothing (tham khảo)

Tất cả chỉ báo dùng chung phương pháp làm mượt với alpha = 1/N (N=14):

```
Smoothed_t = Smoothed_{t-1} × (N-1)/N + Value_t × 1/N
           = Smoothed_{t-1} × 13/14   + Value_t / 14
```

Tương đương EMA nhưng mượt hơn SMA, đủ nhạy để bắt thay đổi thị trường.

---

## 9. Điểm khác biệt so với hệ thống truyền thống

| Điểm | Wilder Paper Trader | Truyền thống |
|---|---|---|
| ADX trending threshold | >= 35 | >= 25 |
| RSI overbought/oversold | 68 / 32 | 70 / 30 |
| Entry signal TRENDING | SAR Flip + DI Gap | Thường dùng MA cross |
| Exit | ATR trailing 0.5x | Fixed TP/SL |
| Sizing | Step theo profit | Fixed % |
| Win Rate | 43.8% | Thường tối ưu WR > 50% |
| RR | 3:1 | Thường 1:1 – 2:1 |
