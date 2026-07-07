# Dip & Ambiguity Check — Dual-File 1-Tick Backtester

DIP BUY overnight strategy on ES, validated with a dual-file architecture:
primary retrace bars + 1-tick data used only to resolve intrabar entry/PT/SL
sequencing on the entry bar. From the bar after entry onward, the primary
chart's High/Low drive the exits.

## IMPORTANT FIX in this version (2026-07-07)

The 1-tick window was off by one bar. Sierra Chart exports these bars
timestamped at the bar's **OPEN**, not its close. The original code read the
tick window as `(timestamp[i-1], timestamp[i]]`, which is the **previous**
bar's ticks. Corrected to `(timestamp[i], timestamp[i+1]]`.

Verification: for the correct window, each bar's High/Low must equal the
max/min of the ticks inside it. Old window matched 5/55 bars; corrected
window matches 55/55.

Impact: the old window manufactured fake wins on trending sessions (it "saw"
the profit target getting touched on the higher prior bar). After the fix the
inflated ~90%+ win / large-profit results collapse to their true values.

## Configuration (top of the script)

- `TICK`               tick size (ES = 0.25)
- `POINT_VALUE`        $ per 1.00 point (MES = 5.0, full ES = 50.0)
- `COMMISSION_PER_UNIT` per-side commission
- `START_TIME_SEC` / `END_TIME_SEC`  session window (default 17:00 -> 07:00)
- `ATR_PERIOD`        Wilder ATR period (20)
- `SL_COEF`           stop loss = entry - SL_COEF * ATR
- `PT_COEF`           profit target = entry + PT_COEF * ATR
- `INPUT_BAR_FILE`    primary bar export path
- `INPUT_1T_FILE`     1-tick export path (must cover the same window)
- `OUTPUT_DIR`        where the CSV / equity PNG are written

Load enough history that the ATR (period 20) warms up before the trades you
care about — a couple hundred bars is not enough for stable ATR.

## Payoff note (why the default params lose)

Profitability depends on the PT:SL geometry, not the raw win rate. Breakeven
win rate = SL / (SL + PT). With PT 0.40 / SL 0.80 that is 66.7%; with
PT 0.40 / SL 2.50 that is 86.2%. On the full ES history the realised win rate
lands just below each line, so the strategy is slightly negative before
commissions and clearly negative after. To make it work you need either a
larger PT relative to the SL, or a filter that lifts the win rate above the
breakeven line (regime / time-of-day / volatility).

## Run

```
python3 dip_ambiguity_check.py
```

Outputs: `path_verified_breakout_backtest_trades.csv`,
`path_verified_breakout_backtest_equity.png`, and a summary printed to stdout.
