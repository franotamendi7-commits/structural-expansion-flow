import pandas as pd

archivos = [
    'backtest_Q22023.csv',
    'backtest_Q32023.csv',
    'backtest_Q42023.csv',
    'backtest_Q12024.csv',
    'backtest_Q22024.csv',
    'backtest_institucional_Q32024.csv',
    'backtest_Q12025.csv',
    'backtest_institucional_Q42025.csv',   # si ya la tenés con otro nombre, ajustá
    'backtest_Q32025.csv',
    'backtest_Q12026.csv',
    'backtest_Q22026_hasta_ayer.csv'
]

dfs = []
for arch in archivos:
    try:
        df = pd.read_csv(arch, parse_dates=['entry_time'])
        dfs.append(df)
        print(f"✔ {arch}: {len(df)} trades")
    except FileNotFoundError:
        print(f"⚠ No se encontró {arch}, se omite.")

if not dfs:
    raise SystemExit("No se encontró ningún archivo.")

full = pd.concat(dfs, ignore_index=True)
full = full.sort_values('entry_time').reset_index(drop=True)
full.to_csv('backtest_3years.csv', index=False)
print(f"\n📁 Total trades en backtest_3years.csv: {len(full)}")
