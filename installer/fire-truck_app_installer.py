#!/usr/bin/env python3

# =============================================================================
#  INSTALADOR AUTOMÁTICO PARA fire-truck-app (VERSIÓN PYTHON)
# =============================================================================
#
#  Este script realiza una instalación completa en una Raspberry Pi:
#  0. Habilita una señal de alimentación en GPIO 12 (3.3V) usando RPi.GPIO.
#  1. Comprueba que se ejecuta como root.
#  2. Pide los datos necesarios (URL del repo, rama).
#  3. Actualiza el sistema e instala todas las dependencias.
#  4. Crea el usuario 'cosigein' si no existe.
#  5. Instala la Deploy Key de Git que debe estar junto a este script.
#  6. Configura /boot/firmware/config.txt para habilitar todo el hardware.
#  7. Clona el repositorio de la aplicación.
#  8. Configura el entorno virtual y las dependencias de Python.
#  9. Establece todos los permisos de sistema y de usuario.
#  10. Instala y habilita los servicios systemd.
#
#  USO:
#  1. Copia este archivo y tu clave privada (renombrada a 'deploy_key') a la RPi.
#  2. Hazlo ejecutable: chmod +x fire_truck_app_installer.py
#  3. Ejecútalo con sudo: sudo python3 ./fire_truck_app_installer.py
#
# =============================================================================

import os
import sys
import subprocess
import shutil
import pwd
import stat

# --- Variables de Configuración ---
TARGET_USER = "cosigein"
HOME_DIR = f"/home/{TARGET_USER}"
APP_DIR = os.path.join(HOME_DIR, "fire-truck-app")
LOG_DIR = os.path.join(HOME_DIR, "logs")
DATA_DIR = os.path.join(HOME_DIR, "datos")
BOOT_CONFIG_FILE = "/boot/firmware/config.txt"
POWER_OK_GPIO = 12  # Pin BCM 12 (BOARD 32) para la señal de alimentación

# --- Colores para la Salida ---
C_HEADER = '\033[95m'
C_OKBLUE = '\033[94m'
C_OKCYAN = '\033[96m'
C_OKGREEN = '\032m'
C_WARNING = '\033[93m'
C_FAIL = '\033[91m'
C_ENDC = '\033[0m'
C_BOLD = '\033[1m'

# --- Funciones de Logging ---
def log_step(message):
    print(f"\n{C_HEADER}{C_BOLD}>>> {message}{C_ENDC}")

def log_info(message):
    print(f"{C_OKCYAN}    {message}{C_ENDC}")

def log_ok(message):
    print(f"{C_OKGREEN}[OK] {message}{C_ENDC}")

def log_warn(message):
    print(f"{C_WARNING}[WARN] {message}{C_ENDC}")

def log_fail(message):
    print(f"{C_FAIL}[FAIL] {message}{C_ENDC}", file=sys.stderr)
    sys.exit(1)

# --- Funciones Auxiliares ---
def run_command(command, as_user=None, env=None):
    """Ejecuta un comando de sistema, fallando si devuelve un error."""
    preexec_fn = None
    if as_user:
        try:
            user_info = pwd.getpwnam(as_user)
            uid, gid = user_info.pw_uid, user_info.pw_gid
            
            def demote():
                os.setgid(gid)
                os.setuid(uid)
            preexec_fn = demote
        except KeyError:
            log_fail(f"El usuario '{as_user}' no existe. No se puede ejecutar el comando.")

    try:
        # Usamos `capture_output=True` y `text=True` para suprimir la salida por defecto
        # y solo mostrarla si hay un error.
        process = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            preexec_fn=preexec_fn,
            env=env
        )
        return process
    except subprocess.CalledProcessError as e:
        log_fail(f"El comando '{' '.join(command)}' falló.\n"
                 f"--- STDOUT ---\n{e.stdout}\n"
                 f"--- STDERR ---\n{e.stderr}")
    except FileNotFoundError:
        log_fail(f"Comando no encontrado: '{command[0]}'. ¿Está instalado?")

