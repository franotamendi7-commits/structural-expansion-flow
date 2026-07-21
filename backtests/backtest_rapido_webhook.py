"""
Backtest rápido de la estrategia VWAP breakout para los 5 bots.
Replica EXACTAMENTE la lógica de multi_bot.py sin ML filter ni guard.
Corre sobre los últimos 30 días de velas 15m de Binance.
"""
import sys, json, math, time
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone
from pathlib import Path

BINANCE_FAPI = "https://fapi.binance.com"
COMMISSION = 0.0005
SPREAD = 0.0002
INITIAL_EQUITY = 0.0

BOTS = {
    "BTC": {"symbol": "BTCUSDT", "risk_pct": 0.0075, "min_size": 0.001,
             "vwap_n": 15, "dev_thr": 0.75, "sl_mult": 1.5, "tp_mult": 1.0,
             "trend_filter": "regime",   # usa detección de régimen con bias
             "regime_params": {
                 "ALCISTA": {"vwap_n": 15, "dev_thr": 0.75, "sl_mult": 1.2, "tp_mult": 1.2, "bias": 1},
                 "BAJISTA": {"vwap_n": 20, "dev_thr": 1.0, "sl_mult": 2.0, "tp_mult": 1.5, "bias": -1},
                 "LATERAL": {"vwap_n": 15, "dev_thr": 0.75, "sl_mult": 1.0, "tp_mult": 1.5, "bias": 0},
             }},
    "ETH": {"symbol": "ETHUSDT", "risk_pct": 0.0085, "min_size": 0.001,
             "vwap_n": 10, "dev_thr": 1.0, "sl_mult": 1.5, "tp_mult": 1.5,
             "trend_filter": "vwap", "trend_period": 50},
    "SOL": {"symbol": "SOLUSDT", "risk_pct": 0.01, "min_size": 0.01,
             "vwap_n": 12, "dev_thr": 1.2, "sl_mult": 1.2, "tp_mult": 1.5,
             "trend_filter": "vwap", "trend_period": 200},
    "XRP": {"symbol": "XRPUSDT", "risk_pct": 0.01, "min_size": 0.1,
             "vwap_n": 10, "dev_thr": 1.0, "sl_mult": 1.2, "tp_mult": 1.5,
             "trend_filter": "vwap", "trend_period": 50},
    "BNB": {"symbol": "BNBUSDT", "risk_pct": 0.01, "min_size": 0.01,
             "vwap_n": 10, "dev_thr": 1.0, "sl_mult": 1.2, "tp_mult": 1.2,
             "trend_filter": "vwap", "trend_period": 50},
}


def fetch_klines(symbol, interval, limit=1500, start_time=None):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time:
        params["startTime"] = int(start_time.timestamp() * 1000)
    for attempt in range(3):
        try:
            r = requests.get(f"{BINANCE_FAPI}/fapi/v1/klines",
                             params=params, timeout=30)
            if r.status_code == 200:
                break
            time.sleep(1.5 ** attempt)
        except Exception:
            time.sleep(1.5 ** attempt)
    else:
        return pd.DataFrame()
    rows = r.json()
    cols = ["open_time","open","high","low","close","volume",
            "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"]
    df = pd.DataFrame(rows, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c])
    return df


def atr_wilder(df, n=14):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    out = np.full(len(tr), np.nan)
    out[n-1] = tr[:n].mean()
    for i in range(n, len(tr)):
        out[i] = (out[i-1] * (n-1) + tr[i]) / n
    return out


