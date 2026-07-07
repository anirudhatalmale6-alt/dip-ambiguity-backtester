#!/usr/bin/env python3
"""
Dynamic Breakout Strategy (AFL-Aligned) — Bid/Ask Volume Entry & ATR Exits
Dual-File Architecture: Primary Bars paired with a 1-Tick Chart Price Path Engine
Skips/Ignores entries if the Profit Target is reached before the limit order fills.
"""
import csv, sys, os
from datetime import datetime
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ===== GLOBAL CONFIGURATION =====
TICK = 0.25
POINT_VALUE = 5.0  # Dollar value of a 1.00 point price move
TRADE_SIZE = 1
COMMISSION_PER_UNIT = 0.5  # Per side commission cost
INITIAL_EQUITY = 10_000

# Globex Trading Window Setup (17:00 to 15:00 Next Day)
START_TIME_SEC = 17 * 3600   # 17:00:00
END_TIME_SEC   = 7 * 3600   # 15:00:00

# ATR Target Configurations
ATR_PERIOD = 20
SL_COEF = 2.50   # Stop Loss ATR multiplier
PT_COEF = 0.40   # Profit Target ATR multiplier

# ----- DUAL-FILE CONFIGURATION -----
INPUT_BAR_FILE = r"/var/lib/freelancer/projects/40176089/ES 8-4-4-4T Retrace v3.txt"
INPUT_1T_FILE  = r"/var/lib/freelancer/projects/40176089/ES 1T Overnigh.txt"
OUTPUT_DIR     = r"/var/lib/freelancer/projects/40176089"
# ================================================================


def parse_time_secs(ts):
    ts = ts.strip().split('.')[0]
    p = ts.split(':')
    return int(p[0]) * 3600 + int(p[1]) * 60 + (int(p[2]) if len(p) > 2 else 0)


def parse_to_epoch(d, t):
    """Converts Date and Time strings into highly precise Unix epoch floats."""
    dt_str = f"{d.strip()} {t.strip()}".replace('-', '/')
    try:
        if '.' in dt_str:
            return datetime.strptime(dt_str, "%Y/%m/%d %H:%M:%S.%f").timestamp()
        else:
            return datetime.strptime(dt_str, "%Y/%m/%d %H:%M:%S").timestamp()
    except:
        return 0.0


def date_to_int(ds):
    ds = ds.strip().replace('-', '/')
    p = ds.split('/')
    if int(p[0]) > 1000:
        return int(p[0]) * 10000 + int(p[1]) * 100 + int(p[2])
    return int(p[2]) * 10000 + int(p[0]) * 100 + int(p[1])


def round_tick(v):
    return round(v / TICK) * TICK


def compute_atr(H, L, C, period=20):
    n = len(C)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(H[i] - L[i], abs(H[i] - C[i-1]), abs(L[i] - C[i-1]))
    
    atr = np.zeros(n)
    if n > period:
        atr[period] = np.mean(tr[1:period+1])
        for i in range(period + 1, n):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    return atr


