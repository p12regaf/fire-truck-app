#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
=============================================================================
 SCRIPT DE DESPLIEGUE LOCAL PARA fire-truck-app
=============================================================================
"""

import subprocess
import sys
from pathlib import Path
import os


# --- Configuración ---
TARGET_USER = "cosigein"
BASE_DIR = Path(__file__).resolve().parent
APP_DIR = Path(f"/home/{TARGET_USER}/fire-truck-app")
LOG_DIR = Path(f"/home/{TARGET_USER}/logs")
DATA_DIR = Path(f"/home/{TARGET_USER}/datos")
SERVICES_DIR = BASE_DIR / "services"
# ---------------------

# --- Utilidades ---
def run(cmd, sudo=False, cwd=None, ignore_errors=False):
    if sudo:
        cmd = ["sudo"] + cmd

    print(f">>> {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.stdout:
        print(result.stdout.strip())

    if result.stderr:
        print(result.stderr.strip(), file=sys.stderr)

    if result.returncode != 0 and not ignore_errors:
        sys.exit(result.returncode)

def require_root():
    if os.geteuid() != 0:
        print("Este script debe ejecutarse con sudo")
        sys.exit(1)

# --- Pasos ---
def create_dirs():
    run(["mkdir", "-p", str(APP_DIR), str(LOG_DIR), str(DATA_DIR)], sudo=True)

def update_repo():
    if not (APP_DIR / ".git").exists():
        print("Error: el repositorio debe existir localmente")
        sys.exit(1)

    run(["git", "fetch", "--all", "--prune"], cwd=APP_DIR)
    run(["git", "reset", "--hard", "origin/main"], cwd=APP_DIR)
    run(["git", "clean", "-fdx"], cwd=APP_DIR)

def setup_venv():
    run(["python3", "-m", "venv", ".venv"], cwd=APP_DIR)
    run([str(APP_DIR / ".venv/bin/pip"), "install", "-r", "requirements.txt"], cwd=APP_DIR)

def permissions():
    update_script = APP_DIR / "scripts/check_and_install_update.sh"
    if update_script.exists():
        run(["chmod", "+x", str(update_script)], sudo=True)

    run(
        ["usermod", "-a", "-G", "gpio,i2c,dialout", TARGET_USER],
        sudo=True,
        ignore_errors=True
    )

def sudoers():
    rule = f"{TARGET_USER} ALL=(ALL) NOPASSWD: /sbin/shutdown, /sbin/reboot"
    run(
        ["sh", "-c", f"echo '{rule}' > /etc/sudoers.d/99-fire-truck-app"],
        sudo=True
    )
    run(["chmod", "0440", "/etc/sudoers.d/99-fire-truck-app"], sudo=True)

def can_rtc():
    config_file = Path("/boot/firmware/config.txt")
    if not config_file.exists():
        config_file = Path("/boot/config.txt")

    can_cfg = (
        "\n# fire-truck CAN\n"
        "dtparam=spi=on\n"
        "dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25\n"
    )

    run(
        ["sh", "-c", f"grep -q mcp2515-can0 {config_file} || printf '{can_cfg}' >> {config_file}"],
        sudo=True
    )

    run(["apt-get", "-y", "remove", "fake-hwclock"], sudo=True, ignore_errors=True)

def systemd():
    for svc in ("app.service", "updater.service"):
        src = SERVICES_DIR / svc
        if not src.exists():
            print(f"Servicio no encontrado: {svc}")
            continue

        run(["cp", str(src), f"/etc/systemd/system/{svc}"], sudo=True)

    run(["systemctl", "daemon-reload"], sudo=True)
    run(["systemctl", "enable", "app.service", "updater.service"], sudo=True)
    run(["systemctl", "restart", "app.service", "updater.service"], sudo=True)

# --- Main ---
def main():
    create_dirs()
    update_repo()
    setup_venv()
    permissions()
    sudoers()
    can_rtc()
    systemd()

    print("\n✔ Despliegue local completado")

if __name__ == "__main__":
    main()