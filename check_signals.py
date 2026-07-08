import pandas as pd, json, sys
from datetime import datetime, timezone, timedelta

# Cargar audit log
try:
    audit = pd.read_csv('audit_log.csv')
    audit['timestamp_entry'] = pd.to_datetime(audit['timestamp_entry'], utc=True)
except:
    audit = pd.DataFrame()

# Cargar rejection log
try:
    with open('rejection_log.json') as f:
        rej = json.load(f)
    rej_df = pd.DataFrame(rej)
    if not rej_df.empty:
        rej_df['timestamp'] = pd.to_datetime(rej_df['timestamp'], utc=True)
except:
    rej_df = pd.DataFrame()

# Ventana de 4 días
ahora = datetime.now(timezone.utc)
hace4d = ahora - timedelta(days=4)

# Filtrar por fecha
audit4 = audit[audit['timestamp_entry'] >= hace4d] if not audit.empty else pd.DataFrame()
rej4 = rej_df[rej_df['timestamp'] >= hace4d] if not rej_df.empty else pd.DataFrame()

print("🔍 Señales de los últimos 4 días (hasta ahora)\n")

if audit4.empty and rej4.empty:
    print("❌ No hubo ninguna señal en este período.")
    sys.exit()

# Señales del motor
if not audit4.empty:
    print("📌 SEÑALES DETECTADAS POR EL MOTOR (aprobadas por filtros fijos):")
    for _, row in audit4.iterrows():
        print(f"  {row['timestamp_entry']} | {row['symbol']} | {row['side']} | Score:{row['score']} | Salida:{row['exit_reason'] or 'Pendiente'}")

# Señales vetadas
if not rej4.empty:
    print("\n📌 SEÑALES VETADAS (por filtros o ML):")
    for _, row in rej4.iterrows():
        motivo = row.get('veto_reason','') or row.get('explanation','')
        print(f"  {row['timestamp']} | {row.get('symbol','?')} | {row.get('direction','?')} | Motivo: {motivo}")
