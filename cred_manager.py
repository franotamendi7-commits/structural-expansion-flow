"""
============================================================
 CREDENTIAL MANAGER — Encrypted credential storage
============================================================
 Provides:
   - encrypt_file(input_path, output_path, password) — encrypts a JSON file
   - load_credentials(encrypted_path, password) — decrypts and returns dict
   - get_credential(key, default=None) — get a single credential
   - CredentialsGate — Streamlit UI component for password entry

 The encryption uses Fernet (AES-128-CBC + HMAC-SHA256) from the
 `cryptography` package. The password is stretched with PBKDF2-HMAC-SHA256
 (600,000 iterations, 16-byte salt) before being used as the Fernet key.

 USAGE:
   1. Create credentials.json with your secrets (see credentials.example.json)
   2. Run: python cred_manager.py encrypt
      -> Prompts for master password
      -> Creates credentials.enc (delete credentials.json after)
   3. In app.py:
      from cred_manager import get_credentials_or_prompt
      creds = get_credentials_or_prompt()  # prompts via Streamlit if needed
      tg_token = creds.get("TELEGRAM_BOT_TOKEN")
============================================================
"""
import os
import sys
import json
import getpass
import logging
from pathlib import Path
from typing import Optional, Dict, Any

import base64

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

logger = logging.getLogger("cred_manager")

# Default file locations (relative to this module's directory)
_MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_ENCRYPTED_PATH = _MODULE_DIR / "credentials.enc"
DEFAULT_PLAIN_PATH = _MODULE_DIR / "credentials.json"

# PBKDF2 parameters (OWASP 2023 recommendation: >= 600,000 iterations for SHA-256)
PBKDF2_ITERATIONS = 600_000
SALT_SIZE = 16  # bytes


def _check_crypto() -> None:
    """Raise a helpful error if `cryptography` is not installed."""
    if not CRYPTO_AVAILABLE:
        raise RuntimeError(
            "The 'cryptography' package is required for credential encryption. "
            "Install it with: pip install cryptography"
        )


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible key from a password + salt using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    key = kdf.derive(password.encode("utf-8"))
    return base64.urlsafe_b64encode(key)


def encrypt_bytes(data: bytes, password: str) -> bytes:
    """
    Encrypt raw bytes with a password.
    Returns: salt (16 bytes) + Fernet token (variable length).
    """
    _check_crypto()
    salt = os.urandom(SALT_SIZE)
    key = _derive_key(password, salt)
    f = Fernet(key)
    token = f.encrypt(data)
    return salt + token


def decrypt_bytes(encrypted: bytes, password: str) -> bytes:
    """
    Decrypt bytes produced by encrypt_bytes().
    Raises InvalidToken if the password is wrong.
    """
    _check_crypto()
    if len(encrypted) < SALT_SIZE + 1:
        raise ValueError("Encrypted data is too short (corrupted?)")
    salt = encrypted[:SALT_SIZE]
    token = encrypted[SALT_SIZE:]
    key = _derive_key(password, salt)
    f = Fernet(key)
    return f.decrypt(token)


