#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
from pathlib import Path
import time
import json
import shutil
import tarfile

TARGET_USER = "cosigein"
APP_DIR = Path(f"/home/{TARGET_USER}/fire-truck-app")
LOG_DIR = Path(f"/home/{TARGET_USER}/logs")
DATA_DIR = Path(f"/home/{TARGET_USER}/datos")
SSH_KEY = Path(f"/home/{TARGET_USER}/.ssh/id_ed25519")
SERVICES = ["updater.service", "app.service"]
UPDATE_STATE_FILE = Path(f"/home/{TARGET_USER}/update_state.json")
BACKUP_FILE = Path(f"/home/{TARGET_USER}/fire-truck-app_stable_backup.tar.gz")

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
        return result.returncode
    return 0

def create_local_snapshot():
    """Crea un archivo comprimido del repositorio actual como respaldo local."""
    if not (APP_DIR / ".git").exists():
        return
    
    print(f">>> Creando snapshot local en {BACKUP_FILE}...")
    try:
        # Excluimos .venv y otros archivos temporales para ahorrar espacio
        def exclude_files(tarinfo):
            if ".venv" in tarinfo.name or "__pycache__" in tarinfo.name or ".git" in tarinfo.name:
                return None
            return tarinfo

        with tarfile.open(BACKUP_FILE, "w:gz") as tar:
            tar.add(str(APP_DIR), arcname=APP_DIR.name, filter=exclude_files)
        print(">>> Snapshot local creado con éxito.")
    except Exception as e:
        print(f"⚠ No se pudo crear el snapshot local: {e}")

def restore_local_snapshot():
    """Restaura el repositorio desde el snapshot local."""
    if not BACKUP_FILE.exists():
        print("ERROR: No hay snapshot local disponible para restaurar.")
        return False
    
    print(f">>> [OFFLINE ROLLBACK] Restaurando desde {BACKUP_FILE}...")
    try:
        # Limpiar directorio actual (excepto .git si existe y no queremos perder historial)
        # Pero en un rollback total offline, a veces es mejor limpiar todo.
        # Por seguridad, movemos lo actual a un .old
        old_dir = APP_DIR.with_suffix(".failed")
        if old_dir.exists():
            shutil.rmtree(old_dir)
        
        APP_DIR.rename(old_dir)
        
        with tarfile.open(BACKUP_FILE, "r:gz") as tar:
            tar.extractall(path=APP_DIR.parent)
        
        print(">>> [OFFLINE ROLLBACK] Restauración completada.")
        return True
    except Exception as e:
        print(f"ERROR: Falló la restauración del snapshot local: {e}")
        return False

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

def get_current_commit():
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=APP_DIR, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None

def load_update_state():
    if UPDATE_STATE_FILE.exists():
        try:
            with open(UPDATE_STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_stable_commit": None, "pending_commit": None}

def save_update_state(state):
    with open(UPDATE_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def repo_step():
    parent = APP_DIR.parent
    ensure_ssh_key_permissions()
    
    state = load_update_state()
    current_commit = get_current_commit()

    # --- Lógica de Rollback ---
    # Casos para rollback:
    # 1. Hay un pending_commit y coincide con el actual (falló el arranque anterior).
    # 2. El estado explícitamente dice is_stable: False (falló la salud de la sesión anterior).
    is_unstable = state.get("is_stable") == False
    is_pending_failed = state.get("pending_commit") and state["pending_commit"] == current_commit

    if is_unstable or is_pending_failed:
        reason = state.get("instability_reason", "Desconocido")
        print(f">>> [ROLLBACK] Reversión necesaria. Motivo: {reason}")
        
        rollback_success = False
        
        # 1. Intentar siempre la restauración local primero (más seguro y predecible)
        print(">>> [ROLLBACK] Restaurando desde snapshot local...")
        rollback_success = restore_local_snapshot()

        # 2. Solo si falla el snapshot local, intentamos Git como último recurso
        if not rollback_success and state.get("last_stable_commit"):
            print(f">>> [ROLLBACK] Snapshot falló. Intentando revertir via Git a {state['last_stable_commit'][:8]}...")
            if run(["git", "reset", "--hard", state["last_stable_commit"]], cwd=APP_DIR, ignore_errors=True) == 0:
                run(["git", "clean", "-fdx"], cwd=APP_DIR)
                rollback_success = True

        if rollback_success:
            state["pending_commit"] = None
            # IMPORTANTE: Después de un rollback exitoso, marcamos como estable el estado inicial 
            # para evitar bucles si el snapshot restaurado es antiguo pero funcional.
            state["is_stable"] = True 
            save_update_state(state)
            print(">>> [ROLLBACK] Éxito. Saltando actualización para este arranque.")
            return
        else:
            print(">>> [ROLLBACK] ERROR CRÍTICO: No se pudo revertir la aplicación.")

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
        
        # Antes de actualizar, guardamos el actual como "stable" (si no había pending)
        if not state.get("pending_commit"):
            state["last_stable_commit"] = current_commit

        # Verificamos si hay cambios
        result = subprocess.run(["git", "rev-parse", f"origin/{GIT_BRANCH}"], cwd=APP_DIR, capture_output=True, text=True)
        new_commit = result.stdout.strip()

        if new_commit != current_commit:
            print(f">>> Nueva versión detectada: {new_commit[:8]}")
            run(["git", "reset", "--hard", f"origin/{GIT_BRANCH}"], cwd=APP_DIR)
            run(["git", "clean", "-fdx"], cwd=APP_DIR)
            state["pending_commit"] = new_commit
            save_update_state(state)
        else:
            print(">>> El repositorio ya está actualizado.")

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
    if ensure_dirs() != 0: sys.exit(1)
    fix_dns()
    
    # Intentar esperar red, pero no morir si no hay (para permitir rollback local)
    try:
        wait_for_network()
        network_ok = True
    except SystemExit:
        network_ok = False
        print("⚠ Continuando sin red (modo offline/rollback local)...")

    repo_step()
    install_services()
    venv_step()
    
    # Si todo fue bien y el repo está listo, creamos snapshot si no existe o ha cambiado
    # (Podríamos hacerlo solo si network_ok era True, indicando que acabamos de actualizar)
    if network_ok:
        create_local_snapshot()
        
    print("\n✔ Setup completado correctamente")

if __name__ == "__main__":
    main()