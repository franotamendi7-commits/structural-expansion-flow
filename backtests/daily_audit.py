"""
Auditoría diaria — verifica que todo el sistema funcione correctamente.
Ejecutar cada 24h: python daily_audit.py
"""
import json, os, sys, time
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent
LOGS = BASE / "logs"
REPORT = BASE / "daily_audit_report.json"

CHECKS = {
    "passed": [],
    "warnings": [],
    "errors": [],
}

def log_check(name: str, ok: bool, detail: str = ""):
    status = "✅" if ok else "❌"
    level = "passed" if ok else "errors"
    print(f"  {status} {name}: {detail}")
    CHECKS[level].append({"check": name, "detail": detail})

def check_daemon():
    import subprocess
    r = subprocess.run(["ps", "aux"], capture_output=True, text=True)
    has_daemon = "run_bots_background" in r.stdout
    if has_daemon:
        for line in r.stdout.splitlines():
            if "run_bots_background" in line and "grep" not in line:
                parts = line.split()
                pid = parts[1]
                cpu = parts[2]
                mem = parts[3]
                uptime = parts[9]
                log_check("Daemon 24/7", True, f"PID {pid} | CPU {cpu}% | MEM {mem}% | Up {uptime}")
                return pid
    else:
        log_check("Daemon 24/7", False, "No run_bots_background process found")
    return None

def check_api():
    import urllib.request, json
    try:
        resp = urllib.request.urlopen("http://localhost:8585/health", timeout=5)
        data = json.loads(resp.read())
        ok = data.get("status") == "ok"
        log_check("API Server", ok, f"HTTP {resp.status} /health → {data}")
    except Exception as e:
        log_check("API Server", False, str(e))

def check_dashboard():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(3)
        r = s.connect_ex(("127.0.0.1", 8501))
        ok = r == 0
        log_check("Dashboard Streamlit", ok, f"Port 8501 {'OPEN' if ok else 'CLOSED'}")
    finally:
        s.close()

def check_tunnels():
    import subprocess, re
    r = subprocess.run(["ps", "aux"], capture_output=True, text=True)
    tunnels = [l for l in r.stdout.splitlines() if "cloudflared tunnel" in l]
    dash = [t for t in tunnels if "8501" in t or "dashboard" in t]
    api = [t for t in tunnels if "8585" in t or "api" in t]
    log_check("Tunnel Dashboard", len(dash) > 0, f"{len(dash)} process(es)")
    log_check("Tunnel API", len(api) > 0, f"{len(api)} process(es)")

def check_signal_log():
    path = BASE / "signal_log.json"
    if not path.exists():
        log_check("Signal Log", False, "File not found")
        return
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            log_check("Signal Log", False, "Not a list")
            return
        n = len(data)
        last = data[-1] if n > 0 else None
        hash_ok = last and "hash" in last and "prev_hash" in last
        eth_ok = last and "eth_signature" in last
        hash_status = f"SHA256: {'YES' if hash_ok else 'NO'}"
        eth_status = f"ETH sig: {'YES' if eth_ok else 'NO'}" if last else "No trades yet"
        log_check("Signal Log Integrity", True, f"{n} records | {hash_status} | {eth_status}")
        if last:
            log_check("Signal Log Last Entry", True, f"{last.get('ts','')} {last.get('symbol','')} {last.get('type','')}")
    except Exception as e:
        log_check("Signal Log", False, str(e))

def check_disk():
    import subprocess
    r = subprocess.run(["du", "-sh", str(LOGS)], capture_output=True, text=True)
    size = r.stdout.strip().split("\t")[0] if r.stdout else "?"
    log_check("Disk Usage", True, f"Logs: {size}")
    limit = 500  # MB
    try:
        mb = float(size.replace("M", "").replace("G", "")) * (1024 if "G" in size else 1)
        if mb > limit:
            log_check("Disk Warning", False, f"Logs are {size} (limit {limit}MB)")
    except:
        pass

def check_secrets():
    path = BASE / ".streamlit" / "secrets.toml"
    if not path.exists():
        log_check("Secrets File", False, "Not found")
        return
    text = path.read_text()
    keys = {"TELEGRAM_TOKEN": False, "TELEGRAM_CHANNEL_ID": False, "BINANCE_API_KEY": False, "BINANCE_SECRET_KEY": False, "ETH_PRIVATE_KEY": False}
    for line in text.splitlines():
        for k in keys:
            if line.startswith(k):
                keys[k] = True
    missing = [k for k, v in keys.items() if not v]
    if missing:
        log_check("Secrets", False, f"Missing: {', '.join(missing)}")
    else:
        log_check("Secrets", True, "All keys present")

def check_git():
    import subprocess
    r = subprocess.run(["git", "log", "--oneline", "-1"], capture_output=True, text=True, cwd=BASE)
    last = r.stdout.strip() if r.stdout else "?"
    log_check("Git Latest", True, last)

def check_recent_trades():
    path = LOGS / "bot_daemon.log"
    if not path.exists():
        return
    lines = path.read_text().strip().splitlines()
    trades = [l for l in lines if "Equity:" in l and "Trades:" in l]
    if trades:
        last = trades[-1]
        log_check("Bot Activity", True, last)

def run():
    print(f"\n{'='*60}")
    print(f"  DAILY AUDIT — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    check_daemon()
    check_api()
    check_dashboard()
    check_tunnels()
    check_signal_log()
    check_disk()
    check_secrets()
    check_git()
    check_recent_trades()

    total = len(CHECKS["passed"])
    warns = len(CHECKS["warnings"])
    errors = len(CHECKS["errors"])
    print(f"\n{'='*60}")
    print(f"  RESULT: {total} ✅  |  {warns} ⚠️  |  {errors} ❌")
    print(f"{'='*60}\n")

    report = {
        "ts": datetime.now().isoformat(),
        "passed": total,
        "warnings": warns,
        "errors": errors,
        "checks": CHECKS,
    }
    REPORT.write_text(json.dumps(report, indent=2))

    if errors > 0:
        sys.exit(1)

if __name__ == "__main__":
    run()