def encrypt_file(input_path: Path, output_path: Path, password: str) -> None:
    """Encrypt a JSON file and write the encrypted blob to output_path."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    with open(input_path, "rb") as f:
        data = f.read()
    encrypted = encrypt_bytes(data, password)
    with open(output_path, "wb") as f:
        f.write(encrypted)
    logger.info(f"Encrypted {input_path} -> {output_path} ({len(encrypted)} bytes)")


def decrypt_file(input_path: Path, password: str) -> bytes:
    """Decrypt an encrypted file and return the raw bytes."""
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Encrypted file not found: {input_path}")
    with open(input_path, "rb") as f:
        encrypted = f.read()
    return decrypt_bytes(encrypted, password)


def load_credentials(encrypted_path: Path = DEFAULT_ENCRYPTED_PATH,
                     password: Optional[str] = None) -> Dict[str, Any]:
    """
    Load and decrypt credentials.enc, returning the dict.
    If password is None, prompts via getpass (for CLI use).
    """
    if password is None:
        password = getpass.getpass("Master password: ")
    raw = decrypt_file(encrypted_path, password)
    return json.loads(raw.decode("utf-8"))


# ============================================================
# Streamlit integration
# ============================================================
def get_credentials_or_prompt(encrypted_path: Path = DEFAULT_ENCRYPTED_PATH,
                              key_session_state: str = "decrypted_credentials"
                              ) -> Optional[Dict[str, Any]]:
    """
    Streamlit-friendly credential loader.
    Returns the decrypted credentials dict if available, else None.

    Flow:
      1. If credentials are already in session_state, return them.
      2. If not, show a password input via st.text_input(type="password").
         When the user submits, try to decrypt credentials.enc.
         On success, store in session_state and return.
         On failure, show error and return None.
      3. If credentials.enc doesn't exist, return None (user can fall back
         to manual entry in sidebar).
    """
    import streamlit as st

    # Already decrypted this session?
    if key_session_state in st.session_state:
        return st.session_state[key_session_state]

    # Encrypted file exists?
    if not encrypted_path.exists():
        return None

    # Show password input
    st.sidebar.markdown("---")
    st.sidebar.markdown("#### 🔐 Credentials")
    pw = st.sidebar.text_input("Master password", type="password",
                                help="Decrypts credentials.enc")
    if not pw:
        st.sidebar.info("Ingresa la master password para cargar credenciales")
        return None

    try:
        creds = load_credentials(encrypted_path, pw)
        st.session_state[key_session_state] = creds
        st.sidebar.success("✅ Credenciales cargadas")
        return creds
    except InvalidToken:
        st.sidebar.error("❌ Password incorrecta")
        return None
    except Exception as e:
        st.sidebar.error(f"❌ Error: {e}")
        return None


def get_credential(key: str, default: Optional[str] = None,
                   encrypted_path: Path = DEFAULT_ENCRYPTED_PATH) -> Optional[str]:
    """
    Get a single credential by key. Returns default if not available.
    Uses session_state cache if available.
    """
    import streamlit as st
    if "decrypted_credentials" in st.session_state:
        return st.session_state["decrypted_credentials"].get(key, default)
    return default


def clear_credentials() -> None:
    """Clear decrypted credentials from session_state (logout)."""
    import streamlit as st
    if "decrypted_credentials" in st.session_state:
        del st.session_state["decrypted_credentials"]


# ============================================================
# CLI: python cred_manager.py encrypt
# ============================================================
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Credential encryption utility")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_enc = sub.add_parser("encrypt", help="Encrypt credentials.json -> credentials.enc")
    p_enc.add_argument("--input", default=str(DEFAULT_PLAIN_PATH),
                       help="Input JSON file (default: credentials.json)")
    p_enc.add_argument("--output", default=str(DEFAULT_ENCRYPTED_PATH),
                       help="Output encrypted file (default: credentials.enc)")

    p_dec = sub.add_parser("decrypt", help="Decrypt credentials.enc -> stdout (JSON)")
    p_dec.add_argument("--input", default=str(DEFAULT_ENCRYPTED_PATH),
                       help="Encrypted file (default: credentials.enc)")

    p_test = sub.add_parser("test", help="Test password against credentials.enc")

    args = ap.parse_args()

    if args.cmd == "encrypt":
        _check_crypto()
        pw1 = getpass.getpass("Choose a master password: ")
        pw2 = getpass.getpass("Confirm master password: ")
        if pw1 != pw2:
            print("ERROR: passwords do not match")
            sys.exit(1)
        if len(pw1) < 8:
            print("WARNING: password is short (<8 chars). Consider a longer one.")
            cont = input("Continue anyway? (y/N): ").strip().lower()
            if cont != "y":
                sys.exit(1)
        encrypt_file(Path(args.input), Path(args.output), pw1)
        print(f"\n✓ Encrypted {args.input} -> {args.output}")
        print(f"  Now delete the plaintext file: rm {args.input}")

    elif args.cmd == "decrypt":
        _check_crypto()
        pw = getpass.getpass("Master password: ")
        try:
            raw = decrypt_file(Path(args.input), pw)
            data = json.loads(raw.decode("utf-8"))
            print(json.dumps(data, indent=2))
        except InvalidToken:
            print("ERROR: wrong password or corrupted file")
            sys.exit(1)

    elif args.cmd == "test":
        _check_crypto()
        pw = getpass.getpass("Master password: ")
        try:
            raw = decrypt_file(DEFAULT_ENCRYPTED_PATH, pw)
            data = json.loads(raw.decode("utf-8"))
            print(f"✓ Password correct. {len(data)} credentials loaded.")
            print(f"  Keys: {list(data.keys())}")
        except InvalidToken:
            print("✗ Wrong password")
            sys.exit(1)
        except FileNotFoundError:
            print(f"✗ {DEFAULT_ENCRYPTED_PATH} not found. Run 'encrypt' first.")
            sys.exit(1)
