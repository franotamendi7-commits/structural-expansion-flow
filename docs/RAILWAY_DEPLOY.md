# Railway Deployment Guide

## Overview

This guide walks you through deploying the trading bot system to Railway so it runs 24/7 without depending on Francisco's Mac.

The system consists of two services running in a single container:
1. **Bot Daemon** (`run_bots_background.py`) - Runs 5 institutional trading bots (BTC, ETH, SOL, BNB, XRP) on Binance Futures testnet
2. **Streamlit Dashboard** (`app.py`) - Real-time monitoring dashboard on port 8501

---

## Prerequisites

- [Railway account](https://railway.app) (free tier works)
- [GitHub account](https://github.com) with the repo `franotamendi7-commits/structural-expansion-flow`
- Binance Futures **testnet** API keys (already configured)

---

## Step 1: Push Code to GitHub

Make sure all changes are committed and pushed:

```bash
cd /Users/franciscootamendi/ai-agents-v3
git add .
git commit -m "Railway deployment: add env var support, start.sh, update Dockerfile/Procfile"
git push origin main
```

---

## Step 2: Create Railway Project

1. Go to [railway.app](https://railway.app) and log in
2. Click **"New Project"**
3. Select **"Deploy from GitHub repo"**
4. Authorize Railway to access your GitHub account
5. Select the repo: `franotamendi7-commits/structural-expansion-flow`
6. Railway will auto-detect the Dockerfile and start building

---

## Step 3: Set Environment Variables

In the Railway dashboard, go to your service → **Variables** tab and add:

### Required (Binance Testnet)
```
BINANCE_API_KEY=<your-testnet-api-key>
BINANCE_SECRET_KEY=<your-testnet-secret-key>
```

### Optional (Telegram Alerts)
```
TELEGRAM_TOKEN=<your-telegram-bot-token>
TELEGRAM_CHAT_ID=<your-chat-id>
TELEGRAM_CHANNEL_ID=<your-channel-id>
```

### Optional (Other)
```
WEBHOOK_SECRET=<if-using-tradingview-webhook>
```

> **Note:** The code reads from environment variables first, then falls back to `.streamlit/secrets.toml`. On Railway, only env vars are needed.

---

## Step 4: Configure Service Settings

In the Railway dashboard:

1. Go to **Settings** → **Networking**
2. Ensure **Public Networking** is enabled (for dashboard access)
3. Railway will assign a URL like `https://your-project.up.railway.app`

---

## Step 5: Deploy

Railway auto-deploys on push. To trigger a manual deploy:

1. Go to **Deployments** tab
2. Click **"Deploy"** (or it deploys automatically on git push)

### Monitor Build Logs

- Go to **Deployments** → click the latest deployment
- Watch the build logs for errors
- The container should start and show:
  ```
  Starting Institutional V2 Bots (24/7 daemon)
  MultiBotSystemV2 initialized with 4 bots
  ```

---

## Step 6: Verify Deployment

### Check Bot Daemon
1. In Railway dashboard, go to **Deployments** → **Logs**
2. Look for lines like:
   ```
   Tick: 0 positions, 0 signals
   Equity: $X,XXX.XX | PnL: $0.00 | Trades: 0 | WR: 0.0%
   ```

### Check Dashboard
1. Open your Railway URL (e.g., `https://your-project.up.railway.app`)
2. You should see the CYBER COMMAND dashboard
3. The dashboard auto-refreshes every 30 seconds

---

## Architecture

```
Railway Container
├── start.sh (entrypoint)
│   ├── python run_bots_background.py  (bot daemon, background)
│   └── streamlit run app.py           (dashboard, foreground)
├── engine/
│   ├── institutional_engine_v2.py
│   ├── position_manager.py
│   ├── drawdown_manager.py
│   └── parameter_adapter.py
├── multi_bot_v2.py
├── execution_manager.py
└── app.py
```

### State Files (Ephemeral on Railway)
- `drawdown_state.json` - Drawdown manager state
- `parameter_state.json` - Auto-adaptation state
- `position_state_*.json` - Open positions per symbol
- `trade_history.json` - Trade history
- `signal_log.json` - Signal audit trail with SHA256 chain
- `logs/daemon.log` - Bot daemon logs

> **Warning:** Railway containers restart occasionally. State files will be lost on restart. This is acceptable for testnet trading. For production, consider using Railway Volumes or an external database.

---

## Cost Estimate

- **Railway Free Tier:** $5 credit/month (sufficient for testnet)
- **Hobby Plan ($5/mo):** If you need more resources
- The bot uses minimal CPU/memory (Python + Streamlit)

---

## Troubleshooting

### Bot not starting
- Check logs for import errors
- Verify all env vars are set correctly
- Ensure `requirements.txt` is up to date

### Dashboard shows "STOPPED"
- The bot daemon may have crashed
- Check logs for errors
- Railway restarts failed containers automatically

### API errors
- Verify testnet API keys are correct
- Check Binance testnet status: https://testnet.binancefuture.com

### Container restarting
- Check memory usage (Streamlit can be memory-heavy)
- Consider upgrading Railway plan if hitting limits

---

## Files Changed for Railway Deployment

| File | Change |
|------|--------|
| `requirements.txt` | Added `toml>=0.10` |
| `multi_bot_v2.py` | Added env var support for Binance keys |
| `app.py` | Added env var support for Binance keys |
| `multi_bot.py` | Added env var support for Telegram secrets |
| `Procfile` | Changed to use `start.sh` |
| `Dockerfile` | Changed CMD to use `start.sh` |
| `start.sh` | **NEW** - Runs bot daemon + Streamlit |

---

## Next Steps

1. **Monitor for 1-2 weeks** on testnet
2. **Review trade performance** in the dashboard
3. **Consider Railway Volumes** for persistent state (if moving to real trading)
4. **Set up alerts** via Telegram for critical events
5. **Consider separating services** (bot daemon + dashboard) for better resource management

---

## Quick Reference

```bash
# View live logs (Railway CLI)
railway logs

# Trigger redeploy
railway up

# Check service status
railway status

# Open dashboard
railway open
```

---

*Last updated: August 2026*
