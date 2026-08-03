#!/bin/bash
# Start bot daemon in background
python run_bots_background.py &
BOT_PID=$!

# Wait for daemon to start
sleep 5

# Function to cleanup on exit
cleanup() {
    echo "Shutting down..."
    if kill -0 $BOT_PID 2>/dev/null; then
        kill $BOT_PID
        wait $BOT_PID 2>/dev/null
    fi
    exit 0
}

# Trap signals
trap cleanup SIGTERM SIGINT

# Start Streamlit (this will be the main process)
exec streamlit run app.py \
    --server.port=${PORT:-8501} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --browser.gatherUsageStats=false