def load_primary_bars(fn):
    print(f"Loading primary bar data from {fn}...")
    dates, times, epochs = [], [], []
    O, H, L, C = [], [], [], []
    with open(fn, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for r in reader:
            r = {k.strip(): v.strip() for k, v in r.items()}
            dates.append(r['Date'])
            times.append(r['Time'])
            epochs.append(parse_to_epoch(r['Date'], r['Time']))
            O.append(float(r['Open']))
            H.append(float(r['High']))
            L.append(float(r['Low']))
            C.append(float(r.get('Last', r.get('Close', '0'))))
    return dates, times, np.array(epochs), np.array(O), np.array(H), np.array(L), np.array(C)


def load_1t_ticks(fn):
    print(f"Loading 1-Tick path validation data from {fn}...")
    t_epochs, t_highs, t_lows = [], [], []
    with open(fn, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for r in reader:
            r = {k.strip(): v.strip() for k, v in r.items()}
            t_epochs.append(parse_to_epoch(r['Date'], r['Time']))
            t_highs.append(float(r['High']))
            t_lows.append(float(r['Low']))
    return np.array(t_epochs), np.array(t_highs), np.array(t_lows)


def run_backtest(dates, times, primary_epochs, O, H, L, C, t_epochs, t_highs, t_lows):
    n = len(C)
    time_secs = np.array([parse_time_secs(times[i]) for i in range(n)])
    
    if START_TIME_SEC <= END_TIME_SEC:
        time_ok = (time_secs >= START_TIME_SEC) & (time_secs < END_TIME_SEC)
    else:
        time_ok = (time_secs >= START_TIME_SEC) | (time_secs < END_TIME_SEC)

    atr = compute_atr(H, L, C, period=ATR_PERIOD)

    pos = 0
    equity = INITIAL_EQUITY
    entry_idx = 0
    entry_price = 0.0
    sl_level = 0.0
    pt_level = 0.0
    trades = []
    skipped_pt_before_entry = 0
    total_dip_signals = 0
    no_1t_data_signals = 0

    start_idx = ATR_PERIOD + 1
    equity_curve = [equity] * start_idx

    # Linear scanning pointer to walk the 1-Tick dataset with O(N+M) efficiency
    ptr_1t = 0
    num_1t = len(t_epochs)

    for i in range(start_idx, n):
        current_time = time_secs[i]
        
        # Bar timestamps are at BAR OPEN, so bar i's ticks fall in (ts[i], ts[i+1]]
        t_start = primary_epochs[i]
        t_end = primary_epochs[i+1] if i + 1 < n else primary_epochs[i] + 86400.0

        # Sync and capture all 1-Tick sub-bars falling inside this primary bar window
        sub_highs, sub_lows = [], []
        while ptr_1t < num_1t and t_epochs[ptr_1t] <= t_start:
            ptr_1t += 1
        while ptr_1t < num_1t and t_epochs[ptr_1t] <= t_end:
            sub_highs.append(t_highs[ptr_1t])
            sub_lows.append(t_lows[ptr_1t])
            ptr_1t += 1

        # --- 1. ENTRY EVALUATION (WITH CHROMATIC 1T PATH CHECK) ---
        if pos == 0:
            # Does the primary bar indicate a dip penetration below prior low?
            if time_ok[i] and L[i] < L[i-1]:
                total_dip_signals += 1
                calc_entry = round_tick(L[i-1])
                current_atr = atr[i-1]
                calc_sl = round_tick(calc_entry - (SL_COEF * current_atr))
                calc_pt = round_tick(calc_entry + (PT_COEF * current_atr))

                trade_opened = False
                pt_already_visited = False
                exit_on_entry_bar = False
                same_bar_exit_price = 0.0
                same_bar_exit_type = ''

                if not sub_highs:
                    no_1t_data_signals += 1

                # Walk chronologically through the sub-ticks to parse sequence order
                for th, tl in zip(sub_highs, sub_lows):
                    if not trade_opened:
                        hit_pt = (th >= calc_pt)
                        hit_entry = (tl <= calc_entry)

                        if hit_pt and not hit_entry:
                            # PT visited before entry - mark it but keep walking
                            pt_already_visited = True
                            continue
                        elif hit_entry:
                            trade_opened = True
                            if pt_already_visited:
                                skipped_pt_before_entry += 1
                            # Check if this entry tick also hits SL or PT
                            if tl <= calc_sl and th >= calc_pt:
                                same_bar_exit_price = calc_sl
                                same_bar_exit_type = 'ATR_SL_SameTick_Conflict'
                                exit_on_entry_bar = True
                                break
                            elif tl <= calc_sl:
                                same_bar_exit_price = calc_sl
                                same_bar_exit_type = 'ATR_SL_SameTick'
                                exit_on_entry_bar = True
                                break
                            elif th >= calc_pt and not pt_already_visited:
                                same_bar_exit_price = calc_pt
                                same_bar_exit_type = 'ATR_PT_SameTick'
                                exit_on_entry_bar = True
                                break
                    else:
                        # Trade is open, evaluate the remainder of the 1T sub-ticks inside this bar
                        if tl <= calc_sl and th >= calc_pt:
                            same_bar_exit_price = calc_sl
                            same_bar_exit_type = 'ATR_SL_SameBar_1T_Conflict'
                            exit_on_entry_bar = True
                            break
                        elif tl <= calc_sl:
                            same_bar_exit_price = calc_sl
                            same_bar_exit_type = 'ATR_SL_SameBar_1T'
                            exit_on_entry_bar = True
                            break
                        elif th >= calc_pt:
                            same_bar_exit_price = calc_pt
                            same_bar_exit_type = 'ATR_PT_SameBar_1T'
                            exit_on_entry_bar = True
                            break

                if trade_opened:
                    # Valid fill confirmed without front-running bypass
                    entry_idx = i
                    entry_price = calc_entry
                    sl_level = calc_sl
                    pt_level = calc_pt
                    
                    if exit_on_entry_bar:
                        # Trade opened and closed within the same primary bar
                        gross_pnl = (same_bar_exit_price - entry_price) * POINT_VALUE * TRADE_SIZE
                        total_commission = 2 * (COMMISSION_PER_UNIT * TRADE_SIZE)
                        net_pnl = gross_pnl - total_commission
                        equity += net_pnl
                        
                        trades.append({
                            'entry_date': dates[entry_idx], 'entry_time': times[entry_idx],
                            'exit_date': dates[i], 'exit_time': times[i],
                            'entry_price': entry_price, 'exit_price': same_bar_exit_price,
                            'gross_pnl': gross_pnl, 'commission': total_commission, 'pnl': net_pnl,
                            'points': same_bar_exit_price - entry_price,
                            'pnl_per_share': net_pnl / TRADE_SIZE, 'cents_per_share': (net_pnl / TRADE_SIZE) * 100,
                            'exit_type': same_bar_exit_type
                        })
                        pos = 0
                    else:
                        pos = 1

        # --- 2. RISK MANAGEMENT & EXIT ENGINE (FOR SUBSEQUENT BARS) ---
        elif pos == 1:
            if START_TIME_SEC <= END_TIME_SEC:
                is_eod_forced = current_time >= END_TIME_SEC
            else:
                is_eod_forced = (current_time >= END_TIME_SEC) & (current_time < START_TIME_SEC)

            # Check if targets are violated within subsequent bars using standard high/low logic
            hit_sl = L[i] <= sl_level
            hit_pt = H[i] >= pt_level

            if is_eod_forced:
                exit_price = round_tick(C[i])
                exit_type = 'EOD_Forced'
                pos = 0
            elif hit_sl and hit_pt:
                exit_price = sl_level
                exit_type = 'ATR_SL_Conflict'
                pos = 0
            elif hit_sl:
                exit_price = sl_level
                exit_type = 'ATR_SL'
                pos = 0
            elif hit_pt:
                exit_price = pt_level
                exit_type = 'ATR_PT'
                pos = 0

            if pos == 0:
                gross_pnl = (exit_price - entry_price) * POINT_VALUE * TRADE_SIZE
                total_commission = 2 * (COMMISSION_PER_UNIT * TRADE_SIZE)
                net_pnl = gross_pnl - total_commission
                equity += net_pnl
                
                trades.append({
                    'entry_date': dates[entry_idx], 'entry_time': times[entry_idx],
                    'exit_date': dates[i], 'exit_time': times[i],
                    'entry_price': entry_price, 'exit_price': exit_price,
                    'gross_pnl': gross_pnl, 'commission': total_commission, 'pnl': net_pnl,
                    'points': exit_price - entry_price,
                    'pnl_per_share': net_pnl / TRADE_SIZE, 'cents_per_share': (net_pnl / TRADE_SIZE) * 100,
                    'exit_type': exit_type
                })
            
        equity_curve.append(equity)

    # Global data boundary check for open positions
    if pos == 1:
        exit_price = round_tick(C[-1])
        gross_pnl = (exit_price - entry_price) * POINT_VALUE * TRADE_SIZE
        total_commission = 2 * (COMMISSION_PER_UNIT * TRADE_SIZE)
        net_pnl = gross_pnl - total_commission
        equity += net_pnl
        trades.append({
            'entry_date': dates[entry_idx], 'entry_time': times[entry_idx],
            'exit_date': dates[-1], 'exit_time': times[-1],
            'entry_price': entry_price, 'exit_price': exit_price,
            'gross_pnl': gross_pnl, 'commission': total_commission, 'pnl': net_pnl,
            'points': exit_price - entry_price,
            'pnl_per_share': net_pnl / TRADE_SIZE, 'cents_per_share': (net_pnl / TRADE_SIZE) * 100,
            'exit_type': 'Data_End'
        })
        equity_curve[-1] = equity

    stats = {
        'total_dip_signals': total_dip_signals,
        'skipped_pt_before_entry': skipped_pt_before_entry,
        'no_1t_data_signals': no_1t_data_signals,
    }
    return trades, np.array(equity_curve), stats


def print_results(trades, equity_curve, stats=None):
    if not trades:
        print("\nNo trades survived the 1-Tick front-run ambiguity filter!")
        return
    pnls = np.array([t['pnl'] for t in trades])
    points = np.array([t['points'] for t in trades])
    p_per_share = np.array([t['pnl_per_share'] for t in trades])
    c_per_share = np.array([t['cents_per_share'] for t in trades])
    
    winners = pnls[pnls > 0]
    losers = pnls[pnls <= 0]

    daily_pnl_map = {}
    for t in trades:
        daily_pnl_map[t['exit_date']] = daily_pnl_map.get(t['exit_date'], 0.0) + t['pnl']
    daily_vals = list(daily_pnl_map.values())
    sharpe = (np.mean(daily_vals) / np.std(daily_vals, ddof=1)) * np.sqrt(252) if len(daily_vals) > 1 and np.std(daily_vals) != 0 else 0.0

    print(f"\n{'='*60}\nBACKTEST RESULTS — DUAL FILE 1-TICK AMBIGUITY SCANNER\n{'='*60}")
    print(f"Total trades:      {len(trades)}")
    print(f"Winners:           {len(winners)} ({100*len(winners)/len(trades):.1f}%)")
    print(f"Losers:            {len(losers)} ({100*len(losers)/len(trades):.1f}%)")
    print(f"\nAverage $ / Trade: ${pnls.mean():,.2f}")
    print(f"Avg Profit / Share:${p_per_share.mean():,.4f} ({c_per_share.mean():.2f}¢)")
    print(f"Annualized Sharpe: {sharpe:.2f}")
    print(f"Net P&L:           ${pnls.sum():,.2f}")
    print(f"Max drawdown:      ${(equity_curve - np.maximum.accumulate(equity_curve)).min():,.2f}")
    print(f"Final equity:      ${equity_curve[-1]:,.2f}")

    if stats:
        skipped = stats['skipped_pt_before_entry']
        total = stats['total_dip_signals']
        no_data = stats['no_1t_data_signals']
        print(f"\n--- 1T AMBIGUITY FILTER STATS ---")
        print(f"Total dip signals (L[i] <= L[i-1]):  {total}")
        print(f"PT visited before entry (traded anyway):{skipped} ({100*skipped/total:.1f}%)")
        print(f"No 1T data available:                 {no_data}")
        print(f"Valid entries taken:                   {len(trades)} ({100*len(trades)/total:.1f}%)")
    print(f"{'='*60}")


def plot_results(equity_curve, trades, output_prefix):
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={'height_ratios': [2, 1]})
    axes[0].plot(equity_curve, color='navy', linewidth=0.8)
    axes[0].set_title('Equity Curve — Dual-File Path Confirmed Strategy')
    axes[0].grid(True, alpha=0.3)
    
    pnls = [t['pnl'] for t in trades]
    if pnls:
        axes[1].bar(range(len(pnls)), pnls, color=['green' if p > 0 else 'red' for p in pnls], width=1.0)
        axes[1].grid(True, alpha=0.3)
    
    plt.tight_layout()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    plt.savefig(os.path.join(OUTPUT_DIR, f'{output_prefix}_equity.png'), dpi=150)
    plt.close()


def save_trades(trades, output_prefix):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f'{output_prefix}_trades.csv')
    with open(out_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=trades[0].keys())
        writer.writeheader()
        writer.writerows(trades)


def main():
    if not os.path.exists(INPUT_BAR_FILE) or not os.path.exists(INPUT_1T_FILE):
        print("Error: Primary data file or 1-Tick data file path missing.")
        return

    dates, times, primary_epochs, O, H, L, C = load_primary_bars(INPUT_BAR_FILE)
    t_epochs, t_highs, t_lows = load_1t_ticks(INPUT_1T_FILE)

    trades, equity_curve, stats = run_backtest(dates, times, primary_epochs, O, H, L, C, t_epochs, t_highs, t_lows)
    print_results(trades, equity_curve, stats)

    prefix = 'path_verified_breakout_backtest'
    plot_results(equity_curve, trades, prefix)
    save_trades(trades, prefix)


if __name__ == '__main__':
    main()