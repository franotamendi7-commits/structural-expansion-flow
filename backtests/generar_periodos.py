import subprocess

periodos = [
    ("2023-01-01", "2023-03-31", "Q12023"),
    ("2024-07-01", "2024-09-30", "Q32024"),
    ("2025-10-01", "2025-12-31", "Q42025"),
    ("2026-03-19", "2026-06-17", "Q12026_ref")
]

for start, end, nombre in periodos:
    cmd = f"python3 backtest_institucional_v1.py --start {start} --end {end} --output backtest_institucional_{nombre}.csv"
    print(f"\n🚀 Generando {nombre} ({start} → {end})...")
    resultado = subprocess.run(cmd, shell=True)
    if resultado.returncode != 0:
        print(f"⚠️ Falló {nombre}, revisá el comando.")
    else:
        print(f"✅ {nombre} terminado.")