def rolling_vwap(df, n):
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vp = tp * df["volume"]
    return vp.rolling(n).sum() / df["volume"].rolling(n).sum()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def detect_regime(df1h):
    df = df1h.copy().sort_values("open_time").reset_index(drop=True)
    df["day"] = df["open_time"].dt.floor("D")
    df["h4"] = df["open_time"].dt.floor("4h")
    daily = df.groupby("day").agg(close_d=("close","last")).reset_index()
    daily["ema20"] = ema(daily["close_d"], 20)
    daily["ema20_y"] = daily["ema20"].shift(1)
    daily["ema20_y_prev"] = daily["ema20_y"].shift(1)
    daily["rising_y"] = (daily["ema20_y"] > daily["ema20_y_prev"]).astype(bool)
    daily["falling_y"] = (daily["ema20_y"] < daily["ema20_y_prev"]).astype(bool)
    if len(daily) < 2:
        return "LATERAL"
    last_day = daily["day"].iloc[-1]
    dr = daily[daily["day"] == last_day].iloc[0]
    ema20_y = dr["ema20_y"]
    rising = bool(dr["rising_y"]) if pd.notna(dr["rising_y"]) else False
    falling = bool(dr["falling_y"]) if pd.notna(dr["falling_y"]) else False
    h4 = df.groupby("h4").agg(close_4h=("close","last")).reset_index()
    if len(h4) < 2:
        return "LATERAL"
    close_4h = float(h4["close_4h"].iloc[-2])
    if pd.isna(ema20_y):
        raw = "LATERAL"
    elif close_4h > ema20_y and rising:
        raw = "ALCISTA"
    elif close_4h < ema20_y and falling:
        raw = "BAJISTA"
    else:
        raw = "LATERAL"
    h4_full = df.groupby("h4").agg(close_4h=("close","last")).reset_index()
    h4_full["day"] = h4_full["h4"].dt.floor("D")
    h4_full = h4_full.merge(daily[["day","ema20_y","rising_y","falling_y"]], on="day", how="left")
    h4_full["raw"] = "LATERAL"
    up_mask = (h4_full["close_4h"] > h4_full["ema20_y"]) & h4_full["rising_y"]
    dn_mask = (h4_full["close_4h"] < h4_full["ema20_y"]) & h4_full["falling_y"]
    h4_full.loc[up_mask, "raw"] = "ALCISTA"
    h4_full.loc[dn_mask, "raw"] = "BAJISTA"
    h4_full.loc[h4_full["ema20_y"].isna(), "raw"] = "LATERAL"
    raw_series = h4_full["raw"].tolist()
    final = []
    cur = "LATERAL"; pending=None; pc=0
    for v in raw_series:
        if v == cur:
            pending=None; pc=0; final.append(cur)
        else:
            if v == pending: pc += 1
            else: pending=v; pc=1
            if pc >= 2:
                cur = pending; pending=None; pc=0
            final.append(cur)
    return final[-1] if final else raw


def trend_bias_from_15m(bar, slow_period=50, name=""):
    """Determina bias direccional desde los propios datos 15m.
    Retorna 1 (alcista), -1 (bajista), 0 (neutral)."""
    if len(bar) < slow_period + 5:
        return 0
    vwap_slow = rolling_vwap(bar, slow_period)
    close = float(bar["close"].iloc[-1])
    vwap_val = float(vwap_slow.iloc[-1])
    vwap_prev = float(vwap_slow.iloc[-5]) if len(vwap_slow) >= 5 else vwap_val
    slope = vwap_val - vwap_prev
    if close > vwap_val and slope > 0:
        return 1
    elif close < vwap_val and slope < 0:
        return -1
    return 0


def vwap_breakout_signal(df15, vwap_n, dev_thr, sl_mult, tp_mult, bias=0):
    vwap = rolling_vwap(df15, vwap_n)
    dev = (df15["close"] - vwap) / vwap * 100.0
    dev_prev = dev.shift(1)
    if len(dev) < 2:
        return None
    last_dev = float(dev.iloc[-1])
    last_dev_prev = float(dev_prev.iloc[-1]) if pd.notna(dev_prev.iloc[-1]) else None
    if last_dev_prev is None:
        return None
    a15 = float(atr_wilder(df15, 14)[-1]) if len(df15) >= 14 else 0
    if not math.isfinite(a15) or a15 <= 0:
        return None
    close = float(df15["close"].iloc[-1])
    long_ok = (bias >= 0) and last_dev_prev >= -dev_thr and last_dev < -dev_thr
    short_ok = (bias <= 0) and last_dev_prev <= dev_thr and last_dev > dev_thr
    if long_ok:
        return {"dir": 1, "entry": close, "stop": close - sl_mult * a15, "tp": close + tp_mult * a15}
    if short_ok:
        return {"dir": -1, "entry": close, "stop": close + sl_mult * a15, "tp": close - tp_mult * a15}
    return None


