#!/bin/bash
# Start paper trading with Institutional Engine V2
# Logs to logs/paper_trading.log

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$DIR/logs"
LOG_FILE="$LOG_DIR/paper_trading.log"
SECRETS="$DIR/.streamlit/secrets.toml"

mkdir -p "$LOG_DIR"

echo "============================================"
echo "  PAPER TRADING - Institutional Engine V2"
echo "  $(date)"
echo "============================================"

# Verify API keys exist
if ! grep -q "BINANCE_API_KEY" "$SECRETS"; then
    echo "ERROR: BINANCE_API_KEY not found in $SECRETS"
    exit 1
fi

if ! grep -q "BINANCE_SECRET_KEY" "$SECRETS"; then
    echo "ERROR: BINANCE_SECRET_KEY not found in $SECRETS"
    exit 1
fi

echo "[OK] API keys configuradas"

# Quick API check
cd "$DIR"
API_OK=$(python3 verify_api.py 2>&1)
if echo "$API_OK" | grep -q "CONEXION EXITOSA"; then
    echo "[OK]Conexion Binance testnet verificada"
else
    echo "ERROR: Binance connection failed:"
    echo "$API_OK"
    exit 1
fi

echo ""
echo "Starting multi_bot_v2.py..."
echo "Logs: $LOG_FILE"
echo "Press Ctrl+C to stop"
echo ""

# Run bot with logging
python3 multi_bot_v2.py --interval 60 2>&1 | tee -a "$LOG_FILE"
