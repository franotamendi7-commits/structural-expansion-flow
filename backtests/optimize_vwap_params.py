"""
Optuna optimization of VWAP breakout params per pair.
Uses 15m historical data from backtest_cache/.
Output: vwap_optimal_params.json
"""
import math, json, warnings
from pathlib import Path

import numpy as np
import pandas as pd
import optuna

warnings.filterwarnings("ignore")

CACHE_DIR = Path(__file__).parent / "backtest_cache"
OUT_PATH = Path(__file__).parent / "vwap_optimal_params.json"

PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]

def atr_np(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    out = np.full(len(tr), np.nan)
    out[n-1] = tr[:n].mean()
    for i in range(n, len(tr)):
        out[i] = (out[i-1] * (n-1) + tr[i]) / n
    return out

def rolling_vwap_np(close, high, low, volume, n: int) -> np.ndarray:
    tp = (high + low + close) / 3.0
    vp = tp * volume
    cum_vp = np.concatenate([[np.nan], np.cumsum(vp[1:])])
    cum_v = np.concatenate([[np.nan], np.cumsum(volume[1:])])
    out = np.full(len(close), np.nan)
    for i in range(n, len(close)):
        out[i] = (cum_vp[i] - cum_vp[i-n]) / (cum_v[i] - cum_v[i-n]) if (cum_v[i] - cum_v[i-n]) > 0 else np.nan
    return out

def simulate_vwap_breakout(df: pd.DataFrame, vwap_n: int, dev_thr: float,
                           sl_mult: float, tp_mult: float, atr_n: int = 14,
                           slippage: float = 0.0005, cost: float = 0.0004) -> dict:
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    volume = df["volume"].values
    timestamps = df.index.values

    vwap = rolling_vwap_np(close, high, low, volume, vwap_n)
    dev = (close - vwap) / vwap * 100.0

    a15_vals = atr_np(high, low, close, atr_n)

    trades = []
    for i in range(vwap_n + atr_n, len(close)):
        if math.isnan(dev[i]) or math.isnan(dev[i-1]):
            continue
        if math.isnan(a15_vals[i]) or a15_vals[i] <= 0:
            continue

        thr = dev_thr
        entry = close[i]
        a15 = a15_vals[i]

        # LONG: dev_prev >= -thr AND dev < -thr
        if dev[i-1] >= -thr and dev[i] < -thr:
            direction = 1
            stop = entry - sl_mult * a15
            tp = entry + tp_mult * a15
        # SHORT: dev_prev <= +thr AND dev > +thr
        elif dev[i-1] <= thr and dev[i] > thr:
            direction = -1
            stop = entry + sl_mult * a15
            tp = entry - tp_mult * a15
        else:
            continue

        exit_price = None
        exit_reason = None
        for j in range(i+1, len(close)):
            if direction == 1:
                if close[j] <= stop:
                    exit_price = stop
                    exit_reason = "SL"
                    break
                elif close[j] >= tp:
                    exit_price = tp
                    exit_reason = "TP"
                    break
                elif low[j] <= stop:
                    exit_price = stop
                    exit_reason = "SL"
                    break
            else:
                if close[j] >= stop:
                    exit_price = stop
                    exit_reason = "SL"
                    break
                elif close[j] <= tp:
                    exit_price = tp
                    exit_reason = "TP"
                    break
                elif high[j] >= stop:
                    exit_price = stop
                    exit_reason = "SL"
                    break

        if exit_price is None:
            continue

        if direction == 1:
            pnl_pct = (exit_price / entry - 1) * direction
        else:
            pnl_pct = (1 - exit_price / entry) * direction

        pnl_pct -= slippage + cost

        trades.append({
            "entry_time": int(timestamps[i]),
            "exit_time": int(timestamps[j]) if exit_reason else int(timestamps[i+1]),
            "dir": direction,
            "entry": entry,
            "exit": exit_price,
            "pnl_pct": pnl_pct,
            "reason": exit_reason,
        })

    if len(trades) < 5:
        return dict(n_trades=0, sharpe=-999, ret=0, wr=0, pf=0, trades=trades)

    returns = np.array([t["pnl_pct"] for t in trades])
    ret = float(returns.sum())
    wr = float((returns > 0).mean())
    sharpe = float(returns.mean() / returns.std() * math.sqrt(365*96)) if returns.std() > 0 else -999
    gross_win = returns[returns > 0].sum() if (returns > 0).any() else 0
    gross_loss = abs(returns[returns < 0].sum()) if (returns < 0).any() else 0.001
    pf = float(gross_win / gross_loss)

    return dict(n_trades=len(trades), sharpe=sharpe, ret=ret, wr=wr, pf=pf, trades=trades)

def load_data(pair: str) -> pd.DataFrame:
    path = CACHE_DIR / f"{pair}_15m.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing data: {path}")
    df = pd.read_csv(path)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df