def ensure_config_line(pattern, line, comment=None):
    """Asegura que una línea de configuración exista en config.txt."""
    try:
        with open(BOOT_CONFIG_FILE, 'r') as f:
            content = f.read()

        # Usamos `re.search` para buscar el patrón al inicio de una línea
        import re
        if re.search(f"^{re.escape(pattern)}", content, re.MULTILINE):
            log_info(f"La configuración '{pattern}' ya existe. Omitiendo.")
            return

        with open(BOOT_CONFIG_FILE, 'a') as f:
            log_info(f"Añadiendo: {line}")
            if comment:
                f.write(f"\n{comment}\n")
            f.write(f"{line}\n")

    except FileNotFoundError:
        log_fail(f"No se encontró el archivo de configuración: {BOOT_CONFIG_FILE}")
    except IOError as e:
        log_fail(f"Error de E/S al manejar {BOOT_CONFIG_FILE}: {e}")

def main():
    """Función principal del script de instalación."""
    
    # --- PASO 0: Comprobaciones Previas ---
    log_step("Paso 0: Realizando comprobaciones previas...")
    if os.geteuid() != 0:
        log_fail("Este script debe ser ejecutado como root. Por favor, usa 'sudo'.")

    # --- PASO DE INICIALIZACIÓN: Habilitación de Hardware Esencial ---
    log_step("Paso de Inicialización: Habilitando señal de alimentación...")
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(POWER_OK_GPIO, GPIO.OUT)
        GPIO.output(POWER_OK_GPIO, GPIO.HIGH)
        log_ok(f"Señal de alimentación habilitada en BCM pin {POWER_OK_GPIO} (3.3V) vía RPi.GPIO.")
    except (ImportError, RuntimeError) as e:
        log_warn(f"No se pudo inicializar RPi.GPIO ({e}).")
        log_info("Esto es normal si no estás en una Raspberry Pi o la librería no está instalada.")
        log_info("Intentando instalar 'python3-rpi.gpio' y reintentando...")
        run_command(["apt-get", "update"])
        run_command(["apt-get", "install", "-y", "python3-rpi.gpio"])
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(POWER_OK_GPIO, GPIO.OUT)
            GPIO.output(POWER_OK_GPIO, GPIO.HIGH)
            log_ok("¡Éxito! RPi.GPIO instalado y señal de alimentación habilitada.")
        except Exception as final_e:
            log_fail(f"Falló el segundo intento de inicializar GPIO: {final_e}")


    script_dir = os.path.dirname(os.path.realpath(__file__))
    key_src_path = os.path.join(script_dir, "deploy_key")

    if not os.path.exists(key_src_path):
        log_fail("No se encontró el archivo 'deploy_key' en el mismo directorio que el instalador.")
    log_ok("Comprobaciones previas superadas.")

    # --- PASO 1: Recopilar Información ---
    log_step("Paso 1: Recopilando información necesaria...")
    repo_url = input("Introduce la URL SSH de tu repositorio Git (ej. git@github.com:user/repo.git): ")
    git_branch = input("Introduce el nombre de la rama a desplegar (ej. main) [main]: ")
    if not git_branch:
        git_branch = "main"
    log_ok("Información recopilada.")

    # --- PASO 2: Preparación del Sistema ---
    log_step("Paso 2: Actualizando sistema e instalando dependencias...")
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    run_command(["apt-get", "update"], env=env)
    run_command(["apt-get", "upgrade", "-y"], env=env)
    run_command(["apt-get", "install", "-y", "git", "python3-pip", "python3-venv", "can-utils", "i2c-tools"], env=env)
    log_ok("Sistema y dependencias listos.")

    # --- PASO 3: Creación de Usuario y Directorios ---
    log_step("Paso 3: Configurando usuario y directorios...")
    try:
        pwd.getpwnam(TARGET_USER)
        log_info(f"El usuario '{TARGET_USER}' ya existe. Asegurando membresía de grupos...")
        run_command(["usermod", "-a", "-G", "sudo,gpio,i2c,dialout", TARGET_USER])
    except KeyError:
        log_info(f"Creando usuario '{TARGET_USER}'...")
        run_command(["useradd", "-m", "-s", "/bin/bash", "-G", "sudo,gpio,i2c,dialout", TARGET_USER])
        log_warn(f"¡ACCIÓN REQUERIDA! Se ha creado el usuario '{TARGET_USER}'.")
        log_warn(f"Por favor, establece una contraseña para él ejecutando 'sudo passwd {TARGET_USER}' después.")

    log_info("Creando directorios de la aplicación...")
    os.makedirs(APP_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    
    user_info = pwd.getpwnam(TARGET_USER)
    uid, gid = user_info.pw_uid, user_info.pw_gid
    shutil.chown(HOME_DIR, user=uid, group=gid)
    for root, dirs, files in os.walk(HOME_DIR):
        for d in dirs:
            shutil.chown(os.path.join(root, d), user=uid, group=gid)
        for f in files:
            shutil.chown(os.path.join(root, f), user=uid, group=gid)
    log_ok("Usuario y directorios configurados.")

    # --- PASO 4: Instalación de la Deploy Key ---
    log_step("Paso 4: Instalando Deploy Key de Git...")
    key_dir = os.path.join(HOME_DIR, ".ssh")
    key_dest_path = os.path.join(key_dir, "id_ed25519")

    os.makedirs(key_dir, mode=0o700, exist_ok=True)
    shutil.copy(key_src_path, key_dest_path)
    os.chmod(key_dest_path, 0o600)
    shutil.chown(key_dir, user=uid, group=gid)
    shutil.chown(key_dest_path, user=uid, group=gid)

    git_host = repo_url.split('@')[1].split(':')[0]
    log_info(f"Añadiendo el host de Git ({git_host}) a known_hosts...")
    known_hosts_path = os.path.join(key_dir, "known_hosts")
    
    keyscan_result = run_command(["ssh-keyscan", git_host])
    
    # Escribir y ordenar para evitar duplicados
    existing_keys = set()
    if os.path.exists(known_hosts_path):
        with open(known_hosts_path, 'r') as f:
            existing_keys = set(line.strip() for line in f)

    new_keys = set(line.strip() for line in keyscan_result.stdout.splitlines())
    all_keys = sorted(list(existing_keys.union(new_keys)))
    
    with open(known_hosts_path, 'w') as f:
        f.write('\n'.join(all_keys) + '\n')
        
    shutil.chown(known_hosts_path, user=uid, group=gid)
    log_ok("Deploy Key instalada correctamente.")

    # --- PASO 5: Configuración de Hardware de la RPi ---
    log_step(f"Paso 5: Configurando periféricos en {BOOT_CONFIG_FILE}...")
    ensure_config_line("dtparam=i2c_arm=", "dtparam=i2c_arm=on", "# Habilitar I2C y SPI")
    ensure_config_line("dtparam=spi=", "dtparam=spi=on")
    ensure_config_line("dtoverlay=mcp2515-can0", "dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=23", "# Habilitar CAN bus (fire-truck-app)")
    ensure_config_line("dtoverlay=i2c-rtc,ds3231", "dtoverlay=i2c-rtc,ds3231", "# Habilitar RTC DS3231 (fire-truck-app)")
    ensure_config_line("enable_uart=", "enable_uart=1", "# Habilitar UART y deshabilitar Bluetooth (fire-truck-app)")
    ensure_config_line("dtoverlay=disable-bt", "dtoverlay=disable-bt")
    
    log_info("Deshabilitando fake-hwclock para dar prioridad al RTC físico...")
    try:
        run_command(["apt-get", "-y", "remove", "fake-hwclock"])
        run_command(["update-rc.d", "-f", "fake-hwclock", "remove"])
    except subprocess.CalledProcessError as e:
        log_warn(f"No se pudo remover fake-hwclock (quizás ya no estaba instalado): {e.stderr}")
    log_ok("Configuración de hardware completada.")

    # --- PASO 6: Clonar Repositorio de la Aplicación ---
    log_step("Paso 6: Clonando repositorio de la aplicación...")
    if os.path.exists(os.path.join(APP_DIR, ".git")):
        log_warn("El directorio de la aplicación ya existe. Se forzará la actualización.")
        run_command(["git", "-C", APP_DIR, "fetch", "--all"], as_user=TARGET_USER)
        run_command(["git", "-C", APP_DIR, "reset", "--hard", f"origin/{git_branch}"], as_user=TARGET_USER)
        run_command(["git", "-C", APP_DIR, "clean", "-fdx"], as_user=TARGET_USER)
    else:
        run_command(["git", "clone", "--branch", git_branch, repo_url, APP_DIR], as_user=TARGET_USER)
    log_ok(f"Repositorio clonado/actualizado en {APP_DIR}.")

    # --- PASO 7: Configuración del Entorno Python ---
    log_step("Paso 7: Configurando entorno virtual y dependencias...")
    venv_path = os.path.join(APP_DIR, ".venv")
    pip_path = os.path.join(venv_path, "bin/pip")
    requirements_path = os.path.join(APP_DIR, "requirements.txt")

    run_command(["python3", "-m", "venv", venv_path], as_user=TARGET_USER)
    run_command([pip_path, "install", "-r", requirements_path], as_user=TARGET_USER)
    log_ok("Entorno Python listo.")
    
    # --- PASO 8: Configuración de Permisos Finales ---
    log_step("Paso 8: Estableciendo permisos de sistema...")
    log_info("Configurando sudo sin contraseña para shutdown/reboot...")
    sudoers_file = "/etc/sudoers.d/99-fire-truck-app"
    with open(sudoers_file, "w") as f:
        f.write(f"{TARGET_USER} ALL=(ALL) NOPASSWD: /sbin/shutdown, /sbin/reboot\n")
    os.chmod(sudoers_file, 0o440)

    log_info("Haciendo ejecutables los scripts necesarios...")
    script_to_exec = os.path.join(APP_DIR, "scripts/check_and_install_update.sh")
    if os.path.exists(script_to_exec):
        st = os.stat(script_to_exec)
        os.chmod(script_to_exec, st.st_mode | stat.S_IEXEC)
    log_ok("Permisos establecidos.")

    # --- PASO 9: Instalación de Servicios systemd ---
    log_step("Paso 9: Instalando servicios systemd...")
    services = ["app.service", "updater.service"]
    for service in services:
        src = os.path.join(APP_DIR, "services", service)
        dest = os.path.join("/etc/systemd/system", service)
        if os.path.exists(src):
            log_info(f"Copiando {service} a {dest}")
            shutil.copy(src, dest)
        else:
            log_warn(f"No se encontró el archivo de servicio: {src}")

    run_command(["systemctl", "daemon-reload"])
    run_command(["systemctl", "enable", "app.service"])
    run_command(["systemctl", "enable", "updater.service"])
    log_ok("Servicios instalados y habilitados para el arranque.")

    # --- PASO 10: Finalización ---
    log_step("¡Instalación completada!")
    log_warn("Es NECESARIO reiniciar el sistema para aplicar los cambios de hardware.")
    
    reboot_choice = input("¿Deseas reiniciar la Raspberry Pi ahora? (s/n): ").lower()
    if reboot_choice == 's':
        log_info("Reiniciando el sistema ahora...")
        run_command(["reboot"])
    else:
        log_info("No se reiniciará. Recuerda hacerlo manualmente con 'sudo reboot'.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_fail(f"Ocurrió un error inesperado durante la instalación: {e}")
    finally:
        # Intenta limpiar los pines GPIO al salir, aunque el script es de un solo uso
        try:
            import RPi.GPIO as GPIO
            GPIO.cleanup()
            log_info("Pines GPIO limpiados.")
        except (ImportError, RuntimeError):
            pass # No hacer nada si RPi.GPIO no está disponible