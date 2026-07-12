"""
On-chain audit trail for VWAP Breakout signals.
Each trade hash is signed with an Ethereum wallet for immutable verification.

Usage:
  from chain_audit import sign_trade, verify_trade

Requires ETH_PRIVATE_KEY in secrets.toml or env.
If no key is configured, signing is skipped silently.
"""
import json, os
from pathlib import Path
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

SECRETS_PATH = Path(__file__).parent / ".streamlit" / "secrets.toml"
SIGNAL_LOG = Path(__file__).parent / "signal_log.json"

def _load_private_key() -> str | None:
    if key := os.getenv("ETH_PRIVATE_KEY"):
        return key
    try:
        if SECRETS_PATH.exists():
            text = SECRETS_PATH.read_text()
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("ETH_PRIVATE_KEY"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except:
        pass
    return None

def _get_wallet_address(key: str) -> str:
    acct = Account.from_key(key)
    return acct.address

def sign_message(message: str, private_key: str) -> str:
    msg = encode_defunct(text=message)
    signed = Account.sign_message(msg, private_key)
    return signed.signature.hex()

def sign_trade(record: dict) -> dict:
    key = _load_private_key()
    if not key:
        return record
    address = _get_wallet_address(key)
    raw = json.dumps(record, sort_keys=True)
    signature = sign_message(raw, key)
    record["eth_address"] = Web3.to_checksum_address(address)
    record["eth_signature"] = signature
    record["verify_url"] = (
        f"https://etherscan.io/verifySig/{Web3.to_checksum_address(address)}"
        f"?msg={Web3.keccak(text=raw).hex()}&sig={signature}"
    )
    return record

def verify_trade(record: dict) -> bool:
    if not record.get("eth_signature") or not record.get("eth_address"):
        return False
    raw = json.dumps({k: v for k, v in record.items() if k not in ("eth_signature", "eth_address", "verify_url")}, sort_keys=True)
    msg = encode_defunct(text=raw)
    address = Web3.to_checksum_address(record["eth_address"])
    try:
        recovered = Account.recover_message(msg, signature=record["eth_signature"])
        return Web3.to_checksum_address(recovered) == address
    except:
        return False

def verify_all() -> list[dict]:
    results = []
    if not SIGNAL_LOG.exists():
        return results
    log = json.loads(SIGNAL_LOG.read_text())
    for i, rec in enumerate(log):
        if rec.get("eth_signature"):
            ok = verify_trade(rec)
            results.append({"index": i, "ts": rec["ts"], "valid": ok, "hash": rec.get("hash", "")})
    return results

if __name__ == "__main__":
    key = _load_private_key()
    if key:
        addr = _get_wallet_address(key)
        print(f"Wallet: {addr}")
        print(f"Balance: (check on Etherscan)")
    else:
        print("No ETH_PRIVATE_KEY configured. Set in .streamlit/secrets.toml or env.")
        print("Example: ETH_PRIVATE_KEY=0x...")
