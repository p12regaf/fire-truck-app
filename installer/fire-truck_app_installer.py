#!/usr/bin/env python3

# =============================================================================
#  INSTALADOR AUTOMÁTICO PARA fire-truck-app (VERSIÓN PYTHON)
# =============================================================================
#
#  Este script realiza una instalación completa y robusta en una Raspberry Pi:
#  - Corrige los problemas de permisos de clave SSH desde el inicio.
#  - Configura sudoers para permitir que el servicio de actualización funcione.
#  - Asegura que la red esté lista antes de que los servicios arranquen.
#  - Actualiza todos los parámetros necesarios en config.yaml.
#
#  USO:
#  1. Copia este archivo y tu clave privada (renombrada a 'deploy_key') a la RPi.
#  2. Hazlo ejecutable y ejecutalo con sudo: chmod +x fire-truck_app_installer.py
#  3. Ejecútalo con sudo: sudo python3 ./fire-truck_app_installer.py 
#

# =============================================================================

import os
import sys
import subprocess
import shutil
import pwd
import stat
import re

# --- Variables de Configuración ---
TARGET_USER = "cosigein"
HOME_DIR = f"/home/{TARGET_USER}"
APP_DIR = os.path.join(HOME_DIR, "fire-truck-app")
LOG_DIR = os.path.join(HOME_DIR, "logs")
DATA_DIR = os.path.join(HOME_DIR, "datos")
BOOT_CONFIG_PRIMARY_PATH = "/boot/firmware/config.txt"
BOOT_CONFIG_FALLBACK_PATH = "/boot/config.txt" 
BOOT_CONFIG_FILE = "" 
POWER_OK_GPIO = 12

