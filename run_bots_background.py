"""
RUN BOTS BACKGROUND — 24/7 daemon
Lanza MultiBotSystem.start() como proceso independiente.
El dashboard puede seguir abierto para monitoreo.
"""
import time, sys, os, signal, logging
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "daemon.log"),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger("daemon")

PID_FILE = Path(__file__).parent / "bots_daemon.pid"

def write_pid():
    PID_FILE.write_text(str(os.getpid()))

def cleanup(signum=None, frame=None):
    logger.info("Shutting down bots...")
    if hasattr(system, 'stop'):
        system.stop()
    if PID_FILE.exists():
        PID_FILE.unlink()
    logger.info("Bots stopped.")
    sys.exit(0)

def daily_report(system):
    """Send daily summary via Telegram."""
    try:
        from multi_bot import telegram_alert
        summary = system.portfolio_summary()
        equity = summary['total_equity']
        pnl = summary['net_pnl']
        trades = summary['total_trades']
        wr = summary['win_rate']
        bots_status = ""
        for name, bot in system.bots.items():
            s = bot.status()
            in_pos = s.get("position") is not None
            pos_str = f"In position" if in_pos else "No position"
            bots_status += f"\n  {name}: ${s['equity']:.2f} | {pos_str}"
        msg = (
            f"📊 <b>DAILY REPORT</b>\n"
            f"Equity: ${equity:.2f} | PnL: ${pnl:.2f}\n"
            f"Trades: {trades} | WR: {wr:.1f}%\n"
            f"Bots:{bots_status}"
        )
        telegram_alert(msg)
        logger.info("Daily report sent")
    except Exception as e:
        logger.warning(f"Daily report error: {e}")

if __name__ == "__main__":
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, 0)
            logger.error(f"Already running (PID {old_pid}). Kill it first: kill {old_pid}")
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            PID_FILE.unlink()

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    from multi_bot import MultiBotSystem

    logger.info("=" * 60)
    logger.info("Starting VWAP Breakout Bots (24/7 daemon)")
    logger.info("=" * 60)

    system = MultiBotSystem()
    system.start()

    write_pid()
    logger.info(f"Daemon PID: {os.getpid()}")
    logger.info("5 bots running in background threads (poll every 5 min)")
    logger.info("Stop with: kill $(cat bots_daemon.pid)")
    logger.info("=" * 60)

    # Keep main thread alive
    last_report_day = None
    try:
        while True:
            time.sleep(60)
            now = datetime.now(timezone.utc)
            # Log summary every 5 min
            if int(time.time()) % 300 < 60:
                summary = system.portfolio_summary()
                logger.info(
                    f"Equity: ${summary['total_equity']:.2f} | "
                    f"PnL: ${summary['net_pnl']:+.2f} | "
                    f"Trades: {summary['total_trades']} | "
                    f"WR: {summary['win_rate']:.1f}%"
                )
            # Daily report at 00:00 UTC
            report_day = now.strftime("%Y-%m-%d")
            if report_day != last_report_day and now.hour == 0 and now.minute < 5:
                last_report_day = report_day
                daily_report(system)
    except KeyboardInterrupt:
        cleanup()
