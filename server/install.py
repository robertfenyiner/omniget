#!/usr/bin/env python3
"""Install the already-built CLI and a user systemd web service, without sudo."""
import argparse
import ipaddress
import os
from pathlib import Path
import shutil
import subprocess
import sys

from auth import AuthStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1", help="Loopback or a private Tailscale IPv4 address")
    args = parser.parse_args()
    address = ipaddress.ip_address(args.bind)
    if address.version != 4 or not (address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10")):
        parser.error("Usa loopback o una dirección IPv4 de Tailscale.")
    os.umask(0o077)
    root = Path(__file__).resolve().parent.parent
    if any(char.isspace() or char in '%"\\' for char in str(root) + str(Path.home())):
        raise SystemExit("Esta instalación requiere rutas de proyecto y HOME sin espacios, comillas, barras inversas ni %.")
    source = root / "src-tauri/target/debug/omniget-cli"
    if not source.is_file():
        raise SystemExit("Compila primero: cargo build --locked --manifest-path src-tauri/Cargo.toml -p omniget-cli")
    binaries = Path.home() / ".local/bin"
    binaries.mkdir(parents=True, exist_ok=True)
    cli = binaries / "omniget-cli"
    if cli.exists() and cli.read_bytes() != source.read_bytes():
        raise SystemExit("Ya existe otra CLI en ~/.local/bin/omniget-cli. Consérvala o muévela antes de instalar.")
    if not cli.exists():
        shutil.copy2(source, cli)
        cli.chmod(0o700)
    config = Path.home() / ".config/omniget-web"
    config.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = config / "environment"
    if not env.exists():
        env.write_text(f"OMNIGET_WEB_BIND={args.bind}\nOMNIGET_WEB_PORT=8790\nOMNIGET_CLI={cli}\nPATH={binaries}:/usr/local/bin:/usr/bin:/bin\n")
        env.chmod(0o600)
    else:
        print("Se conserva la configuración existente; --bind solo se aplica en la primera instalación.")
        # Retire the first version's bearer, including its on-disk copy.
        clean = "\n".join(line for line in env.read_text().splitlines() if not line.startswith("OMNIGET_WEB_TOKEN=")) + "\n"
        AuthStore.write_private(env, clean)
    auth = AuthStore(config)
    units = Path.home() / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    unit = units / "omniget-web.service"
    unit.write_text(f'''[Unit]
Description=OmniGet private web download queue
After=network-online.target

[Service]
Type=simple
EnvironmentFile={env}
ExecStart="{sys.executable}" "{root / 'server/app.py'}"
WorkingDirectory={root}
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
RestrictSUIDSGID=true
KillMode=control-group
MemoryMax=3G
TasksMax=128
LimitFSIZE=21474836480

[Install]
WantedBy=default.target
''')
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemd-analyze", "--user", "verify", str(unit)], check=True)
    subprocess.run(["systemctl", "--user", "enable", "omniget-web.service"], check=True)
    subprocess.run(["systemctl", "--user", "restart", "omniget-web.service"], check=True)
    subprocess.run(["systemctl", "--user", "is-active", "--quiet", "omniget-web.service"], check=True)
    print(f"Servicio instalado. Configuración privada: {env}")
    if auth.initial_path.exists():
        print(f"Contraseña temporal de primer acceso: {auth.initial_path}")
    print(f"Dirección configurada en la primera instalación: http://{args.bind}:8790")


if __name__ == "__main__":
    main()
