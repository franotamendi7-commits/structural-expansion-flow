#!/bin/bash
set -e

echo "=== Starting Trading Bot Daemon ==="
python run_bots_background.py > /tmp/bot.log 2>&1 &
BOT_PID=$!
echo "Bot daemon PID: $BOT_PID"

# Wait and check if daemon started
sleep 10
if ! kill -0 $BOT_PID 2>/dev/null; then
  echo "ERROR: Bot daemon failed to start!"
  cat /tmp/bot.log
  exit 1
fi

echo "Bot daemon running. Starting dashboard..."

# Redirect Streamlit logs too
exec streamlit run app.py \
  --server.port=$PORT \
  --server.address=0.0.0.0 \
  --server.headless=true \
  --browser.gatherUsageStats=false \
  --theme.base=dark \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false \
  > /tmp/streamlit.log 2>&1
