#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
from pathlib import Path
import time

TARGET_USER = "cosigein"
APP_DIR = Path(f"/home/{TARGET_USER}/fire-truck-app")
LOG_DIR = Path(f"/home/{TARGET_USER}/logs")
DATA_DIR = Path(f"/home/{TARGET_USER}/datos")
SSH_KEY = Path(f"/home/{TARGET_USER}/.ssh/id_ed25519")
SERVICES = ["update.service", "app.service"]



REPO_URL = "git@github.com:p12regaf/fire-truck-app.git"
GIT_BRANCH = "main"

def run(cmd, sudo=False, cwd=None, ignore_errors=False):
    """Ejecuta un comando en la placa, con sudo opcional."""
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

def ensure_dirs():
    """Crea directorios necesarios."""
    run(["mkdir", "-p", str(APP_DIR), str(LOG_DIR), str(DATA_DIR)], sudo=True)

def fix_dns():
    """Configura DNS permanente para evitar errores de Git."""
    print(">>> Configurando DNS permanente...")
    resolved_conf = """[Resolve]
DNS=1.1.1.1 8.8.8.8
FallbackDNS=9.9.9.9
"""
    subprocess.run(
        ["sudo", "tee", "/etc/systemd/resolved.conf"],
        input=resolved_conf.encode(),
        check=True
    )
    subprocess.run(["sudo", "systemctl", "restart", "systemd-resolved"], check=True)
    print(">>> DNS configurado correctamente")

def wait_for_network():
    """Espera a que haya resolución DNS."""
    print(">>> Esperando red (DNS)...")
    for _ in range(10):
        result = subprocess.run(
            ["getent", "hosts", "github.com"],
            stdout=subprocess.DEVNULL
        )
        if result.returncode == 0:
            print(">>> Red OK")
            return
        time.sleep(3)
    print("ERROR: sin DNS / Internet")
    sys.exit(1)

def ensure_ssh_key_permissions():
    """Asegura que las claves SSH tengan permisos correctos."""
    if SSH_KEY.exists():
        print(f">>> Asegurando permisos correctos de {SSH_KEY}")
        run(["chmod", "600", str(SSH_KEY)])
    else:
        print(f"⚠ La clave SSH {SSH_KEY} no existe, Git fallará si es necesaria")

def repo_step():
    """Clona o actualiza el repositorio."""
    parent = APP_DIR.parent

    # Permisos correctos antes de usar Git
    ensure_ssh_key_permissions()

    if not (APP_DIR / ".git").exists():
        if any(APP_DIR.iterdir()):
            print(">>> Directorio no vacío pero sin .git, limpiando")
            run(["rm", "-rf", str(APP_DIR)], sudo=True)

        print(f">>> Clonando repo {REPO_URL} en {APP_DIR}")
        run(
            ["git", "clone", "-b", GIT_BRANCH, REPO_URL, str(APP_DIR)],
            cwd=parent
        )
    else:
        print(">>> Actualizando repositorio")
        run(["git", "fetch", "--all", "--prune"], cwd=APP_DIR)
        run(["git", "reset", "--hard", f"origin/{GIT_BRANCH}"], cwd=APP_DIR)
        run(["git", "clean", "-fdx"], cwd=APP_DIR)

def venv_step():
    """Crea virtualenv e instala dependencias."""
    run(["python3", "-m", "venv", ".venv"], cwd=APP_DIR)
    run([str(APP_DIR / ".venv/bin/pip"), "install", "-r", "requirements.txt"], cwd=APP_DIR)

def install_services():
    for service_name in SERVICES:
        src = APP_DIR / "services" / service_name
        dest = Path("/etc/systemd/system") / service_name
        if not src.exists():
            print(f"⚠ No se encontró {src}, no se instalará el servicio")
            continue
        if not dest.exists():
            print(f">>> Instalando {service_name}")
            run(["sudo", "cp", str(src), str(dest)])
        else:
            print(f">>> {service_name} ya existe, actualizando...")
            run(["sudo", "cp", str(src), str(dest)])
    # Recarga y habilita
    print(">>> Recargando systemd...")
    run(["sudo", "systemctl", "daemon-reload"])
    for service_name in SERVICES:
        print(f">>> Habilitando y arrancando {service_name}")
        run(["sudo", "systemctl", "enable", service_name])
        run(["sudo", "systemctl", "restart", service_name])
        run(["systemctl", "status", service_name], ignore_errors=True)

def main():
    ensure_dirs()
    fix_dns()
    wait_for_network()
    repo_step()
    install_services()

    venv_step()
    print("\n✔ Setup completado correctamente")

if __name__ == "__main__":
    main()