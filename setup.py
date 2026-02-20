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
SERVICES = ["updater.service", "app.service"]

REPO_URL = "git@github.com:p12regaf/fire-truck-app.git"
GIT_BRANCH = "main"

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

def ensure_dirs():
    run(["mkdir", "-p", str(APP_DIR), str(LOG_DIR), str(DATA_DIR)], sudo=True)

def fix_dns():
    print(">>> Configurando DNS permanente...")
    resolved_conf = """[Resolve]
DNS=1.1.1.1 8.8.8.8
FallbackDNS=9.9.9.9
"""
    subprocess.run(["sudo", "tee", "/etc/systemd/resolved.conf"], input=resolved_conf.encode(), check=True)
    subprocess.run(["sudo", "systemctl", "restart", "systemd-resolved"], check=True)
    print(">>> DNS configurado correctamente")

def wait_for_network():
    print(">>> Esperando red (DNS)...")
    for _ in range(10):
        result = subprocess.run(["getent", "hosts", "github.com"], stdout=subprocess.DEVNULL)
        if result.returncode == 0:
            print(">>> Red OK")
            return
        time.sleep(3)
    print("ERROR: sin DNS / Internet")
    sys.exit(1)

def ensure_ssh_key_permissions():
    if SSH_KEY.exists():
        print(f">>> Asegurando permisos correctos de {SSH_KEY}")
        run(["chmod", "600", str(SSH_KEY)])
    else:
        print(f"⚠ La clave SSH {SSH_KEY} no existe, Git fallará si es necesaria")

def repo_step():
    parent = APP_DIR.parent
    ensure_ssh_key_permissions()

    # Verifica si .git/index existe y si parece corrupto
    git_index = APP_DIR / ".git" / "index"
    if git_index.exists():
        try:
            subprocess.run(["git", "status"], cwd=APP_DIR, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            print(">>> Índice de Git corrupto, eliminando carpeta completa del repo")
            run(["rm", "-rf", str(APP_DIR)], sudo=True)

    # Asegurarse de que el directorio APP_DIR exista
    APP_DIR.mkdir(parents=True, exist_ok=True)

    if not (APP_DIR / ".git").exists():
        if any(APP_DIR.iterdir()):
            print(">>> Directorio no vacío pero sin .git, limpiando")
            run(["rm", "-rf", str(APP_DIR)], sudo=True)
            APP_DIR.mkdir(parents=True, exist_ok=True)

        print(f">>> Clonando repo {REPO_URL} en {APP_DIR}")
        run(["git", "clone", "-b", GIT_BRANCH, REPO_URL, str(APP_DIR)], cwd=parent)
    else:
        print(">>> Actualizando repositorio")
        run(["git", "fetch", "--all", "--prune"], cwd=APP_DIR)
        run(["git", "reset", "--hard", f"origin/{GIT_BRANCH}"], cwd=APP_DIR)
        run(["git", "clean", "-fdx"], cwd=APP_DIR)
    # Asegurarse de que el directorio APP_DIR exista
    APP_DIR.mkdir(parents=True, exist_ok=True)

    # Crear config.yaml si no existe
    config_dir = APP_DIR / "config"
    config_dir.mkdir(parents=True, exist_ok=True)  # asegurarse de que exista

    config_file = config_dir / "config.yaml"
    config_template = config_dir / "config.yaml.template"

    if not config_file.exists():
        if config_template.exists():
            print(">>> config.yaml no existe, copiando config.yaml.template")
            run(["cp", str(config_template), str(config_file)])
        else:
            print("⚠ No existe config.yaml ni config.yaml.template, se requiere configuración")

def venv_step():
    run(["python3", "-m", "venv", ".venv"], cwd=APP_DIR)
    run([str(APP_DIR / ".venv/bin/pip"), "install", "-r", "requirements.txt"], cwd=APP_DIR)


def install_services():
    for service_name in SERVICES:
        src = APP_DIR / "services" / service_name
        dest = Path("/etc/systemd/system") / service_name
        if not src.exists():
            print(f"⚠ No se encontró {src}, no se instalará el servicio")
            continue
        print(f">>> Instalando/actualizando {service_name}")
        run(["sudo", "cp", str(src), str(dest)])
    
    print(">>> Recargando systemd...")
    run(["sudo", "systemctl", "daemon-reload"])
    
    for service_name in SERVICES:
        print(f">>> Habilitando {service_name} para iniciar al arranque")
        run(["sudo", "systemctl", "enable", service_name])
        # Ya no se hace restart ni status

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