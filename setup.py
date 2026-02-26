#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
from pathlib import Path
import time
import json
import shutil
import tarfile
import yaml
import ftplib
import socket
import re

TARGET_USER = "cosigein"
APP_DIR = Path(f"/home/{TARGET_USER}/fire-truck-app")
LOG_DIR = Path(f"/home/{TARGET_USER}/logs")
DATA_DIR = Path(f"/home/{TARGET_USER}/datos")
SSH_KEY = Path(f"/home/{TARGET_USER}/.ssh/id_ed25519")
VERSIONS_DIR = Path(f"/home/{TARGET_USER}/versions")
SESSION_HEALTH_FILE = APP_DIR / "session_health.json"
SERVICES = ["updater.service", "app.service"]
UPDATE_STATE_FILE = Path(f"/home/{TARGET_USER}/update_state.json")
BACKUP_FILE = Path(f"/home/{TARGET_USER}/fire-truck-app_stable_backup.tar.gz")

REPO_URL = "git@github.com:p12regaf/fire-truck-app.git"
GIT_BRANCH = "main"

def run(cmd, sudo=False, cwd=None, ignore_errors=False):
    if sudo:
        cmd = ["sudo"] + cmd

    print(f"\n>>> Ejecutando: {' '.join(cmd)}")
    if cwd:
        print(f">>> En directorio: {cwd}")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    print(f"stdout:\n{result.stdout.strip()}")
    print(f"stderr:\n{result.stderr.strip()}")
    print(f"returncode: {result.returncode}")

    if result.returncode != 0 and not ignore_errors:
        print(f"⚠ Comando falló: {' '.join(cmd)}")
        return result.returncode
    return 0

def create_local_snapshot():
    print("\n>>> Creando snapshot local (backup) del repo")
    if not (APP_DIR / ".git").exists():
        print("⚠ No existe .git, se omite snapshot")
        return
    try:
        def exclude_files(tarinfo):
            if ".venv" in tarinfo.name or "__pycache__" in tarinfo.name or ".git" in tarinfo.name:
                return None
            return tarinfo

        with tarfile.open(BACKUP_FILE, "w:gz") as tar:
            tar.add(str(APP_DIR), arcname=APP_DIR.name, filter=exclude_files)
        print(f">>> Snapshot creado en {BACKUP_FILE}")
    except Exception as e:
        print(f"⚠ Error al crear snapshot: {e}")

def restore_local_snapshot():
    print("\n>>> Restaurando snapshot local")
    if not BACKUP_FILE.exists():
        print("⚠ No hay snapshot local")
        return False
    try:
        old_dir = APP_DIR.with_suffix(".failed")
        if old_dir.exists():
            shutil.rmtree(old_dir)
        APP_DIR.rename(old_dir)
        with tarfile.open(BACKUP_FILE, "r:gz") as tar:
            tar.extractall(path=APP_DIR.parent)
        print(">>> Snapshot restaurado con éxito")
        return True
    except Exception as e:
        print(f"⚠ Error restaurando snapshot: {e}")
        return False

def ensure_dirs():
    # Solo creamos los directorios si no existen.
    dirs_to_ensure = [str(APP_DIR), str(LOG_DIR), str(DATA_DIR), str(VERSIONS_DIR)]
    res = run(["sudo", "mkdir", "-p"] + dirs_to_ensure)
    
    if res == 0:
        # Optimización: En lugar de chown -R /home/cosigein (lento), 
        # solo aseguramos permisos en las rutas críticas del sistema.
        print(">>> Asegurando permisos en directorios críticos...")
        run(["sudo", "chown", "-R", f"{TARGET_USER}:{TARGET_USER}", str(APP_DIR)])
        run(["sudo", "chown", "-R", f"{TARGET_USER}:{TARGET_USER}", str(LOG_DIR)])
        run(["sudo", "chown", "-R", f"{TARGET_USER}:{TARGET_USER}", str(DATA_DIR)])
        run(["sudo", "chown", "-R", f"{TARGET_USER}:{TARGET_USER}", str(VERSIONS_DIR)])
    return res

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
    for i in range(10):
        result = subprocess.run(["getent", "hosts", "github.com"], stdout=subprocess.DEVNULL)
        if result.returncode == 0:
            print(f">>> Red OK (detectada en {(i+1)*3}s)")
            return
        time.sleep(3)
    print("ERROR: sin DNS / Internet")
    sys.exit(1)

def ensure_ssh_key_permissions():
    print("\n>>> Verificando permisos de la clave SSH")
    if SSH_KEY.exists():
        run(["chmod", "600", str(SSH_KEY)])
    else:
        print(f"⚠ Clave SSH {SSH_KEY} no encontrada")

