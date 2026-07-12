"""
X/Twitter auto-poster for VWAP Breakout signals.
Posts trade results automatically when signals close.
Needs: pip install tweepy

Setup:
1. Go to https://developer.twitter.com and create a project
2. Generate API keys (read+write permissions)
3. Set env vars or edit defaults below
"""
import os, json, time, logging
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("x_poster")

SIGNAL_LOG = Path(__file__).parent / "signal_log.json"
POSTED_LOG = Path(__file__).parent / "x_posted.log"

# ─── SET THESE when you have API keys ───
X_API_KEY = os.getenv("X_API_KEY", "")
X_API_SECRET = os.getenv("X_API_SECRET", "")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
X_ACCESS_SECRET = os.getenv("X_ACCESS_SECRET", "")

def fmt_pnl(pnl: float) -> str:
    if pnl > 0:
        return f"+${pnl:.2f}"
    return f"-${abs(pnl):.2f}"

def post_tweet(text: str) -> bool:
    if not all([X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET]):
        logger.warning("X API not configured. Set env vars X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET")
        return False
    try:
        import tweepy
        client = tweepy.Client(
            consumer_key=X_API_KEY, consumer_secret=X_API_SECRET,
            access_token=X_ACCESS_TOKEN, access_token_secret=X_ACCESS_SECRET,
        )
        client.create_tweet(text=text)
        return True
    except Exception as e:
        logger.error(f"Tweet failed: {e}")
        return False

def already_posted(signal_ts: str) -> bool:
    if not POSTED_LOG.exists():
        return False
    posted = POSTED_LOG.read_text().strip().split("\n")
    return signal_ts in posted

def mark_posted(signal_ts: str):
    with open(POSTED_LOG, "a") as f:
        f.write(signal_ts + "\n")

def watch_and_post():
    """Run in loop: check signal_log.json, post new exit signals to X."""
    logger.info("X poster watching for new signals...")
    while True:
        try:
            if not SIGNAL_LOG.exists():
                time.sleep(30)
                continue
            with open(SIGNAL_LOG) as f:
                signals = json.load(f)
            for sig in reversed(signals):
                if sig.get("type") != "exit":
                    continue
                if already_posted(sig["ts"]):
                    continue
                dir_str = "LONG" if sig.get("dir") == 1 else "SHORT"
                emoji = "✅" if sig.get("pnl", 0) > 0 else "❌"
                text = (
                    f"{emoji} #{sig['symbol']} {dir_str} CLOSED\n"
                    f"Entry: ${sig['entry']:.2f}\n"
                    f"PnL: {fmt_pnl(sig['pnl'])}\n"
                    f"Reason: {sig.get('reason', 'N/A').upper()}\n\n"
                    f"#crypto #trading #signals"
                )
                if post_tweet(text):
                    mark_posted(sig["ts"])
                    logger.info(f"Posted: {sig['symbol']} {dir_str} {sig.get('pnl', 0):+.2f}")
                time.sleep(5)
        except Exception as e:
            logger.error(f"Watch error: {e}")
        time.sleep(30)

if __name__ == "__main__":
    watch_and_post()
