#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
from pathlib import Path
import time
import json
import tarfile
import shutil

TARGET_USER = "cosigein"
APP_DIR = Path(f"/home/{TARGET_USER}/fire-truck-app")
LOG_DIR = Path(f"/home/{TARGET_USER}/logs")
DATA_DIR = Path(f"/home/{TARGET_USER}/datos")
SSH_KEY = Path(f"/home/{TARGET_USER}/.ssh/id_ed25519")
VERSIONS_DIR = Path(f"/home/{TARGET_USER}/versions")
SESSION_HEALTH_FILE = APP_DIR / "session_health.json"
SERVICES = ["updater.service", "app.service"]

REPO_URL = "git@github.com:p12regaf/fire-truck-app.git"
GIT_BRANCH = "Net-Security"

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
    run(["mkdir", "-p", str(APP_DIR), str(LOG_DIR), str(DATA_DIR), str(VERSIONS_DIR)], sudo=True)

RESOLVED_CONF_PATH = Path("/etc/systemd/resolved.conf")
FALLBACK_DNS_LINE = "FallbackDNS=1.1.1.1 8.8.8.8 9.9.9.9"

def fix_dns():
    print(">>> Comprobando resolución de DNS...")
    # Verificar si ya funciona la resolución de nombres
    result = subprocess.run(["getent", "hosts", "github.com"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode == 0:
        print(">>> El DNS ya funciona correctamente. Saltando configuración.")
        return

    print(">>> DNS no funciona. Configurando FallbackDNS de forma quirúrgica...")
    try:
        # Leer el archivo existente para no destruir otras configuraciones
        existing_lines = []
        has_resolve_section = False
        fallback_already_set = False
        modified = False

        if RESOLVED_CONF_PATH.exists():
            existing_lines = RESOLVED_CONF_PATH.read_text().splitlines()

        new_lines = []
        for line in existing_lines:
            stripped = line.strip()
            if stripped == "[Resolve]":
                has_resolve_section = True
            if stripped.startswith("FallbackDNS="):
                if stripped == FALLBACK_DNS_LINE:
                    fallback_already_set = True
                    new_lines.append(line)
                else:
                    # Reemplazar la línea existente con la nuestra
                    new_lines.append(FALLBACK_DNS_LINE)
                    modified = True
                continue
            new_lines.append(line)

        if fallback_already_set:
            print(">>> FallbackDNS ya está configurado correctamente. Solo reiniciando resolved...")
            subprocess.run(["sudo", "systemctl", "restart", "systemd-resolved"], check=True)
            return

        if not modified:
            # No había línea FallbackDNS, hay que añadirla
            if not has_resolve_section:
                new_lines.append("[Resolve]")
            new_lines.append(FALLBACK_DNS_LINE)

        new_content = "\n".join(new_lines) + "\n"
        # Escribir de forma segura usando tee (necesitamos sudo)
        subprocess.run(["sudo", "tee", str(RESOLVED_CONF_PATH)],
                       input=new_content.encode(), stdout=subprocess.DEVNULL, check=True)
        subprocess.run(["sudo", "systemctl", "restart", "systemd-resolved"], check=True)
        print(">>> FallbackDNS configurado correctamente (resto del archivo preservado)")
    except subprocess.CalledProcessError as e:
        print(f"⚠ No se pudo configurar el DNS: {e}")
    except IOError as e:
        print(f"⚠ No se pudo leer {RESOLVED_CONF_PATH}: {e}")

def wait_for_network():
    print(">>> Esperando red (DNS)...")
    # Comprobar una vez inmediatamente
    result = subprocess.run(["getent", "hosts", "github.com"], stdout=subprocess.DEVNULL)
    if result.returncode == 0:
        print(">>> Red OK")
        return

    for i in range(20): # 20 intentos de 1s = 20s total
        time.sleep(1)
        result = subprocess.run(["getent", "hosts", "github.com"], stdout=subprocess.DEVNULL)
        if result.returncode == 0:
            print(f">>> Red OK (detectada en {i+1}s)")
            return
    print("⚠ Advertencia: sin DNS / Internet estable. Continuando setup sin actualizaciones.")
    # No salimos con error para permitir que la app intente arrancar con lo que tenga

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
            print(">>> Índice de Git corrupto. Verificando conexión antes de borrar.")
            # Solo borrar si hay internet para clonar de nuevo
            net_check = subprocess.run(["getent", "hosts", "github.com"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if net_check.returncode == 0:
                print(">>> Internet OK, re-clonando repositorio.")
                run(["rm", "-rf", str(APP_DIR)], sudo=True)
            else:
                print("⚠ Error: Índice corrupto y NO hay internet. Manteniendo archivos actuales como último recurso.")

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
        archive_current_version()
        run(["git", "fetch", "--all", "--prune"], cwd=APP_DIR)
        run(["git", "reset", "--hard", f"origin/{GIT_BRANCH}"], cwd=APP_DIR)
        run(["git", "clean", "-fd"], cwd=APP_DIR)
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
    venv_dir = APP_DIR / ".venv"
    req_file = APP_DIR / "requirements.txt"
    hash_file = venv_dir / ".req_hash"
    
    if not venv_dir.exists():
        run(["python3", "-m", "venv", ".venv"], cwd=APP_DIR)
    
    if req_file.exists():
        import hashlib
        current_hash = hashlib.md5(req_file.read_bytes()).hexdigest()
        
        if hash_file.exists() and hash_file.read_text().strip() == current_hash:
            print(">>> Requisitos de Python ya instalados (hash coincide). Saltando pip install.")
            return
            
        print(">>> Instalando/actualizando dependencias de Python...")
        run([str(APP_DIR / ".venv/bin/pip"), "install", "-r", "requirements.txt"], cwd=APP_DIR)
        hash_file.write_text(current_hash)
    else:
        print(">>> No se encontró requirements.txt. Saltando instalación de dependencias.")


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

def archive_current_version():
    """Comprime la versión actual del código antes de actualizarla."""
    version_file = APP_DIR / ".version"
    if not version_file.exists():
        print("⚠ No se encontró .version, no se puede archivar.")
        return
    
    version = version_file.read_text().strip()
    archive_path = VERSIONS_DIR / "current.tar.gz"
    
    print(f">>> Archivando versión {version} en {archive_path}...")
    try:
        VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, "w:gz") as tar:
            for item in APP_DIR.iterdir():
                # Excluir .venv, .git, config (no forman parte del código desplegable)
                if item.name in ('.venv', '.git', 'config', '__pycache__', 'session_health.json'):
                    continue
                tar.add(str(item), arcname=item.name)
        print(f">>> Versión {version} archivada correctamente.")
    except Exception as e:
        print(f"⚠ No se pudo archivar la versión actual: {e}")

def check_session_health() -> bool:
    """
    Lee session_health.json del último arranque.
    - Si had_internet=True: marca la versión como estable.
    - Si had_internet=False: realiza un rollback a la versión estable.
    Devuelve True si se debe saltar repo_step (tras un rollback).
    """
    if not SESSION_HEALTH_FILE.exists():
        print(">>> No se encontró session_health.json (primera ejecución o crash). Continuando normal.")
        return False
    
    try:
        with open(SESSION_HEALTH_FILE, 'r') as f:
            health = json.load(f)
        # Limpiar el archivo para el siguiente ciclo
        SESSION_HEALTH_FILE.unlink(missing_ok=True)
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠ No se pudo leer session_health.json: {e}")
        return False
    
    had_internet = health.get("had_internet", False)
    version = health.get("version", "desconocida")
    
    current_archive = VERSIONS_DIR / "current.tar.gz"
    stable_archive = VERSIONS_DIR / "stable.tar.gz"
    
    if had_internet:
        print(f">>> Última sesión (v{version}) tuvo internet. Marcando como ESTABLE.")
        if current_archive.exists():
            try:
                shutil.copy2(str(current_archive), str(stable_archive))
                print(f">>> Versión estable actualizada: {stable_archive}")
            except IOError as e:
                print(f"⚠ No se pudo copiar a stable.tar.gz: {e}")
        else:
            print(">>> No hay current.tar.gz para marcar como estable (primera ejecución).")
        return False
    else:
        print(f"⚠ Última sesión (v{version}) NO tuvo internet. Intentando ROLLBACK...")
        return rollback_to_stable()

def rollback_to_stable() -> bool:
    """
    Extrae stable.tar.gz sobre el directorio de la app.
    Devuelve True si el rollback se realizó (y se debe saltar repo_step).
    """
    stable_archive = VERSIONS_DIR / "stable.tar.gz"
    
    if not stable_archive.exists():
        print("⚠ No hay versión estable guardada. No se puede hacer rollback.")
        return False
    
    try:
        print(f">>> Extrayendo versión estable desde {stable_archive}...")
        # Limpiar archivos de código actuales (preservar .venv, .git, config)
        for item in APP_DIR.iterdir():
            if item.name in ('.venv', '.git', 'config', '__pycache__'):
                continue
            if item.is_dir():
                shutil.rmtree(str(item))
            else:
                item.unlink()
        
        # Extraer el archivo estable
        with tarfile.open(stable_archive, "r:gz") as tar:
            tar.extractall(path=str(APP_DIR))
        
        print(">>> ¡ROLLBACK completado! Saltando actualización de Git.")
        return True
    except Exception as e:
        print(f"⚠ Error durante el rollback: {e}")
        return False

def main():
    ensure_dirs()
    fix_dns()
    wait_for_network()
    skip_update = check_session_health()
    if not skip_update:
        repo_step()
    install_services()
    venv_step()
    print("\n✔ Setup completado correctamente")

if __name__ == "__main__":
    main()