def get_current_commit():
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=APP_DIR, capture_output=True, text=True)
    commit = result.stdout.strip() if result.returncode == 0 else None
    print(f">>> Commit actual: {commit}")
    return commit

def load_update_state():
    if UPDATE_STATE_FILE.exists():
        try:
            with open(UPDATE_STATE_FILE, 'r') as f:
                state = json.load(f)
                print(f">>> Estado de actualización cargado: {state}")
                return state
        except Exception as e:
            print(f"⚠ Error leyendo {UPDATE_STATE_FILE}: {e}")
    return {"last_stable_commit": None, "pending_commit": None}

def save_update_state(state):
    print(f">>> Guardando estado de actualización: {state}")
    with open(UPDATE_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def repo_step():
    print("\n>>> Paso de repositorio (Git)")
    parent = APP_DIR.parent
    ensure_ssh_key_permissions()

    # Asegurar permisos inmediatamente al entrar en repo_step
    run(["sudo", "chown", "-R", f"{TARGET_USER}:{TARGET_USER}", str(APP_DIR)])

    # Solo esperamos la red si realmente vamos a intentar interactuar con GitHub
    wait_for_network()

    # Verifica si .git/index existe y si parece corrupto
    git_index = APP_DIR / ".git" / "index"
    if git_index.exists():
        try:
            subprocess.run(["git", "status"], cwd=APP_DIR, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            print(">>> Índice de Git corrupto, limpiando archivos del repo (preservando archivos críticos)")
            # Eliminar todo excepto lo que queremos mantener
            for item in APP_DIR.iterdir():
                if item.name not in ('.venv', 'config', 'session_health.json', 'setup.py'):
                    run(["sudo", "rm", "-rf", str(item)])

    # Asegurarse de que el directorio APP_DIR exista
    APP_DIR.mkdir(parents=True, exist_ok=True)

    if not (APP_DIR / ".git").exists():
        # Si hay archivos pero no hay .git, limpiar para poder clonar (sin borrar config/setup)
        if any(APP_DIR.iterdir()):
            print(">>> El directorio no es un repo git, limpiando para clonar (preservando archivos críticos)")
            for item in APP_DIR.iterdir():
                if item.name not in ('.venv', 'config', 'session_health.json', 'setup.py'):
                    run(["sudo", "rm", "-rf", str(item)])
        
        print(f">>> Inicializando y vinculando repo {REPO_URL}")
        if run(["git", "init"], cwd=APP_DIR) != 0: return 1
        if run(["git", "remote", "add", "origin", REPO_URL], cwd=APP_DIR) != 0: return 1
    
    print(">>> Actualizando repositorio")
    if run(["git", "fetch", "--all", "--prune"], cwd=APP_DIR) != 0: return 1
    if run(["git", "reset", "--hard", f"origin/{GIT_BRANCH}"], cwd=APP_DIR) != 0: return 1
    if run(["git", "clean", "-fd"], cwd=APP_DIR) != 0: return 1
    return 0

    # Crear config.yaml si no existe
    config_dir = APP_DIR / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"
    config_template = config_dir / "config.yaml.template"
    if not config_file.exists():
        if config_template.exists():
            print(">>> Copiando config.yaml.template a config.yaml")
            run(["cp", str(config_template), str(config_file)])
        else:
            print("⚠ No existe config.yaml ni template")

def venv_step():
    venv_dir = APP_DIR / ".venv"
    req_file = APP_DIR / "requirements.txt"
    timestamp_file = venv_dir / ".last_install"
    pip_bin = venv_dir / "bin/pip"
    python_bin = venv_dir / "bin/python3"
    
    # Si no existe .venv o falta el binario de python, lo creamos de cero
    if not venv_dir.exists() or not python_bin.exists():
        print(">>> Creando entorno virtual...")
        if venv_dir.exists():
            run(["sudo", "rm", "-rf", str(venv_dir)])
        if run(["python3", "-m", "venv", ".venv"], cwd=APP_DIR) != 0: return 1
    
    # Solo corremos pip si requirements.txt es más nuevo que nuestro último timestamp
    needs_install = True
    if timestamp_file.exists() and req_file.exists() and pip_bin.exists():
        if timestamp_file.stat().st_mtime > req_file.stat().st_mtime:
            needs_install = False
            print(">>> Requerimientos al día, saltando pip install.")

    if needs_install:
        print(">>> Instalando requerimientos con pip...")
        if run([str(pip_bin), "install", "-r", "requirements.txt"], cwd=APP_DIR) != 0:
            return 1
        timestamp_file.touch()
    return 0

def install_services():
    print("\n>>> Instalando servicios systemd")
    for service_name in SERVICES:
        src = APP_DIR / "services" / service_name
        dest = Path("/etc/systemd/system") / service_name
        if not src.exists():
            print(f"⚠ No se encontró {src}")
            continue
        print(f">>> Copiando {service_name}")
        run(["sudo", "cp", str(src), str(dest)])
    print(">>> Recargando systemd")
    run(["sudo", "systemctl", "daemon-reload"])
    for service_name in SERVICES:
        print(f">>> Habilitando {service_name} al arranque")
        run(["sudo", "systemctl", "enable", service_name])

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

def upload_historical_logs():
    """
    Escanea el directorio de logs en busca de archivos daily_YYYY-MM-DD.log.
    Sube todos los que no correspondan al día de hoy.
    """
    print("\n>>> Iniciando subida de logs históricos a FTP...")
    config_file = APP_DIR / "config" / "config.yaml"
    if not config_file.exists():
        print("⚠ No se encontró config.yaml, saltando subida de logs.")
        return

    try:
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"⚠ Error leyendo config.yaml: {e}")
        return

    ftp_cfg = config.get('ftp', {})
    if not ftp_cfg.get('enabled', False):
        print(">>> FTP deshabilitado en config.yaml.")
        return

    log_dir = Path(config.get('paths', {}).get('app_logs', str(LOG_DIR)))
    if not log_dir.exists():
        print(f">>> El directorio de logs {log_dir} no existe.")
        return

    # Preparar lista de archivos a subir
    today_str = datetime.now().strftime('%Y-%m-%d')
    files_to_upload = []
    for f in log_dir.glob("daily_*.log"):
        # Extraer fecha del nombre: daily_2026-02-26.log
        match = re.search(r'daily_(\d{4}-\d{2}-\d{2})\.log', f.name)
        if match:
            file_date = match.group(1)
            if file_date < today_str:
                files_to_upload.append(f)

    if not files_to_upload:
        print(">>> No hay logs históricos para subir.")
        return

    # Intentar conexión FTP
    print(f">>> Conectando a FTP {ftp_cfg.get('host')}...")
    try:
        ftp = ftplib.FTP()
        ftp.connect(ftp_cfg['host'], ftp_cfg['port'], timeout=20)
        ftp.login(ftp_cfg['user'], ftp_cfg['pass'])
        
        # Navegar a datos_doback/device_name/logs
        # El nombre del dispositivo suele estar en config o se deduce
        # Para setup.py, intentaremos obtenerlo de un archivo de estado o usar un default si no está en config
        device_name = config.get('system', {}).get('device_name', 'unknown_device').lower()
        
        for base_dir in ["datos_doback", device_name, "logs"]:
            try:
                if base_dir not in ftp.nlst():
                    ftp.mkd(base_dir)
                ftp.cwd(base_dir)
            except Exception:
                # Si falla nlst o mkd, intentamos cwd directamente
                ftp.cwd(base_dir)

        for log_file in files_to_upload:
            print(f"  -> Subiendo {log_file.name}...")
            with open(log_file, 'rb') as f:
                ftp.storbinary(f'STOR {log_file.name}', f)
            log_file.unlink()
            print(f"  ✔ {log_file.name} subido y eliminado.")

        ftp.quit()
        print(">>> Subida de logs históricos completada.")
    except Exception as e:
        print(f"⚠ Error durante la subida FTP: {e}")

def fixes():
    run(["stty", "-F", "/dev/serial1", "115200", "raw", "-echo"],ignore_errors=True)
def main():
    print("\n=== INICIO DEL SETUP ===")
    if ensure_dirs() != 0:
        sys.exit(1)
    
    # Quitamos fix_dns y wait_for_network del flujo principal bloqueante.
    # Se ejecutarán solo cuando sea necesario dentro de repo_step.
    
    # Comprobar salud de la sesión anterior y realizar rollback si es necesario
    skip_repo = check_session_health()
    
    if not skip_repo:
        archive_current_version()
        if repo_step() != 0:
            print("⚠ Error crítico en repo_step. Abortando setup.")
            sys.exit(1)
    
    install_services()
    if venv_step() != 0:
        print("⚠ Error crítico en venv_step. Abortando setup.")
        sys.exit(1)
    
    create_local_snapshot()
    fixes()
    upload_historical_logs()
    print("\n✔ Setup completado correctamente (verbose)")

if __name__ == "__main__":
    main()