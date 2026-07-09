import sqlite3
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "trading.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            dir INTEGER NOT NULL,
            entry REAL NOT NULL,
            exit REAL,
            size REAL NOT NULL,
            net REAL,
            equity_before REAL,
            equity_after REAL,
            exit_reason TEXT,
            hold_minutes REAL,
            regime TEXT,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_name TEXT NOT NULL,
            equity REAL NOT NULL,
            timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            dir INTEGER NOT NULL,
            price REAL NOT NULL,
            stop_loss REAL,
            take_profit REAL,
            score REAL,
            regime TEXT,
            status TEXT DEFAULT 'pending',
            executed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS metrics_cache (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trades(exit_time);
        CREATE INDEX IF NOT EXISTS idx_trades_bot ON trades(bot_name);
        CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_snapshots(timestamp);
        CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
    """)
    conn.commit()
    conn.close()
    logger.info(f"DB initialized at {DB_PATH}")


def save_trade(trade: dict, bot_name: str) -> int:
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO trades (bot_name, symbol, dir, entry, exit, size, net,
                                equity_before, equity_after, exit_reason,
                                hold_minutes, regime, entry_time, exit_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            bot_name,
            trade.get("symbol", ""),
            trade.get("dir", 0),
            trade.get("entry", 0),
            trade.get("exit"),
            trade.get("size", 0),
            trade.get("net"),
            trade.get("equity_before"),
            trade.get("equity_after"),
            trade.get("exit_reason"),
            trade.get("hold_minutes"),
            trade.get("regime"),
            str(trade.get("entry_time", "")),
            str(trade.get("exit_time", "")),
        ))
        conn.commit()
        return cur.lastrowid or 0
    except Exception as e:
        logger.error(f"DB save_trade error: {e}")
        return 0
    finally:
        conn.close()


def save_equity_snapshot(bot_name: str, equity: float, timestamp: str = None):
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO equity_snapshots (bot_name, equity, timestamp)
            VALUES (?, ?, ?)
        """, (bot_name, equity, timestamp))
        conn.commit()
    except Exception as e:
        logger.error(f"DB save_equity error: {e}")
    finally:
        conn.close()


def save_signal(signal: Dict) -> int:
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO signals (bot_name, symbol, dir, price, stop_loss,
                                 take_profit, score, regime, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal.get("bot_name", ""),
            signal.get("symbol", ""),
            signal.get("dir", 0),
            signal.get("price"),
            signal.get("stop_loss"),
            signal.get("take_profit"),
            signal.get("score"),
            signal.get("regime"),
            signal.get("status", "pending"),
        ))
        conn.commit()
        return cur.lastrowid or 0
    except Exception as e:
        logger.error(f"DB save_signal error: {e}")
        return 0
    finally:
        conn.close()


def get_recent_trades(limit: int = 50) -> List[Dict]:
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT * FROM trades ORDER BY exit_time DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_equity_curve(bot_name: str = None, limit: int = 2000) -> List[Dict]:
    conn = get_conn()
    try:
        if bot_name:
            rows = conn.execute("""
                SELECT equity, timestamp FROM equity_snapshots
                WHERE bot_name = ? ORDER BY timestamp DESC LIMIT ?
            """, (bot_name, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT bot_name, equity, timestamp FROM equity_snapshots
                ORDER BY timestamp DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_recent_signals(limit: int = 30) -> List[Dict]:
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT * FROM signals ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_portfolio_stats() -> Dict:
    conn = get_conn()
    try:
        total_trades = conn.execute("SELECT COUNT(*) as c FROM trades").fetchone()["c"]
        wins = conn.execute("SELECT COUNT(*) as c FROM trades WHERE net > 0").fetchone()["c"]
        losses = conn.execute("SELECT COUNT(*) as c FROM trades WHERE net < 0").fetchone()["c"]
        total_pnl = conn.execute("SELECT COALESCE(SUM(net), 0) as s FROM trades").fetchone()["s"]
        avg_win = conn.execute("SELECT COALESCE(AVG(net), 0) as a FROM trades WHERE net > 0").fetchone()["a"]
        avg_loss = conn.execute("SELECT COALESCE(AVG(net), 0) as a FROM trades WHERE net < 0").fetchone()["a"]
        max_dd_row = conn.execute("""
            SELECT MIN((e.equity - p.peak) / p.peak * 100) as max_dd
            FROM equity_snapshots e
            JOIN (SELECT bot_name, timestamp, MAX(equity) OVER (ORDER BY timestamp) as peak
                  FROM equity_snapshots) p
            ON e.rowid = p.rowid
        """).fetchone()
        max_dd = max_dd_row["max_dd"] if max_dd_row else 0
        conn.close()
        return {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total_trades * 100) if total_trades else 0,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": abs(avg_win / avg_loss) if avg_loss else float("inf"),
            "max_drawdown_pct": max_dd if max_dd else 0,
        }
    except Exception as e:
        logger.error(f"DB get_portfolio_stats error: {e}")
        return {}
    finally:
        conn.close()


def cache_metric(key: str, value: any):
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO metrics_cache (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, json.dumps(value)))
        conn.commit()
    except Exception as e:
        logger.error(f"DB cache_metric error: {e}")
    finally:
        conn.close()


def get_cached_metric(key: str) -> Optional[any]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM metrics_cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def migrate_trade_history():
    """Migrate existing trade_history.json into SQLite (deduplicated)."""
    path = Path(__file__).parent / "trade_history.json"
    if not path.exists():
        logger.info("No trade_history.json to migrate")
        return 0

    conn = get_conn()
    existing = set()
    for row in conn.execute("SELECT entry_time, exit_time, entry, exit FROM trades").fetchall():
        existing.add((str(row["entry_time"] or ""), str(row["exit_time"] or ""),
                       float(row["entry"] or 0), float(row["exit"] or 0)))

    try:
        with open(path) as f:
            trades = json.load(f)
    except Exception as e:
        logger.error(f"Error reading trade_history.json: {e}")
        return 0

    migrated = 0
    for t in trades:
        key = (str(t.get("entry_time", "")), str(t.get("exit_time", "")),
               float(t.get("entry", 0)), float(t.get("exit", 0)))
        if key in existing:
            continue
        try:
            conn.execute("""
                INSERT INTO trades (bot_name, symbol, dir, entry, exit, size, net,
                                    equity_after, exit_reason, hold_minutes, regime,
                                    entry_time, exit_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                t.get("bot_name", ""),
                t.get("symbol", ""),
                t.get("dir", 0),
                t.get("entry", 0),
                t.get("exit"),
                t.get("size", 0),
                t.get("net"),
                t.get("equity_after"),
                t.get("exit_reason"),
                t.get("hold_minutes"),
                t.get("regime"),
                str(t.get("entry_time", "")),
                str(t.get("exit_time", "")),
            ))
            migrated += 1
        except Exception as e:
            logger.warning(f"Migration error for trade: {e}")

    conn.commit()
    conn.close()
    logger.info(f"Migrated {migrated} trades from trade_history.json to SQLite")
    return migrated


def export_all_trades_csv() -> str:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM trades ORDER BY exit_time DESC").fetchall()
        if not rows:
            return ""
        cols = [d[0] for d in conn.execute("PRAGMA table_info(trades)").fetchall()]
        lines = [",".join(cols)]
        for r in rows:
            lines.append(",".join(str(r[c]) if r[c] is not None else "" for c in cols))
        return "\n".join(lines)
    finally:
        conn.close()


init_db()