# --- Colores para la Salida ---
C_HEADER = '\033[95m'
C_OKBLUE = '\033[94m'
C_OKCYAN = '\033[96m'
C_OKGREEN = '\033[92m'
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
def run_command(command, as_user=None, env=None, ignore_errors=False):
    """Ejecuta un comando de sistema."""
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
        process = subprocess.run(
            command,
            check=not ignore_errors,
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

def configure_fallback_wifi():
    """Añade una red Wi-Fi de respaldo con prioridad baja."""
    log_step("Paso 2.5: Configurando Wi-Fi de respaldo...")
    WPA_CONF_PATH = "/etc/wpa_supplicant/wpa_supplicant.conf"
    
    default_fallback_ssid = "CSGWconfig03"
    try:
        ssid_input = input(f"Introduce SSID de la red de respaldo [{default_fallback_ssid}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        ssid_input = ""
        
    final_ssid = ssid_input or default_fallback_ssid
    log_info(f"SSID de respaldo establecido a: {final_ssid}")

    default_fallback_psk = "12345678"
    try:
        psk_input = input(f"Introduce la contraseña de la red de respaldo [{default_fallback_psk}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        psk_input = ""
        
    final_psk = psk_input or default_fallback_psk
    log_info(f"Usando contraseña {'por defecto' if not psk_input else 'proporcionada'}.")

    if not os.path.exists(WPA_CONF_PATH):
        log_warn(f"No se encontró el archivo '{WPA_CONF_PATH}'. Omitiendo configuración de Wi-Fi.")
        return

    try:
        with open(WPA_CONF_PATH, 'r') as f:
            content = f.read()

        if f'ssid="{final_ssid}"' in content:
            log_info(f"La red Wi-Fi '{final_ssid}' ya está configurada. Omitiendo.")
            return

        priorities = re.findall(r'^\s*priority\s*=\s*(-?\d+)', content, re.MULTILINE)
        new_priority = (min(int(p) for p in priorities) - 1) if priorities else -1
        log_info(f"Asignando prioridad '{new_priority}' a la red de respaldo '{final_ssid}'.")

        fallback_network_block = f"""
network={{
    ssid="{final_ssid}"
    psk="{final_psk}"
    key_mgmt=WPA-PSK
    priority={new_priority}
}}
"""
        with open(WPA_CONF_PATH, 'a') as f:
            f.write(fallback_network_block)

        log_ok(f"Red Wi-Fi de respaldo '{final_ssid}' añadida correctamente.")
    except (IOError, PermissionError) as e:
        log_fail(f"No se pudo modificar el archivo de configuración de Wi-Fi: {e}")

def disable_serial_console():
    """Deshabilita la consola de login en el puerto serie para liberarlo."""
    log_info("Deshabilitando la consola de login en el puerto serie...")
    try:
        run_command(["systemctl", "stop", "serial-getty@ttyS0.service"], ignore_errors=True)
        run_command(["systemctl", "disable", "serial-getty@ttyS0.service"], ignore_errors=True)
        
        cmdline_path = "/boot/firmware/cmdline.txt"
        if not os.path.exists(cmdline_path):
             cmdline_path = "/boot/cmdline.txt"

        if os.path.exists(cmdline_path):
            with open(cmdline_path, "r") as f:
                content = f.read()
            
            new_content = re.sub(r"console=(serial0|ttyAMA0),\d+\s?", "", content)
            
            with open(cmdline_path, "w") as f:
                f.write(new_content)
            log_ok("Consola serie deshabilitada. Se requiere reinicio.")
        else:
            log_warn(f"No se encontró {cmdline_path}. No se pudo deshabilitar la consola del kernel.")
            
    except Exception as e:
        log_warn(f"No se pudo deshabilitar completamente la consola serie: {e}")

def main():
    """Función principal del script de instalación."""
    log_step("Comprobación previa: conexión a Internet después de la instalación")
    try:
        conn_choice = input("¿Estás seguro de que el dispositivo tendrá conexión a Internet después de la instalación? (s/n) [s]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        conn_choice = "n"

    if conn_choice in ("n", "no"):
        log_warn("Instalación cancelada.")
        sys.exit(0)
    
    log_step("Paso 0: Realizando comprobaciones previas...")
    if os.geteuid() != 0:
        log_fail("Este script debe ser ejecutado como root. Por favor, usa 'sudo'.")

    global BOOT_CONFIG_FILE
    if os.path.exists(BOOT_CONFIG_PRIMARY_PATH):
        BOOT_CONFIG_FILE = BOOT_CONFIG_PRIMARY_PATH
    elif os.path.exists(BOOT_CONFIG_FALLBACK_PATH):
        BOOT_CONFIG_FILE = BOOT_CONFIG_FALLBACK_PATH
    else:
        log_fail("No se pudo encontrar un archivo de configuración de arranque.")

    log_step("Parando servicios existentes...")
    services_to_stop = ["app.service", "updater.service", "apagar.service", "alarma.service", "reinicio.service","GPS.service", "gps_imu_logger.service", "interfaz_manual.service", "OBD.service", "rotativo.service", "serverweb.service", "serial.service"]
    for service in services_to_stop:
        run_command(["systemctl", "stop", service], ignore_errors=True)
        run_command(["systemctl", "disable", service], ignore_errors=True)
    log_ok("Servicios anteriores (si existían) detenidos y deshabilitados.")

    log_step("Paso de Inicialización: Habilitando señal de alimentación...")
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(POWER_OK_GPIO, GPIO.OUT)
        GPIO.output(POWER_OK_GPIO, GPIO.HIGH)
        log_ok(f"Señal de alimentación habilitada en BCM pin {POWER_OK_GPIO}.")
    except (ImportError, RuntimeError):
        log_info("Instalando 'python3-rpi.gpio'...")
        run_command(["apt-get", "update"])
        run_command(["apt-get", "install", "-y", "python3-rpi.gpio"])
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(POWER_OK_GPIO, GPIO.OUT)
            GPIO.output(POWER_OK_GPIO, GPIO.HIGH)
            log_ok("RPi.GPIO instalado y señal de alimentación habilitada.")
        except Exception as final_e:
            log_fail(f"Falló el segundo intento de inicializar GPIO: {final_e}")

    script_dir = os.path.dirname(os.path.realpath(__file__))
    key_src_path = os.path.join(script_dir, "deploy_key")
    if not os.path.exists(key_src_path):
        log_fail("No se encontró el archivo 'deploy_key' en el mismo directorio que el instalador.")
    log_ok("Comprobaciones previas superadas.")

    log_step("Paso 1: Recopilando información necesaria...")
    repo_url = input(f"Introduce la URL SSH del repositorio Git [git@github.com:p12regaf/fire-truck-app.git]: ") or "git@github.com:p12regaf/fire-truck-app.git"
    git_branch = input("Introduce la rama a desplegar [main]: ") or "main"

    device_id = ""
    while not (device_id.isdigit() and len(device_id) == 3):
        device_id = input("Introduce el número de dispositivo de 3 dígitos (ej. 001): ").strip()

    DEFAULT_ROTARY_PIN = "22"
    rotary_pin = ""
    while not rotary_pin.isdigit():
        rotary_pin = input(f"Introduce el pin BCM para el rotativo [{DEFAULT_ROTARY_PIN}]: ").strip() or DEFAULT_ROTARY_PIN
    log_ok("Información recopilada.")

    log_step("Paso 2: Actualizando sistema e instalando dependencias...")
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    run_command(["apt-get", "update"], env=env)
    
    upgrade_choice = input("¿Deseas ejecutar 'apt-get upgrade -y' ahora? (s/n) [n]: ").strip().lower()
    if upgrade_choice.startswith('s'):
        log_info("Ejecutando 'apt-get upgrade -y'...")
        run_command(["apt-get", "upgrade", "-y"], env=env)
    
    run_command(["apt-get", "install", "-y", "git", "python3-pip", "python3-venv", "can-utils", "i2c-tools"], env=env)
    log_ok("Sistema y dependencias listos.")

    wifi_choice = input("¿Deseas configurar una red Wi-Fi de respaldo? (s/n) [n]: ").strip().lower()
    if wifi_choice.startswith('s'):
        configure_fallback_wifi()

    log_step("Paso 3: Configurando usuario y directorios...")
    try:
        pwd.getpwnam(TARGET_USER)
        log_info(f"El usuario '{TARGET_USER}' ya existe.")
    except KeyError:
        log_info(f"Creando usuario '{TARGET_USER}'...")
        run_command(["useradd", "-m", "-s", "/bin/bash", TARGET_USER])
        log_warn(f"¡ACCIÓN REQUERIDA! Se ha creado el usuario '{TARGET_USER}'.")
        log_warn(f"Establece una contraseña con: 'sudo passwd {TARGET_USER}'")
    
    log_info("Añadiendo usuario a los grupos necesarios (sudo, gpio, i2c, dialout)...")
    run_command(["usermod", "-a", "-G", "sudo,gpio,i2c,dialout", TARGET_USER])

    log_info("Creando directorios de la aplicación...")
    for d in [APP_DIR, LOG_DIR, DATA_DIR]:
        run_command(["mkdir", "-p", d], as_user=TARGET_USER)
    log_ok("Usuario y directorios configurados.")
    
    # --- INICIO DE LA CORRECCIÓN DE PERMISOS ---
    log_step("Paso 4: Instalando Deploy Key de Git de forma segura...")
    key_dir = os.path.join(HOME_DIR, ".ssh")
    key_dest_path = os.path.join(key_dir, "id_ed25519")
    
    user_info = pwd.getpwnam(TARGET_USER)
    uid, gid = user_info.pw_uid, user_info.pw_gid

    # Crear el directorio .ssh con permisos correctos (700) y propietario correcto
    log_info("Creando directorio .ssh con permisos 700...")
    os.makedirs(key_dir, mode=0o700, exist_ok=True)
    shutil.chown(key_dir, user=uid, group=gid)

    # Copiar y establecer permisos y propietario de la clave
    log_info("Copiando clave privada con permisos 600...")
    shutil.copy(key_src_path, key_dest_path)
    os.chmod(key_dest_path, 0o600)
    shutil.chown(key_dest_path, user=uid, group=gid)

    # Añadir host de Git a known_hosts
    git_host = repo_url.split('@')[1].split(':')[0]
    log_info(f"Añadiendo el host de Git ({git_host}) a known_hosts...")
    known_hosts_path = os.path.join(key_dir, "known_hosts")
    
    # Ejecutamos ssh-keyscan como el usuario final para evitar problemas de permisos
    run_command(["ssh-keyscan", "-H", git_host], as_user=TARGET_USER)
    keyscan_result = run_command(["ssh-keyscan", "-H", git_host], as_user=TARGET_USER)

    # Usamos 'tee' con 'sudo' para escribir el archivo como root, pero luego ajustamos el dueño.
    add_key_cmd = f"echo '{keyscan_result.stdout.strip()}' >> {known_hosts_path}"
    run_command(['sh', '-c', add_key_cmd], as_user=TARGET_USER)
    # Aseguramos que el archivo known_hosts también pertenece al usuario
    if os.path.exists(known_hosts_path):
        shutil.chown(known_hosts_path, user=uid, group=gid)
    
    log_ok("Deploy Key instalada y configurada correctamente.")
    # --- FIN DE LA CORRECCIÓN DE PERMISOS ---

    log_step(f"Paso 5: Configurando periféricos en {BOOT_CONFIG_FILE}...")
    ensure_config_line("dtparam=i2c_arm=", "dtparam=i2c_arm=on", "# Habilitar I2C y SPI")
    ensure_config_line("dtparam=spi=", "dtparam=spi=on")

    can_choice = input("Configurar módulo CAN? (antiguo/nuevo/no) [no]: ").strip().lower()
    if can_choice.startswith('a'):
        ensure_config_line("dtoverlay=mcp2515-can0", "dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25", "# CAN bus (antiguo)")
    elif can_choice.startswith('n'):
        ensure_config_line("dtoverlay=mcp2515-can0", "dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=23", "# CAN bus (nuevo)")
    
    ensure_config_line("dtoverlay=i2c-rtc,ds3231", "dtoverlay=i2c-rtc,ds3231", "# Habilitar RTC DS3231")
    ensure_config_line("enable_uart=", "enable_uart=1", "# Habilitar UART y deshabilitar Bluetooth")
    ensure_config_line("dtoverlay=disable-bt", "dtoverlay=disable-bt")
    
    log_info("Deshabilitando fake-hwclock para dar prioridad al RTC físico...")
    run_command(["apt-get", "-y", "remove", "fake-hwclock"], ignore_errors=True)
    run_command(["update-rc.d", "-f", "fake-hwclock", "remove"], ignore_errors=True)
    log_ok("Configuración de hardware completada.")
    disable_serial_console()

    log_step("Paso 6: Clonando repositorio de la aplicación...")
    if os.path.exists(os.path.join(APP_DIR, ".git")):
        log_warn("El repositorio ya existe. Forzando actualización...")
        run_command(["git", "-C", APP_DIR, "fetch", "--all"], as_user=TARGET_USER)
        run_command(["git", "-C", APP_DIR, "reset", "--hard", f"origin/{git_branch}"], as_user=TARGET_USER)
        run_command(["git", "-C", APP_DIR, "clean", "-fdx"], as_user=TARGET_USER)
    else:
        run_command(["git", "clone", "--branch", git_branch, repo_url, APP_DIR], as_user=TARGET_USER)
    log_ok(f"Repositorio clonado/actualizado en {APP_DIR}.")
    
    run_command(["chown", "-R", f"{uid}:{gid}", APP_DIR])

    log_step("Paso 7: Configurando ID de dispositivo y pin en config.yaml...")
    TEMPLATE_CONFIG_PATH = os.path.join(APP_DIR, "config", "config.yaml.template")
    CONFIG_YAML_PATH = os.path.join(APP_DIR, "config", "config.yaml")
    try:
        shutil.copy(TEMPLATE_CONFIG_PATH, CONFIG_YAML_PATH)
        with open(CONFIG_YAML_PATH, 'r') as f:
            content = f.read()
        
        content = re.sub(r'device_number:\s*"\d+"', f'device_number: "{device_id}"', content)
        content = re.sub(r'pin:\s*\d+\s*#\s*GPIO_ROTATIVO_PIN', f'pin: {rotary_pin} # GPIO_ROTATIVO_PIN', content)

        with open(CONFIG_YAML_PATH, 'w') as f:
            f.write(content)
        
        shutil.chown(CONFIG_YAML_PATH, user=uid, group=gid)
        log_ok("Archivo config.yaml creado y actualizado.")
    except Exception as e:
        log_fail(f"No se pudo crear o modificar {CONFIG_YAML_PATH}: {e}")

    log_step("Paso 8: Configurando entorno virtual y dependencias...")
    venv_path = os.path.join(APP_DIR, ".venv")
    pip_path = os.path.join(venv_path, "bin/pip")
    req_path = os.path.join(APP_DIR, "requirements.txt")
    run_command(["python3", "-m", "venv", venv_path], as_user=TARGET_USER)
    run_command([pip_path, "install", "-r", req_path], as_user=TARGET_USER)
    log_ok("Entorno Python listo.")
    
    # --- INICIO DE LA CORRECCIÓN DE SUDOERS ---
    log_step("Paso 9: Estableciendo permisos de sistema...")
    log_info("Configurando sudo sin contraseña para el actualizador y reinicios...")
    sudoers_file_path = "/etc/sudoers.d/99-fire-truck-app"
    sudo_rule = (
        f"{TARGET_USER} ALL=(ALL) NOPASSWD: /sbin/shutdown, /sbin/reboot, "
        f"/bin/systemctl stop app.service, /bin/systemctl start app.service\n"
    )
    with open(sudoers_file_path, "w") as f:
        f.write(sudo_rule)
    os.chmod(sudoers_file_path, 0o440)
    # --- FIN DE LA CORRECCIÓN DE SUDOERS ---

    log_info("Haciendo ejecutables los scripts necesarios...")
    script_to_exec = os.path.join(APP_DIR, "scripts/check_and_install_update.sh")
    if os.path.exists(script_to_exec):
        st = os.stat(script_to_exec)
        os.chmod(script_to_exec, st.st_mode | stat.S_IEXEC)
    log_ok("Permisos de sistema establecidos.")

    log_step("Paso 10: Instalando y habilitando servicios systemd...")
    # --- INICIO DE LA MEJORA DE RED ---
    log_info("Habilitando el servicio de espera de red para un arranque robusto...")
    run_command(["systemctl", "enable", "systemd-networkd-wait-online.service"], ignore_errors=True)
    # --- FIN DE LA MEJORA DE RED ---
    
    for service in ["app.service", "updater.service"]:
        src = os.path.join(APP_DIR, "services", service)
        dest = os.path.join("/etc/systemd/system", service)
        if os.path.exists(src):
            shutil.copy(src, dest)
        else:
            log_warn(f"No se encontró el archivo de servicio: {src}")

    run_command(["systemctl", "daemon-reload"])
    run_command(["systemctl", "enable", "app.service"])
    run_command(["systemctl", "enable", "updater.service"])
    log_ok("Servicios instalados y habilitados para el arranque.")

    log_step("¡Instalación completada!")
    log_warn("Es NECESARIO reiniciar para aplicar todos los cambios.")
    
    reboot_choice = input("¿Deseas reiniciar la Raspberry Pi ahora? (s/n) [s]: ").lower().strip()
    if not reboot_choice or reboot_choice.startswith('s'):
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
        try:
            import RPi.GPIO as GPIO
            GPIO.cleanup()
            log_info("Pines GPIO limpiados.")
        except (ImportError, RuntimeError):
            pass