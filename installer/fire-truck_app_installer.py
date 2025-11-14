#!/usr/bin/env python3

# ! TODO: Asegurarse de que el dispositivo se conectará a internet después de la instalación.

# =============================================================================
#  INSTALADOR AUTOMÁTICO PARA fire-truck-app (VERSIÓN PYTHON)
# =============================================================================
#
#  Este script realiza una instalación completa en una Raspberry Pi:
#  0. Habilita una señal de alimentación en GPIO 12 (3.3V) usando RPi.GPIO.
#  1. Comprueba que se ejecuta como root.
#  2. Pide los datos necesarios (URL del repo, rama, ID de equipo).
#  3. Actualiza el sistema e instala todas las dependencias.
#  4. Crea el usuario 'cosigein' si no existe.
#  5. Instala la Deploy Key de Git que debe estar junto a este script.
#  6. Configura /boot/firmware/config.txt para habilitar todo el hardware.
#  7. Clona el repositorio de la aplicación.
#  8. Modifica config.yaml con el ID de equipo proporcionado.
#  9. Configura el entorno virtual y las dependencias de Python.
#  10. Establece todos los permisos de sistema y de usuario.
#  11. Instala y habilita los servicios systemd.
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
import re

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

def configure_fallback_wifi():
    """Añade una red Wi-Fi de respaldo con prioridad baja."""
    log_step("Paso 2.5: Configurando Wi-Fi de respaldo...")
    WPA_CONF_PATH = "/etc/wpa_supplicant/wpa_supplicant.conf"
    
    default_fallback_ssid = "CSGWconfig03"
    try:
        ssid_input = input(f"Introduce SSID de la red de respaldo [{default_fallback_ssid}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        ssid_input = ""
        
    # Usar el valor del usuario si lo proporciona, si no, el por defecto.
    final_ssid = ssid_input or default_fallback_ssid
    log_info(f"SSID de respaldo establecido a: {final_ssid}")

    default_fallback_psk = "12345678"
    try:
        psk_input = input(f"Introduce la contraseña de la red de respaldo [{default_fallback_psk}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        psk_input = ""
        
    # Usar la contraseña del usuario si la proporciona, si no, la por defecto.
    final_psk = psk_input or default_fallback_psk
    if psk_input:
        log_info("Contraseña de respaldo establecida a la proporcionada por el usuario.")
    else:
        log_info(f"Usando contraseña por defecto.")

    if not os.path.exists(WPA_CONF_PATH):
        log_warn(f"No se encontró el archivo '{WPA_CONF_PATH}'. Omitiendo configuración de Wi-Fi.")
        return

    try:
        with open(WPA_CONF_PATH, 'r') as f:
            content = f.read()

        # Usar las variables locales 'final_ssid' y 'final_psk'
        if f'ssid="{final_ssid}"' in content:
            log_info(f"La red Wi-Fi '{final_ssid}' ya está configurada. Omitiendo.")
            return

        priorities = re.findall(r'^\s*priority\s*=\s*(-?\d+)', content, re.MULTILINE)
        existing_priorities = [int(p) for p in priorities]
        new_priority = (min(existing_priorities) - 1) if existing_priorities else -1
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
    except Exception as e:
        log_fail(f"Error inesperado al configurar el Wi-Fi de respaldo: {e}")

def main():
    """Función principal del script de instalación."""
    
    log_step("Paso 0: Realizando comprobaciones previas...")
    if os.geteuid() != 0:
        log_fail("Este script debe ser ejecutado como root. Por favor, usa 'sudo'.")

    log_step("Parando servicios existentes...")
    # ! Añadir todos los servicios que deban ser detenidos antes de la instalación
    services_to_stop = ["alarma.service", "apagar.service", "reinicio.service", "app.service", "updater.service"]
    for service in services_to_stop:
        try:
            proc = subprocess.run(
                ["systemctl", "stop", service],
                capture_output=True,
                text=True
            )
            if proc.returncode == 0:
                log_info(f"Servicio '{service}' detenido correctamente.")
            else:
                stderr = (proc.stderr or "").lower()
                stdout = (proc.stdout or "").lower()
                # Detectar unidades no encontradas o mensajes comunes de error
                if ("could not be found" in stderr or "not-found" in stderr or
                        "not found" in stderr or "unit" in stderr and "could not" in stderr):
                    log_warn(f"Servicio '{service}' no encontrado. Se omite.")
                else:
                    log_warn(f"Fallo al detener '{service}' (code={proc.returncode}). "
                             f"STDOUT: {stdout.strip()} STDERR: {stderr.strip()}")
        except FileNotFoundError:
            log_warn("systemctl no encontrado en este sistema. No se pudieron detener servicios con systemctl.")
            break
        except Exception as e:
            log_warn(f"Error inesperado al detener '{service}': {e}")

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

    log_step("Paso 1: Recopilando información necesaria...")
    repo_url = input("Introduce la URL SSH de tu repositorio Git (git@github.com:p12regaf/fire-truck-app.git): ")
    if not repo_url:
        repo_url = "git@github.com:p12regaf/fire-truck-app.git"
    git_branch = input("Introduce el nombre de la rama a desplegar (ej. main) [main]: ")
    if not git_branch:
        git_branch = "main"

    device_id = None
    while True:
        user_input = input("Introduce el número de dispositivo de 3 dígitos (ej. 001, 042): ").strip()
        if user_input.isdigit() and len(user_input) == 3:
            device_id = user_input
            break
        else:
            print(f"{C_WARNING}Entrada inválida. Por favor, introduce exactamente 3 números.{C_ENDC}")
    log_ok("Información recopilada.")

    log_step("Paso 2: Actualizando sistema e instalando dependencias...")
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    run_command(["apt-get", "update"], env=env)

    # Preguntar si se desea hacer un upgrade completo (puede tardar mucho)
    try:
        choice = input("¿Deseas ejecutar 'apt-get upgrade -y' ahora? Esto puede tardar mucho. (s/n) [n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        choice = "n"
    if choice in ("s", "y", "si", "yes"):
        log_step("Ejecutando 'apt-get upgrade -y'...")
        run_command(["apt-get", "upgrade", "-y"], env=env)
        log_ok("Upgrade completado.")
    else:
        log_info("Se omitió 'apt-get upgrade'.")
    run_command(["apt-get", "install", "-y", "git", "python3-pip", "python3-venv", "can-utils", "i2c-tools"], env=env)
    log_ok("Sistema y dependencias listos.")

    try:
        wifi_choice = input("¿Deseas configurar la red Wi-Fi de respaldo ahora? (s/n) [n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        wifi_choice = "n"
    if wifi_choice in ("s", "y", "si", "yes"):
        configure_fallback_wifi()
    else:
        log_info("Se omitió la configuración de la red Wi-Fi de respaldo.")

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

    log_step("Limpiando instalación anterior...")
    old_programs_dir = os.path.join(HOME_DIR, "Documentos/.PROGRAMS")
    if os.path.exists(old_programs_dir):
        try:
            user_choice = input(f"¿Deseas eliminar el directorio anterior ({old_programs_dir})? (s/n) [s]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            user_choice = "s"
        
        if user_choice in ("s", "y", "si", "yes", ""):
            log_info(f"Eliminando directorio anterior: {old_programs_dir}")
            shutil.rmtree(old_programs_dir)
            log_ok("Directorio anterior eliminado correctamente.")
        else:
            log_info("Se omitió la eliminación del directorio anterior.")
    else:
        log_info("No se encontró directorio anterior. Continuando...")


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


    log_step(f"Paso 5: Configurando periféricos en {BOOT_CONFIG_FILE}...")
    ensure_config_line("dtparam=i2c_arm=", "dtparam=i2c_arm=on", "# Habilitar I2C y SPI")
    ensure_config_line("dtparam=spi=", "dtparam=spi=on")

    log_step("Configurando módulo CAN...")
    try:
        can_choice = input("¿Deseas configurar el módulo CAN? (antiguo/nuevo/no) [no]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        can_choice = "no"
    
    if can_choice in ("a", "o", "antiguo", "old"):
        # TODO: Verificar si el interrupt 25 es correcto para el módulo antiguo
        can_config = "dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25"
        log_info("Configurando CAN con interrupt=25 (módulo antiguo).")
        ensure_config_line("dtoverlay=mcp2515-can0", can_config, "# Habilitar CAN bus (fire-truck-app)")
    elif can_choice in ("nuevo", "new"):
        # TODO: Verificar si el interrupt 23 es correcto para el módulo nuevo
        can_config = "dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=23"
        log_info("Configurando CAN con interrupt=23 (módulo nuevo).")
        ensure_config_line("dtoverlay=mcp2515-can0", can_config, "# Habilitar CAN bus (fire-truck-app)")
    else:
        log_info("Se omitió la configuración del módulo CAN.")
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


    log_step("Paso 6: Clonando repositorio de la aplicación...")
    if os.path.exists(os.path.join(APP_DIR, ".git")):
        log_warn("El directorio de la aplicación ya existe. Se forzará la actualización.")
        run_command(["git", "-C", APP_DIR, "fetch", "--all"], as_user=TARGET_USER)
        run_command(["git", "-C", APP_DIR, "reset", "--hard", f"origin/{git_branch}"], as_user=TARGET_USER)
        run_command(["git", "-C", APP_DIR, "clean", "-fdx"], as_user=TARGET_USER)
    else:
        run_command(["git", "clone", "--branch", git_branch, repo_url, APP_DIR], as_user=TARGET_USER)
    log_ok(f"Repositorio clonado/actualizado en {APP_DIR}.")


    log_step("Paso 7: Configurando ID de dispositivo en config.yaml...")
    TEMPLATE_CONFIG_PATH = os.path.join(APP_DIR, "config", "config.yaml.template")
    CONFIG_YAML_PATH = os.path.join(APP_DIR, "config", "config.yaml")
    try:
        # 1. Copiar la plantilla para crear el archivo de configuración local
        log_info(f"Copiando plantilla de configuración a '{CONFIG_YAML_PATH}'...")
        shutil.copy(TEMPLATE_CONFIG_PATH, CONFIG_YAML_PATH)

        # 2. Leer el nuevo archivo de configuración local
        with open(CONFIG_YAML_PATH, 'r') as f:
            config_content = f.read()
        
        # 3. Modificar el contenido con el ID del dispositivo
        new_config_content = re.sub(
            r'(\s*device_number:\s*")[^"]*(")',
            fr'\g<1>{device_id}\g<2>',
            config_content
        )
        
        if new_config_content == config_content:
            log_warn("No se encontró el campo 'device_number' en la plantilla. No se pudo actualizar el ID.")
        else:
            # 4. Escribir los cambios en el archivo de configuración local
            with open(CONFIG_YAML_PATH, 'w') as f:
                f.write(new_config_content)
            log_ok("El archivo config.yaml ha sido creado y actualizado con el ID de equipo.")
            
    except FileNotFoundError:
        log_fail(f"No se encontró el archivo de plantilla de configuración en '{TEMPLATE_CONFIG_PATH}'. ¿Está en el repositorio?")
    except (IOError, shutil.Error) as e:
        log_fail(f"No se pudo crear o modificar {CONFIG_YAML_PATH}: {e}")


    log_step("Paso 8: Configurando entorno virtual y dependencias...")
    venv_path = os.path.join(APP_DIR, ".venv")
    pip_path = os.path.join(venv_path, "bin/pip")
    requirements_path = os.path.join(APP_DIR, "requirements.txt")

    run_command(["python3", "-m", "venv", venv_path], as_user=TARGET_USER)
    run_command([pip_path, "install", "-r", requirements_path], as_user=TARGET_USER)
    log_ok("Entorno Python listo.")
    

    log_step("Paso 9: Estableciendo permisos de sistema...")
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


    log_step("Paso 10: Instalando servicios systemd...")
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

    log_step("¡Instalación completada!")
    log_warn("Es NECESARIO reiniciar el sistema para aplicar los cambios de hardware y red.")
    
    reboot_choice = input("¿Deseas reiniciar la Raspberry Pi ahora? (s/n): ").lower()
    if reboot_choice in ("s", "y", "si", "yes"):
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