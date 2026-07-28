#!/usr/bin/env python3
"""
Backtest Leverage Comparison — Q2 2026
Recalcula equity curve y métricas con diferentes niveles de apalancamiento.
Capital inicial: $500 | Costos ya incluidos en pnl_neto
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from datetime import datetime
import os

# ─── Config ──────────────────────────────────────────────────────────────
INITIAL_CAPITAL = 500.0
LEVERAGES = [1, 3, 5]
CSV_PATH = "backtest_Q22026_completo.csv"
OUT_DIR = "."  # same directory as script

# ─── Load data ───────────────────────────────────────────────────────────
df = pd.read_csv(CSV_PATH)
pnls = df["pnl_neto"].values
entry_times = pd.to_datetime(df["entry_time"])
n_trades = len(pnls)
win_rate = (pnls > 0).sum() / n_trades * 100

print(f"Trades: {n_trades} | WR: {win_rate:.1f}% | PnL base total: ${pnls.sum():.2f}")

# ─── Helper: compute metrics for a leverage level ────────────────────────
def compute_metrics(pnl_series, leverage, initial_capital):
    """Return dict of metrics for given leveraged PnL series."""
    lev_pnl = pnl_series * leverage
    equity = np.cumsum(lev_pnl) + initial_capital
    equity = np.insert(equity, 0, initial_capital)  # prepend initial

    # Peak and drawdown
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak  # fraction
    dd_pct = dd * 100

    # Max drawdown
    max_dd = dd_pct.min()

    # Liquidation check: drawdown exceeds 1/leverage (e.g. 100% for 1x = never, 33% for 3x, 20% for 5x)
    liq_threshold = -100.0 / leverage  # in percent
    liquidated = max_dd < liq_threshold
    # Find first trade index where equity would be <= 0
    liq_trade = None
    if liquidated:
        for i in range(1, len(equity)):
            if equity[i] <= 0:
                liq_trade = i - 1  # trade index (0-based)
                break

    # Daily drawdown counts
    dd_10 = int(np.sum(dd_pct < -10))
    dd_20 = int(np.sum(dd_pct < -20))
    dd_30 = int(np.sum(dd_pct < -30))

    # Basic return metrics
    total_return_pct = (equity[-1] - initial_capital) / initial_capital * 100
    total_pnl = equity[-1] - initial_capital

    # Monthly return (period ~3.5 months: Apr 6 → Jul 17)
    days = (entry_times.iloc[-1] - entry_times.iloc[0]).days
    months = max(days / 30.44, 1)
    monthly_return = ((equity[-1] / initial_capital) ** (1 / months) - 1) * 100

    # Profit Factor
    gross_profit = lev_pnl[lev_pnl > 0].sum()
    gross_loss = abs(lev_pnl[lev_pnl < 0].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # Sharpe Ratio (annualized, assuming risk-free 0%)
    daily_returns = np.diff(equity) / equity[:-1]
    if daily_returns.std() > 0:
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
    else:
        sharpe = 0.0

    # Calmar Ratio (annualized return / |max DD|)
    annual_return = monthly_return * 12
    calmar = annual_return / abs(max_dd) if max_dd != 0 else float('inf')

    # Max consecutive losses
    max_consec_loss = 0
    current_consec = 0
    for p in lev_pnl:
        if p < 0:
            current_consec += 1
            max_consec_loss = max(max_consec_loss, current_consec)
        else:
            current_consec = 0

    return {
        "Leverage": f"{leverage}x",
        "Trades": n_trades,
        "Win Rate": f"{win_rate:.1f}%",
        "Total Return (%)": round(total_return_pct, 2),
        "Monthly Return (%)": round(monthly_return, 2),
        "Final Equity ($)": round(equity[-1], 2),
        "Total PnL ($)": round(total_pnl, 2),
        "Profit Factor": round(profit_factor, 2),
        "Sharpe Ratio": round(sharpe, 2),
        "Calmar Ratio": round(calmar, 2),
        "Max Drawdown (%)": round(max_dd, 2),
        "Max Consec Losses": max_consec_loss,
        "Days DD > 10%": dd_10,
        "Days DD > 20%": dd_20,
        "Days DD > 30%": dd_30,
        "Liquidated?": "YES" if liquidated else "NO",
        "Liq. Trade #": liq_trade if liq_trade is not None else "-",
    }, equity, dd_pct


# ─── Compute all leverage scenarios ──────────────────────────────────────
results = []
equity_curves = {}
dd_curves = {}

for lev in LEVERAGES:
    metrics, eq, dd = compute_metrics(pnls, lev, INITIAL_CAPITAL)
    results.append(metrics)
    equity_curves[lev] = eq
    dd_curves[lev] = dd

# ─── CSV output ──────────────────────────────────────────────────────────
results_df = pd.DataFrame(results)
csv_out = os.path.join(OUT_DIR, "backtest_leverage_comparison.csv")
results_df.to_csv(csv_out, index=False)
print(f"\nCSV saved: {csv_out}")

# ─── Markdown report ─────────────────────────────────────────────────────
md_lines = []
md_lines.append("# Backtest Leverage Comparison — Q2 2026")
md_lines.append(f"\n**Periodo:** {entry_times.iloc[0].strftime('%Y-%m-%d')} → {entry_times.iloc[-1].strftime('%Y-%m-%d')}")
md_lines.append(f"**Capital inicial:** ${INITIAL_CAPITAL:.0f}")
md_lines.append(f"**Trades:** {n_trades} | **Win Rate:** {win_rate:.1f}%")
md_lines.append(f"**Costos:** Ya incluidos en `pnl_neto`")
md_lines.append("")
md_lines.append("## Tabla Comparativa\n")
md_lines.append("| Métrica | " + " | ".join(r["Leverage"] for r in results) + " |")
md_lines.append("|---|" + "|".join(["---"] * len(results)) + "|")

# Build rows for each metric
metric_keys = [k for k in results[0].keys() if k != "Leverage"]
for key in metric_keys:
    vals = [str(r[key]) for r in results]
    md_lines.append(f"| **{key}** | " + " | ".join(vals) + " |")

md_lines.append("")
md_lines.append("## Análisis\n")

# Liquidation analysis
for r in results:
    lev = r["Leverage"]
    if r["Liquidated?"] == "YES":
        md_lines.append(f"- **{lev}:** SE HABRÍA LIQUIDADO en trade #{r['Liq. Trade #']} "
                        f"(Max DD: {r['Max Drawdown (%)']}% supera umbral de {-100/int(lev.replace('x','')):.1f}%)")
    else:
        md_lines.append(f"- **{lev}:** No liquidado (Max DD: {r['Max Drawdown (%)']}% vs "
                        f"umbral de {-100/int(lev.replace('x','')):.1f}%)")

md_lines.append("")
md_lines.append("## Recomendación\n")

# Find best non-liquidated
non_liq = [r for r in results if r["Liquidated?"] == "NO"]
if non_liq:
    best = max(non_liq, key=lambda r: float(r["Sharpe Ratio"]))
    md_lines.append(f"- **Mejor perfil riesgo-retorno:** {best['Leverage']} "
                    f"(Sharpe {best['Sharpe Ratio']}, retorno mensual {best['Monthly Return (%)']}%)")
md_lines.append(f"- **3x** amplifica retornos pero aumenta riesgo de drawdown significativo")
md_lines.append(f"- **5x** con WR 47% es peligroso: drawdown profundo, cercano a liquidación")
md_lines.append("")
md_lines.append("---")
md_lines.append("*Generado por `backtest_leverage_comparison.py`*")

md_out = os.path.join(OUT_DIR, "backtest_leverage_comparison.md")
with open(md_out, "w") as f:
    f.write("\n".join(md_lines))
print(f"MD saved: {md_out}")

# ─── Chart ───────────────────────────────────────────────────────────────
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), gridspec_kw={'height_ratios': [3, 1]})
fig.suptitle("Backtest Leverage Comparison — Q2 2026\nInstitutional Engine · 5 Pairs · 151 Trades",
             fontsize=14, fontweight='bold', y=0.98)

colors = {1: '#2196F3', 3: '#FF9800', 5: '#F44336'}
labels = {1: '1x (Sin apalancamiento)', 3: '3x Apalancamiento', 5: '5x Apalancamiento'}

for lev in LEVERAGES:
    eq = equity_curves[lev]
    ax1.plot(range(len(eq)), eq, color=colors[lev], linewidth=2, label=labels[lev])
    # Mark liquidation if applicable
    r = [x for x in results if x["Leverage"] == f"{lev}x"][0]
    if r["Liquidated?"] == "YES" and r["Liq. Trade #"] != "-":
        liq_idx = int(r["Liq. Trade #"]) + 1
        ax1.scatter([liq_idx], [0], color=colors[lev], marker='x', s=200, zorder=5, linewidth=3)
        ax1.annotate(f'LIQUIDADO\nTrade #{liq_idx}',
                     xy=(liq_idx, 0), xytext=(liq_idx + 3, eq[max(0, liq_idx-10)] * 0.3),
                     fontsize=9, color=colors[lev], fontweight='bold',
                     arrowprops=dict(arrowstyle='->', color=colors[lev]))

ax1.axhline(y=INITIAL_CAPITAL, color='gray', linestyle='--', alpha=0.5, label=f'Capital inicial (${INITIAL_CAPITAL:.0f})')
ax1.set_xlabel('Trade #', fontsize=11)
ax1.set_ylabel('Equity ($)', fontsize=11)
ax1.set_title('Equity Curve por Apalancamiento', fontsize=12)
ax1.legend(loc='upper left', fontsize=10)
ax1.grid(True, alpha=0.3)
ax1.yaxis.set_major_formatter(mtick.FormatStrFormatter('$%.0f'))

# Drawdown subplot
for lev in LEVERAGES:
    dd = dd_curves[lev]
    ax2.fill_between(range(len(dd)), dd, 0, alpha=0.3, color=colors[lev], label=f'{lev}x DD')
    ax2.plot(range(len(dd)), dd, color=colors[lev], linewidth=1)

# Drawdown thresholds
for thresh, label in [(-10, '-10%'), (-20, '-20%'), (-30, '-30%')]:
    ax2.axhline(y=thresh, color='red', linestyle=':', alpha=0.4)
    ax2.text(len(pnls) * 0.97, thresh + 1, label, fontsize=8, color='red', alpha=0.6)

# Liquidation thresholds
for lev in LEVERAGES:
    liq_thresh = -100 / lev
    ax2.axhline(y=liq_thresh, color=colors[lev], linestyle='--', alpha=0.5)
    ax2.text(len(pnls) * 0.75, liq_thresh + 1, f'{lev}x liq ({liq_thresh:.0f}%)',
             fontsize=8, color=colors[lev], alpha=0.7)

ax2.set_xlabel('Trade #', fontsize=11)
ax2.set_ylabel('Drawdown (%)', fontsize=11)
ax2.set_title('Drawdown por Apalancamiento', fontsize=12)
ax2.legend(loc='lower left', fontsize=9)
ax2.grid(True, alpha=0.3)
ax2.yaxis.set_major_formatter(mtick.FormatStrFormatter('%.0f%%'))

plt.tight_layout(rect=[0, 0, 1, 0.95])
chart_out = os.path.join(OUT_DIR, "backtest_leverage_comparison.png")
plt.savefig(chart_out, dpi=150, bbox_inches='tight', facecolor='white')
plt.close()
print(f"Chart saved: {chart_out}")

# ─── Print summary table ────────────────────────────────────────────────
print("\n" + "=" * 80)
print("TABLA COMPARATIVA COMPLETA")
print("=" * 80)
print(f"{'Métrica':<25} {'1x':>12} {'3x':>12} {'5x':>12}")
print("-" * 61)
for key in metric_keys:
    vals = [r[key] for r in results]
    print(f"{key:<25} {str(vals[0]):>12} {str(vals[1]):>12} {str(vals[2]):>12}")
print("=" * 80)

print("\n--- RECOMENDACIÓN ---")
for r in results:
    lev = r["Leverage"]
    if r["Liquidated?"] == "YES":
        print(f"  {lev}: LIQUIDADO en trade #{r['Liq. Trade #']} (DD {r['Max Drawdown (%)']}%)")
    else:
        print(f"  {lev}: Sharpe={r['Sharpe Ratio']}, Retorno mensual={r['Monthly Return (%)']}%, "
              f"Max DD={r['Max Drawdown (%)']}%")
