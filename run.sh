#!/usr/bin/env bash
# ============================================================
#  RUN.SH — STRUCTURAL EXPANSION FLOW (v3)
#  Un solo comando para lanzar el dashboard de los 5 bots
#  (BTC · ETH · SOL · XRP · BNB)
# ============================================================
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "╔══════════════════════════════════════════════════════╗"
echo "║   STRUCTURAL EXPANSION FLOW v3                      ║"
echo "║   BTC · ETH · SOL · XRP · BNB — 5-bot dashboard     ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# 1) Verificar Python
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "❌ Python no encontrado. Instalá Python 3.10+ desde https://python.org"
    exit 1
fi
echo "✅ Python: $($PYTHON --version)"

# 2) Verificar / crear venv
if [ ! -d ".venv" ]; then
    echo "📦 Creando entorno virtual..."
    $PYTHON -m venv .venv
fi
source .venv/bin/activate

# 3) Instalar dependencias
echo "📦 Instalando dependencias..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# 4) Verificar credentials.enc (opcional, para Live Trading en testnet)
if [ ! -f "credentials.enc" ]; then
    echo ""
    echo "⚠️  No se encontró credentials.enc"
    echo "   Para conectar a TESTNET necesitás crear el archivo encriptado:"
    echo ""
    echo "   cp credentials.example.json credentials.json"
    echo "   # Editar credentials.json con TUS KEYS de testnet"
    echo "   python cred_manager.py encrypt"
    echo "   rm credentials.json"
    echo ""
    echo "   ▶️  Sin testnet: el bot opera en PAPER mode con saldo virtual"
    echo ""
fi

# 5) Configurar Telegram (opcional — via env vars)
if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
    echo "✅ Telegram configurado via env vars"
else
    echo "ℹ️  Telegram no configurado via env vars"
    echo "   Las alertas se pueden configurar en el dashboard (sidebar)"
fi

echo ""
echo "🚀 Lanzando dashboard..."
echo "   Abrí http://localhost:8501 en tu navegador"
echo ""

# 6) Lanzar Streamlit
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
