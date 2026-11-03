#!/usr/bin/env python3
"""Reset this machine's single-user web password and require a change at login."""
import argparse
import os
from pathlib import Path
import json
import secrets
import subprocess

from auth import AuthStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", required=True,
                        help="Invalidate the current password and restart the user service")
    parser.parse_args()
    os.umask(0o077)
    auth = AuthStore(Path.home() / ".config/omniget-web")
    password = secrets.token_urlsafe(18)
    with auth.lock:
        AuthStore.write_private(auth.initial_path, password + "\n")
        AuthStore.write_private(auth.path, json.dumps(auth.password_record(password, must_change=True)))
    subprocess.run(["systemctl", "--user", "restart", "omniget-web"], check=True)
    print(f"Contraseña restablecida. Consulta la temporal en {auth.initial_path}")


if __name__ == "__main__":
    main()
