"""Basic historical simulation on Exness-exported M15 CSV.
CSV columns: time,open,high,low,close,tick_volume. Signals use closed bars only.
This is a simplified M15 trend/momentum test, not the live multi-TF strategy.
"""
import argparse
import pandas as pd
from engine import indicators


def run(path: str, sl_atr: float, rr: float):
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = indicators(df)
    trades = []
    for i in range(220, len(df)-1):
        r = df.iloc[i]
        prev = df.iloc[i-1]
        side = None
        if r.close > r.ema50 > r.ema200 and r.macd > r.macd_signal and 45 <= r.rsi <= 68 and r.adx >= 18:
            side = "BUY"
        elif r.close < r.ema50 < r.ema200 and r.macd < r.macd_signal and 32 <= r.rsi <= 55 and r.adx >= 18:
            side = "SELL"
        if not side or pd.isna(r.atr) or r.atr <= 0:
            continue
        entry = float(df.iloc[i+1].open)  # next bar open avoids same-bar lookahead
        risk = float(r.atr) * sl_atr
        stop = entry-risk if side == "BUY" else entry+risk
        target = entry+risk*rr if side == "BUY" else entry-risk*rr
        outcome, exit_price = "OPEN", None
        for j in range(i+1, min(i+49, len(df))):
            bar = df.iloc[j]
            hit_sl = bar.low <= stop if side == "BUY" else bar.high >= stop
            hit_tp = bar.high >= target if side == "BUY" else bar.low <= target
            if hit_sl and hit_tp:
                outcome, exit_price = "LOSS", stop  # conservative when intrabar order unknown
                break
            if hit_sl:
                outcome, exit_price = "LOSS", stop; break
            if hit_tp:
                outcome, exit_price = "WIN", target; break
        if outcome == "OPEN":
            continue
        pnl_r = rr if outcome == "WIN" else -1.0
        trades.append({"time": r.time, "side": side, "entry": entry, "exit": exit_price, "result": outcome, "R": pnl_r})
    out = pd.DataFrame(trades)
    if out.empty:
        print("No trades found for these rules."); return
    wins = int((out.result == "WIN").sum())
    print(f"Trades: {len(out)} | Wins: {wins} | Losses: {len(out)-wins}")
    print(f"Win rate: {wins/len(out)*100:.2f}% | Net R: {out['R'].sum():.2f} | Avg R: {out['R'].mean():.3f}")
    out.to_csv("backtest_trades.csv", index=False)
    print("Saved backtest_trades.csv")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("csv")
    p.add_argument("--sl-atr", type=float, default=1.5)
    p.add_argument("--rr", type=float, default=1.5)
    a = p.parse_args(); run(a.csv, a.sl_atr, a.rr)
