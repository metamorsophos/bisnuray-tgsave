"""Utility script to generate a fresh Pyrogram/Pyrofork StringSession.

Usage (interactive):
    python generate_session.py

Steps performed:
 1. Loads API_ID / API_HASH from environment (config.env or .env) if present.
 2. Prompts for any missing values.
 3. Opens an in-memory Pyrogram client and guides you through login
    (phone number, code, and 2FA password if enabled).
 4. Prints the resulting SESSION_STRING.
 5. Optionally updates/creates .env (and config.env if it exists) by
    replacing/adding SESSION_STRING=... (no quotes, single line).

After generation, copy the SESSION_STRING into your deployment secrets
or leave it in your local .env. Never share it publicly.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Tuple

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = lambda *a, **k: None  # type: ignore

# Load both potential env files early (order: config.env then .env so .env overrides)
load_dotenv("config.env")
load_dotenv()

def prompt_api_credentials() -> Tuple[int, str]:
    api_id_env = os.getenv("API_ID")
    api_hash_env = os.getenv("API_HASH")
    while True:
        if api_id_env is None:
            api_id_str = input("API_ID (integer): ").strip()
        else:
            api_id_str = api_id_env.strip()
            print(f"Using API_ID from env: {api_id_str}")
        try:
            api_id_val = int(api_id_str)
        except ValueError:
            print("API_ID must be an integer.")
            api_id_env = None
            continue
        break
    if api_hash_env is None:
        api_hash_val = input("API_HASH: ").strip()
    else:
        api_hash_val = api_hash_env.strip()
        print(f"Using API_HASH from env: {api_hash_val}")
    if not api_hash_val or len(api_hash_val) < 30:
        print("Warning: API_HASH length looks suspicious. Make sure it's correct.")
    return api_id_val, api_hash_val

def generate_session(api_id: int, api_hash: str) -> str:
    """Login and return the exported StringSession."""
    try:
        from pyrogram import Client  # Pyrofork maintains pyrogram namespace
        from pyrogram.session import StringSession
    except ModuleNotFoundError as e:  # pragma: no cover
        print("[ERROR] pyrogram (Pyrofork) module not found for this interpreter:")
        print(f"        Interpreter: {sys.executable}")
        print("        Run (PowerShell):")
        print("        python -m pip install -r requirements.txt")
        raise SystemExit(1) from e
    except Exception as e:  # pragma: no cover
        import traceback
        print("[ERROR] Importing pyrogram failed due to an internal error (not just missing package).")
        print(f"        Interpreter: {sys.executable}")
        traceback.print_exc()
        print("Hint: ensure package versions support your Python version. Try upgrading Pyrofork:")
        print("       python -m pip install --upgrade Pyrofork")
        raise SystemExit(1) from e
    print("\nStarting temporary client to generate session...")
    with Client(StringSession(), api_id=api_id, api_hash=api_hash) as app:
        me = app.get_me()
        print(f"Logged in as: {me.first_name} (id={me.id})")
        session_string = app.export_session_string()
    return session_string

def update_env_files(session_string: str) -> None:
    """Insert or replace SESSION_STRING=... in .env and config.env (if present)."""
    for filename in (".env", "config.env"):
        path = Path(filename)
        if not path.exists() and filename == "config.env":
            continue
        lines = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
        key = "SESSION_STRING"
        replaced = False
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = f"{key}={session_string}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={session_string}")
        text = "\n".join(lines) + "\n"
        path.write_text(text, encoding="utf-8")
        print(f"Updated {filename} with new SESSION_STRING.")

def validate_session_string(s: str) -> None:
    import base64
    stripped = s.strip().strip('"').strip("'")
    core_len = len(stripped.rstrip('='))
    if core_len % 4 == 1:
        raise ValueError("Generated SESSION_STRING length invalid (mod 4 == 1). Retry generation.")
    padded = stripped + "=" * ((4 - len(stripped) % 4) % 4)
    try:
        blob = base64.urlsafe_b64decode(padded.encode())
    except Exception as e:  # pragma: no cover
        raise ValueError(f"Generated SESSION_STRING failed base64 decode: {e}") from e
    if len(blob) < 200:
        raise ValueError("Generated SESSION_STRING decoded payload unexpectedly small (<200 bytes). Retry.")

def main() -> int:
    print("=== Pyrogram / Pyrofork StringSession Generator ===")
    api_id, api_hash = prompt_api_credentials()
    try:
        session = generate_session(api_id, api_hash)
    except Exception as e:
        print(f"Failed to generate session: {e}")
        return 1
    try:
        validate_session_string(session)
    except Exception as e:
        print(f"Warning: validation raised an issue: {e}")
    print("\nSESSION_STRING (copy everything between the lines):\n" + "-" * 60)
    print(session)
    print("-" * 60)
    if os.getenv("SESSION_STRING"):
        print("(Previous SESSION_STRING detected in environment; this will replace it if you choose to write.)")
    choice = input("Write this SESSION_STRING to .env (and existing config.env) [y/N]? ").strip().lower()
    if choice.startswith("y"):
        update_env_files(session)
    else:
        print("Skipped writing to env files.")
    print("Done. Update any deployment secrets manually if needed.")
    return 0

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