def optimize_pair(pair: str, n_trials: int = 500):
    df = load_data(pair)
    print(f"\n{'='*60}")
    print(f"Optimizing {pair} | {len(df)} rows | {n_trials} trials")

    def objective(trial):
        vwap_n = trial.suggest_int("vwap_n", 5, 30)
        dev_thr = trial.suggest_float("dev_thr", 0.3, 2.5, log=True)
        sl_mult = trial.suggest_float("sl_mult", 1.0, 4.0, log=True)
        tp_mult = trial.suggest_float("tp_mult", 1.0, 5.0, log=True)

        split = int(len(df) * 0.7)
        df_train = df.iloc[:split]
        df_test = df.iloc[split:]

        train_r = simulate_vwap_breakout(
            df_train, vwap_n=vwap_n, dev_thr=dev_thr,
            sl_mult=sl_mult, tp_mult=tp_mult,
        )
        if train_r["n_trades"] < 30:
            return -999 + train_r["n_trades"] * 0.1

        test_r = simulate_vwap_breakout(
            df_test, vwap_n=vwap_n, dev_thr=dev_thr,
            sl_mult=sl_mult, tp_mult=tp_mult,
        )
        if test_r["n_trades"] < 15:
            return -999 + test_r["n_trades"] * 0.1 + train_r["sharpe"] * 0.01

        return test_r["sharpe"]

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    best = study.best_params
    split = int(len(df) * 0.7)
    train_r = simulate_vwap_breakout(df.iloc[:split], **best)
    test_r = simulate_vwap_breakout(df.iloc[split:], **best)
    best["pair"] = pair
    best["train_n"] = train_r["n_trades"]
    best["train_sharpe"] = round(train_r["sharpe"], 3)
    best["train_ret"] = round(train_r["ret"] * 100, 2)
    best["train_wr"] = round(train_r["wr"] * 100, 1)
    best["test_n"] = test_r["n_trades"]
    best["test_sharpe"] = round(test_r["sharpe"], 3)
    best["test_ret"] = round(test_r["ret"] * 100, 2)
    best["test_wr"] = round(test_r["wr"] * 100, 1)
    best["test_pf"] = round(test_r["pf"], 3)

    params_only = {k: best[k] for k in ("vwap_n", "dev_thr", "sl_mult", "tp_mult")}
    full_r = simulate_vwap_breakout(df, **params_only)
    best["n_trades"] = full_r["n_trades"]
    best["sharpe"] = round(full_r["sharpe"], 3)
    best["ret_pct"] = round(full_r["ret"] * 100, 2)
    best["wr"] = round(full_r["wr"] * 100, 1)
    best["pf"] = round(full_r["pf"], 3)

    print(f"\nBest for {pair}:")
    print(f"  Params: vwap_n={best['vwap_n']}, dev_thr={best['dev_thr']:.2f}, sl_mult={best['sl_mult']:.2f}, tp_mult={best['tp_mult']:.2f}")
    print(f"  TRAIN: {best['train_n']} trades | Sharpe {best['train_sharpe']} | Ret {best['train_ret']}% | WR {best['train_wr']}%")
    print(f"  TEST:  {best['test_n']} trades | Sharpe {best['test_sharpe']} | Ret {best['test_ret']}% | WR {best['test_wr']}% | PF {best['test_pf']}")

    return {"best_params": best, "study": study}

def main():
    results = {}
    for pair in PAIRS:
        try:
            r = optimize_pair(pair, n_trials=500)
            results[pair] = r["best_params"]
        except Exception as e:
            print(f"Error optimizing {pair}: {e}")
            results[pair] = {"error": str(e)}

    print(f"\n{'='*60}")
    print(f"OPTIMIZATION COMPLETE")
    print(f"{'='*60}")
    for pair, params in results.items():
        if "error" in params:
            print(f"  {pair}: ERROR - {params['error']}")
        else:
            print(f"  {pair}: vwap_n={params['vwap_n']}, dev_thr={params['dev_thr']:.2f}, sl_mult={params['sl_mult']:.2f}, tp_mult={params['tp_mult']:.2f} | Sharpe={params['sharpe']} Ret={params['ret_pct']}% WR={params['wr']}% PF={params['pf']}")

    with open(OUT_PATH, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {OUT_PATH}")

if __name__ == "__main__":
    main()