def backtest_bot(name, config, df15, df5, df1h=None):
    symbol = config["symbol"]
    eq = 0.0
    trades = []
    pos = None
    n_bars = len(df15)

    MIN_RISK = 1.0

    def compute_size(eq, entry, stop):
        risk = config["risk_pct"] * eq
        if risk <= 0:
            risk = MIN_RISK
        pr = abs(entry - stop)
        return risk / pr if pr > 0 else 0

    for i in range(100, n_bars):  # skip warmup for VWAP + regime
        bar = df15.iloc[:i+1]
        close = float(bar["close"].iloc[-1])

        # Check exit if in position
        if pos is not None:
            exited = False
            if pos["dir"] == 1:
                if close <= pos["sl"]:
                    exit_p = pos["sl"] * (1 - SPREAD)
                    pnl = (exit_p - pos["entry"]) * pos["size"]
                    trades.append({"dir": 1, "entry": pos["entry"], "exit": exit_p,
                                  "pnl": pnl, "reason": "sl", "equity_after": pos["equity"] + pnl,
                                  "time": str(bar["open_time"].iloc[-1])})
                    pos = None; exited = True
                elif close >= pos["tp"]:
                    exit_p = pos["tp"] * (1 - SPREAD)
                    pnl = (exit_p - pos["entry"]) * pos["size"]
                    trades.append({"dir": 1, "entry": pos["entry"], "exit": exit_p,
                                  "pnl": pnl, "reason": "tp", "equity_after": pos["equity"] + pnl,
                                  "time": str(bar["open_time"].iloc[-1])})
                    pos = None; exited = True
            else:
                if close >= pos["sl"]:
                    exit_p = pos["sl"] * (1 + SPREAD)
                    pnl = (pos["entry"] - exit_p) * pos["size"]
                    trades.append({"dir": -1, "entry": pos["entry"], "exit": exit_p,
                                  "pnl": pnl, "reason": "sl", "equity_after": pos["equity"] + pnl,
                                  "time": str(bar["open_time"].iloc[-1])})
                    pos = None; exited = True
                elif close <= pos["tp"]:
                    exit_p = pos["tp"] * (1 + SPREAD)
                    pnl = (pos["entry"] - exit_p) * pos["size"]
                    trades.append({"dir": -1, "entry": pos["entry"], "exit": exit_p,
                                  "pnl": pnl, "reason": "tp", "equity_after": pos["equity"] + pnl,
                                  "time": str(bar["open_time"].iloc[-1])})
                    pos = None; exited = True
            if exited:
                continue

        if pos is not None:
            continue

        # Trend bias per bot
        tf = config.get("trend_filter", "vwap")
        if tf == "regime" and df1h is not None:
            regime = detect_regime(df1h)
            p = config["regime_params"][regime]
            sig = vwap_breakout_signal(bar, p["vwap_n"], p["dev_thr"], p["sl_mult"], p["tp_mult"],
                                        bias=p.get("bias", 0))
        elif tf == "vwap":
            tp = config.get("trend_period", 50)
            bias = trend_bias_from_15m(bar, slow_period=tp)
            sig = vwap_breakout_signal(bar, config["vwap_n"], config["dev_thr"],
                                        config["sl_mult"], config["tp_mult"], bias=bias)
        else:  # "none"
            sig = vwap_breakout_signal(bar, config["vwap_n"], config["dev_thr"],
                                        config["sl_mult"], config["tp_mult"], bias=0)
        if sig is None:
            continue

        # Entry with slippage
        size = compute_size(eq, sig["entry"], sig["stop"])
        if size < config["min_size"]:
            continue
        entry_fill = sig["entry"] * (1 + SPREAD) if sig["dir"] == 1 else sig["entry"] * (1 - SPREAD)
        entry_commission = entry_fill * size * COMMISSION
        pos = {"dir": sig["dir"], "entry": entry_fill, "size": size,
               "sl": sig["stop"], "tp": sig["tp"], "equity": eq}

    return trades


def fetch_klines_range(symbol, interval, days=90):
    """Fetch up to `days` of klines by paginating (Binance max 1500 per request)."""
    from datetime import timedelta
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    all_bars = []
    while start < end:
        df = fetch_klines(symbol, interval, 1500, start_time=start)
        if df.empty:
            break
        all_bars.append(df)
        last_time = df["open_time"].iloc[-1]
        start = last_time + timedelta(minutes=1)
    if not all_bars:
        return pd.DataFrame()
    full = pd.concat(all_bars, ignore_index=True)
    full = full.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    return full


def run():
    results = {}
    portfolio_trades = []
    for name, config in sorted(BOTS.items()):
        symbol = config["symbol"]
        print(f"Fetching {symbol} (3 months)...", end=" ")
        try:
            df15 = fetch_klines_range(symbol, "15m", days=90)
            df5 = fetch_klines(symbol, "5m", 500)
        except Exception as e:
            print(f"ERROR: {e}")
            continue
        if df15.empty or len(df15) < 100:
            print(f"SKIP: insufficient data ({len(df15)} bars)")
            continue
        print(f"{len(df15)} bars 15m")

        df1h = None
        if name == "BTC":
            df1h = fetch_klines_range(symbol, "1h", days=95)
            print(f"  1h data: {len(df1h) if not df1h.empty else 0} bars")

        trades = backtest_bot(name, config, df15, df5, df1h)
        if not trades:
            print(f"  No trades generated")
            results[name] = {"trades": 0, "pnl": 0, "win_rate": 0, "final_eq": INITIAL_EQUITY}
            continue

        wins = sum(1 for t in trades if t["pnl"] > 0)
        losses = sum(1 for t in trades if t["pnl"] < 0)
        total_pnl = sum(t["pnl"] for t in trades)
        wr = wins / len(trades) * 100 if trades else 0
        avg_win = sum(t["pnl"] for t in trades if t["pnl"] > 0) / wins if wins else 0
        avg_loss = sum(t["pnl"] for t in trades if t["pnl"] < 0) / losses if losses else 0
        pf = abs(avg_win / avg_loss) if avg_loss else float("inf")
        max_dd = 0
        eq = 0.0
        peak = 0.001  # evitar div by zero
        for t in trades:
            eq += t["pnl"]
            peak = max(peak, eq) if eq > 0 else peak
            dd = (eq - peak) / peak * 100 if peak > 0 else 0
            max_dd = min(max_dd, dd)

        results[name] = {
            "trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wr, 1),
            "total_pnl": round(total_pnl, 2),
            "final_equity": round(total_pnl, 2),
            "return_pct": round(total_pnl, 2),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_factor": round(pf, 2),
            "max_dd_pct": round(max_dd, 2),
        }
        for t in trades:
            t["bot"] = name
            portfolio_trades.append(t)
        print(f"  Trades: {len(trades)} | PnL: ${total_pnl:+.2f} | WR: {wr:.1f}% | DD: {max_dd:.2f}%")

    # Portfolio
    portfolio_trades.sort(key=lambda x: x.get("time", ""))
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    total_pnl = 0.0
    total_wins = 0
    for t in portfolio_trades:
        eq += t["pnl"]
        total_pnl += t["pnl"]
        peak = max(peak, eq)
        dd = (eq - peak) / peak * 100 if peak > 0 else 0
        max_dd = min(max_dd, dd)
        if t["pnl"] > 0:
            total_wins += 1

    print(f"\n{'='*60}")
    print(f"  PORTFOLIO (5 bots, desde 0)")
    print(f"{'='*60}")
    print(f"  Total trades:     {len(portfolio_trades)}")
    print(f"  Win rate:        {total_wins/len(portfolio_trades)*100:.1f}%" if portfolio_trades else "  Win rate:        N/A")
    print(f"  Total PnL:        ${total_pnl:+.2f}")
    print(f"  Final equity:     ${eq:.2f}")
    print(f"  Max DD:           {max_dd:.2f}%")
    print(f"{'='*60}")

    # Save
    out = Path(__file__).parent / "backtest_resultados_webhook.json"
    with open(out, "w") as f:
        json.dump({"bots": results, "portfolio": {
            "total_trades": len(portfolio_trades),
            "total_pnl": round(total_pnl, 2),
            "final_equity": round(eq, 2),
            "max_dd_pct": round(max_dd, 2),
        }}, f, indent=2)
    print(f"\nResultados guardados en {out}")


if __name__ == "__main__":
    run()