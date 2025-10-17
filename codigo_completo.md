## Archivo: `.\deploy.py`

```python
# -*- coding: utf-8 -*-

"""
# =============================================================================
#  SCRIPT DE DESPLIEGUE AUTOMÁTICO PARA fire-truck-app
# =============================================================================
# ... (descripción igual) ...
"""

import argparse
import getpass
import os
import sys
import time

try:
    import paramiko
except ImportError:
    print("Error: La librería 'paramiko' no está instalada.")
    print("Por favor, ejecútala con: pip install paramiko")
    sys.exit(1)

# --- Configuración ---
TARGET_USER = "cosigein"
APP_DIR = f"/home/{TARGET_USER}/fire-truck-app"
LOG_DIR = f"/home/{TARGET_USER}/logs"
DATA_DIR = f"/home/{TARGET_USER}/datos"
# ---------------------

# --- Clases de utilidad para colores en la terminal ---
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def print_step(msg):
    print(f"\n{Colors.HEADER}{Colors.BOLD}>>> {msg}{Colors.ENDC}")

def print_ok(msg):
    print(f"{Colors.OKGREEN}[OK] {msg}{Colors.ENDC}")

def print_warn(msg):
    print(f"{Colors.WARNING}[WARN] {msg}{Colors.ENDC}")

def print_fail(msg):
    print(f"{Colors.FAIL}[FAIL] {msg}{Colors.ENDC}", file=sys.stderr)

def print_info(msg):
    print(f"{Colors.OKCYAN}     {msg}{Colors.ENDC}")


class SSHDeployer:
    """Gestiona la conexión SSH y la ejecución de comandos en el dispositivo remoto."""
    def __init__(self, host, user, password):
        self.host = host
        self.user = user
        self.password = password
        self.client = None
        self.sftp = None

    def connect(self):
        """Establece la conexión SSH."""
        try:
            print_info(f"Conectando a {self.host} como {self.user}...")
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(self.host, username=self.user, password=self.password, timeout=10)
            self.sftp = self.client.open_sftp()
            print_ok("Conexión SSH establecida.")
        except Exception as e:
            print_fail(f"No se pudo conectar: {e}")
            raise

    def disconnect(self):
        """Cierra la conexión SSH."""
        if self.sftp:
            self.sftp.close()
        if self.client:
            self.client.close()
        print_ok("Conexión SSH cerrada.")

    def execute(self, command, use_sudo=False, ignore_errors=False):
        """Ejecuta un comando en el dispositivo remoto."""
        print_info(f"Ejecutando: {command}")
        full_command = command
        
        if use_sudo:
            full_command = f"sudo -S -p '' {command}"

        try:
            # **CORRECCIÓN FINAL**: Quitar get_pty=True para que `sudo -S` funcione correctamente.
            stdin, stdout, stderr = self.client.exec_command(full_command)
            
            if use_sudo:
                stdin.write(self.password + '\n')
                stdin.flush()
                stdin.channel.shutdown_write()

            # Leer la salida ANTES de esperar el código de finalización.
            out = stdout.read().decode('utf-8', errors='ignore').strip()
            err = stderr.read().decode('utf-8', errors='ignore').strip()
            
            # Ahora que hemos leído la salida, podemos esperar a que el comando termine.
            exit_code = stdout.channel.recv_exit_status()
            
            if err and "Warning: " not in err:
                # En modo no-pty, sudo a menudo se queja de "no tty present". Lo ignoramos.
                if "sudo: no tty present" not in err:
                    print_warn(f"  stderr: {err}")

            if exit_code != 0 and not ignore_errors:
                error_message = f"El comando falló con código de salida {exit_code}."
                if err:
                    error_message += f" Error: {err}"
                raise Exception(error_message)
            
            return out
        except Exception as e:
            print_fail(f"Error al ejecutar el comando '{command}': {e}")
            raise
            
    def upload_file(self, local_path, remote_path):
        """Sube un archivo al dispositivo remoto."""
        try:
            print_info(f"Subiendo '{local_path}' a '{remote_path}'")
            self.sftp.put(local_path, remote_path)
        except Exception as e:
            print_fail(f"Error al subir el archivo: {e}")
            raise


def main():
    parser = argparse.ArgumentParser(description="Script de despliegue para fire-truck-app.")
    parser.add_argument("host", help="Dirección IP o hostname de la Raspberry Pi.")
    args = parser.parse_args()

    password = getpass.getpass(f"Introduce la contraseña para el usuario '{TARGET_USER}' en {args.host}: ")
    repo_url = input(f"{Colors.OKCYAN}Introduce la URL SSH de tu repositorio Git (ej. git@github.com:p12regaf/fire-truck-app.git): {Colors.ENDC}")
    git_branch = input(f"{Colors.OKCYAN}Introduce el nombre de la rama a desplegar (ej. main): {Colors.ENDC}").strip() or "main"

    deployer = None
    try:
        deployer = SSHDeployer(args.host, TARGET_USER, password)
        deployer.connect()

        # --- PASO 1: Preparación del Sistema ---
        print_step("Paso 1: Actualizando el sistema e instalando dependencias...")
        env = "DEBIAN_FRONTEND=noninteractive"
        deployer.execute(f"{env} apt-get update", use_sudo=True)
        deployer.execute(f"{env} apt-get upgrade -y", use_sudo=True)
        deployer.execute(f"{env} apt-get install -y git python3-pip python3-venv can-utils", use_sudo=True)
        print_ok("Sistema preparado.")

        # --- PASO 2: Creación de Directorios ---
        print_step(f"Paso 2: Asegurando la existencia de los directorios de trabajo...")
        deployer.execute(f"mkdir -p {LOG_DIR} {DATA_DIR} {APP_DIR}")
        print_ok("Directorios creados.")
        
        # --- PASO 3: Configuración de la Deploy Key ---
        print_step("Paso 3: Configurando Deploy Key...")
        key_path = f"/home/{TARGET_USER}/.ssh/id_ed25519"
        pub_key_path = f"{key_path}.pub"
        
        deployer.execute(f"mkdir -p /home/{TARGET_USER}/.ssh")
        deployer.execute(f"chmod 700 /home/{TARGET_USER}/.ssh")

        key_exists_cmd = f"[ -f {pub_key_path} ] && echo 'exists' || echo 'not exists'"
        key_status = deployer.execute(key_exists_cmd)
        
        if 'not exists' in key_status:
            print_info("No se encontró una clave SSH existente. Generando una nueva...")
            deployer.execute(f"ssh-keygen -t ed25519 -f {key_path} -N '' -C 'fire-truck-app-deploy-key'")
            public_key = deployer.execute(f"cat {pub_key_path}")
            
            print(f"\n{Colors.WARNING}--- ACCIÓN MANUAL REQUERIDA ---{Colors.ENDC}")
            print(f"Se ha generado una nueva clave pública. Cópiala y añádela como 'Deploy Key' en tu repositorio Git.")
            print(f"Asegúrate de NO marcar 'Allow write access'.")
            print(f"{Colors.OKCYAN}{public_key}{Colors.ENDC}")
            input("\nPresiona Enter cuando hayas añadido la clave para continuar...")
        else:
            print_info("Se ha encontrado una clave SSH existente.")
            public_key = deployer.execute(f"cat {pub_key_path}")
            print(f"Asegúrate de que esta clave pública está configurada como 'Deploy Key' en tu repositorio Git:")
            print(f"{Colors.OKCYAN}{public_key}{Colors.ENDC}")
        
        git_host = repo_url.split('@')[1].split(':')[0]
        deployer.execute(f"ssh-keyscan {git_host} >> /home/{TARGET_USER}/.ssh/known_hosts", ignore_errors=True)
        deployer.execute(f"sort -u /home/{TARGET_USER}/.ssh/known_hosts -o /home/{TARGET_USER}/.ssh/known_hosts")
        print_ok("Deploy Key configurada y verificada.")

        # --- PASO 4: Clonar/Forzar Actualización del Repositorio ---
        print_step("Paso 4: Clonando o forzando la actualización del repositorio...")
        repo_check_cmd = f"[ -d {APP_DIR}/.git ] && echo 'exists' || echo 'not exists'"
        repo_status = deployer.execute(repo_check_cmd)

        if 'exists' in repo_status:
            print_info("El repositorio ya existe. Forzando actualización desde el origen...")
            force_update_cmds = f"cd {APP_DIR} && git fetch --all && git reset --hard origin/{git_branch} && git clean -fdx"
            deployer.execute(force_update_cmds)
        else:
            print_info("Clonando el repositorio...")
            deployer.execute(f"git clone --branch {git_branch} {repo_url} {APP_DIR}")

        print_info("Configurando el entorno virtual de Python...")
        deployer.execute(f"python3 -m venv {APP_DIR}/.venv")
        deployer.execute(f"{APP_DIR}/.venv/bin/pip install -r {APP_DIR}/requirements.txt")
        print_ok("Repositorio y dependencias listos.")
        
        # --- PASO 5: Configuración de Permisos ---
        print_step("Paso 5: Configurando permisos de hardware y sudo...")
        deployer.execute(f"chmod +x {APP_DIR}/scripts/check_and_install_update.sh", ignore_errors=True) # Script puede no existir
        deployer.execute(f"usermod -a -G gpio,i2c,dialout {TARGET_USER}", use_sudo=True)
        
        sudo_rule = f'{TARGET_USER} ALL=(ALL) NOPASSWD: /sbin/shutdown, /sbin/reboot'
        deployer.execute(f"echo '{sudo_rule}' | sudo tee /etc/sudoers.d/99-fire-truck-app > /dev/null")
        deployer.execute(f"chmod 0440 /etc/sudoers.d/99-fire-truck-app", use_sudo=True)
        print_ok("Permisos configurados.")

        # --- PASO 6: Configuración del Bus CAN ---
        print_step("Paso 6: Configurando bus CAN en /boot/firmware/config.txt")
        can_config = "\\n# Habilitar CAN bus (fire-truck-app)\\ndtparam=spi=on\\ndtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25"
        check_can_cmd = "grep -q 'mcp2515-can0' /boot/firmware/config.txt"
        
        try:
            # Usamos sudo aquí porque /boot/firmware/config.txt puede tener permisos restringidos
            deployer.execute(check_can_cmd, use_sudo=True)
            print_info("La configuración del bus CAN ya parece existir. Omitiendo.")
        except Exception:
             print_info("Añadiendo configuración del bus CAN a /boot/firmware/config.txt...")
             deployer.execute(f'printf "{can_config}" | sudo tee -a /boot/firmware/config.txt > /dev/null')
             print_ok("Bus CAN configurado.")
        
        # --- PASO 7: Instalación de los Servicios systemd ---
        print_step("Paso 7: Instalando servicios systemd...")
        local_service_dir = "services"
        if not os.path.isdir(local_service_dir):
            raise FileNotFoundError(f"El directorio '{local_service_dir}' no se encuentra al lado del script.")
        
        for service_file in ["app.service", "updater.service"]:
            local_path = os.path.join(local_service_dir, service_file)
            remote_tmp_path = f"/tmp/{service_file}"
            deployer.upload_file(local_path, remote_tmp_path)
            deployer.execute(f"mv {remote_tmp_path} /etc/systemd/system/", use_sudo=True)
            
        deployer.execute("systemctl daemon-reload", use_sudo=True)
        deployer.execute("systemctl enable updater.service", use_sudo=True)
        deployer.execute("systemctl enable app.service", use_sudo=True)
        print_ok("Servicios instalados y habilitados.")
        
        # --- Finalización ---
        print_step("¡Despliegue completado!")
        print_warn("Es necesario reiniciar el sistema para aplicar todos los cambios.")
        reboot_choice = input("¿Deseas reiniciar la Raspberry Pi ahora? (s/n): ").lower()
        if reboot_choice == 's':
            print_info("Reiniciando el dispositivo...")
            deployer.execute("reboot", use_sudo=True, ignore_errors=True) # Ignorar error si la conexión se corta antes de la respuesta
            time.sleep(2) 
        else:
            print_info("No se reiniciará. Recuerda hacerlo manualmente con 'sudo reboot'.")

    except Exception as e:
        print_fail(f"\nEl proceso de despliegue ha fallado: {e}")
        sys.exit(1)
    finally:
        if deployer:
            deployer.disconnect()

if __name__ == "__main__":
    main()
```

## Archivo: `.\generar_markdown_auto.py`

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

# --- CONFIGURACIÓN ---
# Nombre del archivo Markdown que se generará
OUTPUT_FILENAME = "codigo_completo.md"

# Extensiones de los archivos que queremos buscar
TARGET_EXTENSIONS = ('.py', '.service', '.txt', '.csv', '.dbc', '.sh', '.json', '.yaml', '.yml', '.html.j2', '.sh')

# Lista de carpetas raíz que quieres escanear (búsqueda recursiva).
# Usa '.' para escanear todo desde la carpeta actual.
TARGET_DIRECTORIES = [
    '.' 
]

# ¡NUEVO! Lista de carpetas que quieres EXCLUIR de la búsqueda.
# El script ignorará por completo estas carpetas y todo su contenido.
# Es ideal para carpetas de entornos virtuales, repositorios git, cachés, etc.
EXCLUDED_DIRECTORIES = [
    './.venv',          # Entorno virtual de Python
    './venv',           # Otro nombre común para entorno virtual
    './.git',           # Carpeta del repositorio Git
    './__pycache__',    # Carpetas de caché de Python
    './node_modules'    # Carpeta de dependencias de Node.js
]
# ---------------

def get_markdown_language(filename):
    """Devuelve el identificador de lenguaje para el bloque de código Markdown."""
    if filename.endswith('.py'):
        return 'python'
    if filename.endswith('.service'):
        return 'ini'
    return 'text'

def main():
    """
    Función principal que busca archivos, respetando las exclusiones, y escribe el Markdown.
    """
    try:
        # Normalizamos las rutas de exclusión para una comparación más fiable
        # os.path.normpath elimina './' y convierte '/' a '\' en Windows, etc.
        normalized_excluded_dirs = [os.path.normpath(d) for d in EXCLUDED_DIRECTORIES]

        with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as md_file:
            print(f"Generando el archivo: {OUTPUT_FILENAME}")
            print(f"Carpetas a escanear: {', '.join(TARGET_DIRECTORIES)}")
            print(f"Carpetas a excluir: {', '.join(EXCLUDED_DIRECTORIES)}")
            print(f"Extensiones buscadas: {', '.join(TARGET_EXTENSIONS)}\n")
            
            found_files = False
            for target_dir in TARGET_DIRECTORIES:
                if not os.path.isdir(target_dir):
                    print(f"¡Atención! La carpeta de inicio '{target_dir}' no existe y será omitida.")
                    continue

                for root, dirs, files in os.walk(target_dir, topdown=True):
                    # --- LÓGICA DE EXCLUSIÓN ---
                    # Modificamos la lista 'dirs' en el momento para que os.walk
                    # no entre en los directorios excluidos. Es la forma más eficiente.
                    dirs[:] = [d for d in dirs if os.path.normpath(os.path.join(root, d)) not in normalized_excluded_dirs]
                    
                    for filename in files:
                        if filename.endswith(TARGET_EXTENSIONS):
                            full_path = os.path.join(root, filename)
                            found_files = True
                            
                            print(f"  -> Añadiendo {full_path}...")
                            
                            md_file.write(f"## Archivo: `{full_path}`\n\n")
                            lang = get_markdown_language(filename)
                            md_file.write(f"```{lang}\n")

                            try:
                                with open(full_path, 'r', encoding='utf-8', errors='ignore') as src_file:
                                    content = src_file.read()
                                    md_file.write(content)
                            except Exception as e:
                                error_message = f"Error al leer el archivo: {e}"
                                md_file.write(error_message)
                                print(f"  -> ¡Error! No se pudo leer el archivo {full_path}: {e}")

                            md_file.write("\n```\n\n")
            
            if not found_files:
                 print("\nNo se encontraron archivos con las extensiones especificadas en las carpetas de destino (después de aplicar exclusiones).")
            
            print(f"\n¡Proceso completado! El archivo '{OUTPUT_FILENAME}' ha sido generado.")

    except IOError as e:
        print(f"Error: No se pudo crear o escribir en el archivo de salida '{OUTPUT_FILENAME}'.")
        print(f"Detalle del error: {e}")

if __name__ == "__main__":
    main()
```

## Archivo: `.\main.py`

```python
import argparse
import logging
import signal
import sys
import time
from queue import Queue

from src.core.app_controller import AppController
from src.utils.config_loader import ConfigLoader
from src.utils.unified_logger import setup_logging

# Para la GUI, solo se importa si es necesario para evitar dependencias
# innecesarias en el modo de servicio.
try:
    from src.gui.main_window import MainWindow
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


def main():
    """Punto de entrada principal de la aplicación fire-truck-app."""
    parser = argparse.ArgumentParser(description="Sistema de Monitorización de Vehículos (fire-truck-app)")
    parser.add_argument("--config", default="config/config.yaml", help="Ruta al archivo de configuración.")
    parser.add_argument("--gui", action="store_true", help="Lanzar la aplicación con la interfaz gráfica.")
    args = parser.parse_args()

    # 1. Cargar configuración
    try:
        config_loader = ConfigLoader(args.config)
        config = config_loader.get_config()
    except FileNotFoundError:
        print(f"Error: El archivo de configuración '{args.config}' no fue encontrado.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error al cargar la configuración: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. Configurar logging centralizado
    setup_logging(config)
    log = logging.getLogger(__name__)
    log.info("Iniciando aplicación fire-truck-app...")

    # 3. Crear el controlador principal de la aplicación
    app_controller = AppController(config)

    # 4. Configurar manejo de señales para un apagado ordenado
    def signal_handler(sig, frame):
        log.warning(f"Señal {signal.Signals(sig).name} recibida. Iniciando apagado...")
        app_controller.shutdown()
        # Si se ejecuta la GUI, también se debe cerrar.
        if 'main_window' in locals() and main_window.is_running():
            main_window.close()

    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler) # systemctl stop

    # 5. Iniciar los servicios del backend
    app_controller.start()

    # 6. Decidir si arrancar en modo GUI o headless
    use_gui = args.gui or config.get('system', {}).get('start_with_gui', False)

    if use_gui:
        if not GUI_AVAILABLE:
            log.error("Se solicitó la GUI, pero los componentes (ej. tkinter) no están disponibles. Saliendo.")
            app_controller.shutdown()
            sys.exit(1)
        
        log.info("Iniciando en modo GUI...")
        main_window = MainWindow(app_controller)
        main_window.run() # Esto bloquea hasta que la ventana se cierra
        log.info("La ventana de la GUI se ha cerrado.")
        # Asegurarse de que el backend se apaga si la GUI se cierra primero
        if not app_controller.is_shutting_down():
            app_controller.shutdown()
            
    else:
        log.info("Iniciando en modo headless (servicio).")
        # Mantener el hilo principal vivo para que los hilos de trabajo puedan operar.
        # El bucle se romperá cuando el evento de apagado se active.
        while not app_controller.is_shutting_down():
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                # Esto es redundante si signal_handler funciona, pero es una buena práctica
                break

    log.info("La aplicación fire-truck-app se ha detenido limpiamente.")
    # Limpieza final de GPIO si se usó
    try:
        import RPi.GPIO as GPIO
        GPIO.cleanup()
        log.info("Limpieza de GPIO completada.")
    except (RuntimeError, ImportError):
        # No hacer nada si RPi.GPIO no está disponible o ya fue limpiado
        pass
    sys.exit(0)

if __name__ == "__main__":
    main()

```

## Archivo: `.\requirements.txt`

```text
PyYAML
python-can
pyserial
RPi.GPIO
smbus2
```

## Archivo: `.\config\config.yaml`

```text
# -----------------------------------------------------------------
# Configuración Unificada para la Aplicación fire-truck-app
# -----------------------------------------------------------------
system:
  device_user: "cosigein"
  device_number: "001"
  log_level: "DEBUG"       # DEBUG, INFO, WARNING, ERROR, CRITICAL
  start_with_gui: false 

  power_monitor:
    enabled: true
    # Pin GPIO (en modo BCM) conectado a la alimentación del vehículo.
    # El pin 36 (BOARD) es el 16 (BCM).
    pin: 16 
    # Cómo está conectado el pin. PULL_UP significa que el pin está en ALTO (HIGH)
    # cuando el motor está encendido, y va a BAJO (LOW) cuando se apaga.
    pull_up_down: "PUD_UP" 
  
  alarm_monitor:
    enabled: true
    # Pin GPIO (BCM) para la alarma de la fuente de alimentación. Pin 37 (BOARD) -> 26 (BCM).
    pin: 26
    # La alarma se activa (TRUE) cuando el pin pasa a estado HIGH.
    pull_up_down: "PUD_DOWN"
  
  reboot_monitor:
    enabled: true
    # Pin GPIO (BCM) para la señal de "handshake" o "arranque OK" a la fuente de alimentación.
    # Al arrancar, la aplicación pondrá este pin en estado ALTO (HIGH).
    # Pin 32 (BOARD) -> 12 (BCM).
    pin: 12
    # El valor 'pull_up_down' se ignora para este pin, ya que se configura como SALIDA.
    pull_up_down: "PUD_UP"

paths:
  data_root: "/home/cosigein/datos"
  app_logs: "/home/cosigein/logs"
  # Un único archivo para gestionar todos los contadores de sesión.
  session_db: "/home/cosigein/fire-truck-app_session_data.json"

ftp:
  enabled: true
  host: "***REMOVED***"
  port: 21
  user: "doback"
  pass: "***REMOVED***"
  # Intervalo para buscar y subir archivos de log completos de días anteriores.
  log_upload_interval_sec: 300
  # Intervalo para subir los archivos de estado en tiempo real.
  realtime_upload_interval_sec: 30
  # Los archivos no se subirán si se han modificado en los últimos X segundos.
  upload_safety_margin_sec: 120

data_sources:
  can:
    enabled: true
    interface: "can0"
    bitrate: 500000
    inactivity_timeout_sec: 10
  
    # ID de la petición de diagnóstico estándar (broadcast)
    request_id: 0x7DF 
    # Intervalo en segundos entre cada consulta para no saturar el bus.
    query_interval_sec: 0.2
    # Tiempo máximo en segundos para esperar una respuesta a una consulta.
    response_timeout_sec: 0.5

    # Lista de PGNs (Parameter Group Numbers) de J1939 a escuchar.
    # La aplicación filtrará y parseará solo los mensajes con estos PGNs.
    pgn_to_listen:
      - name: "Engine Speed"
        pgn: 61444  # 0xF004
        unit: "rpm"
      
      - name: "Wheel-Based Vehicle Speed"
        pgn: 65265  # 0xFEF1
        unit: "km/h"
        
      - name: "Engine Coolant Temperature"
        pgn: 65262  # 0xFEEE
        unit: "°C"
        
      - name: "Accelerator Pedal Position 1"
        pgn: 61443 # 0xF003
        unit: "%"

  gps:
    enabled: true
    i2c_bus: 1
    i2c_addr: 0x42
    inactivity_timeout_sec: 600

  estabilometro:
    enabled: true
    serial_port: "/dev/serial0"
    baud_rate: 115200
    session_timeout_sec: 15

  gpio_rotativo:
    enabled: true
    pin: 22
    log_period_sec: 1
```

## Archivo: `.\scripts\check_and_install_update.sh`

```text
#!/bin/bash

APP_DIR="/home/cosigein/fire-truck-app"
LOG_FILE="/home/cosigein/logs/updater.log"
APP_SERVICE="app.service"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - [UPDATER] - $1" | tee -a $LOG_FILE
}

log "--- Iniciando script de comprobación e instalación ---"

# Navegar al directorio de la aplicación
cd $APP_DIR || { log "ERROR: No se pudo acceder a $APP_DIR"; exit 1; }

# Comprobar conectividad
if ! ping -c 1 -W 5 8.8.8.8 &> /dev/null; then
    log "No hay conexión a internet. Saliendo."
    exit 0
fi
log "Conexión a internet detectada."

# Comprobar estado de Git como el usuario correcto
log "Ejecutando comprobaciones de Git como usuario 'cosigein'..."
GIT_OUTPUT=$(sudo -u cosigein git remote update 2>&1 && sudo -u cosigein git status -uno 2>&1)

if [[ $GIT_OUTPUT == *"Your branch is behind"* ]]; then
    log "¡Nueva versión detectada! Iniciando proceso de actualización."

    log "Deteniendo el servicio $APP_SERVICE..."
    systemctl stop $APP_SERVICE

    log "Ejecutando 'git pull' como 'cosigein'..."
    if ! sudo -u cosigein git pull; then
        log "ERROR: 'git pull' falló. Se reintentará en el próximo arranque."
        systemctl start $APP_SERVICE
        exit 1
    fi

    log "Instalando/actualizando dependencias..."
    /home/cosigein/fire-truck-app/.venv/bin/pip install -r requirements.txt

    log "¡Actualización completada! Reiniciando el sistema para aplicar los cambios."
    reboot

elif [[ $GIT_OUTPUT == *"Your branch is up to date"* ]]; then
    log "La aplicación ya está actualizada."
    exit 0
else
    log "Estado de Git no reconocido o error. Saliendo."
    log "Salida de Git: $GIT_OUTPUT"
    exit 1
fi
```

## Archivo: `.\services\app.service`

```ini
[Unit]
Description=Servicio de Monitorización de Vehículos (fire-truck-app)
After=network.target

[Service]
User=cosigein
Group=cosigein
WorkingDirectory=/home/cosigein/fire-truck-app
# Inicia el servicio en modo "headless". Para la GUI, ejecutar manualmente.
ExecStart=/home/cosigein/fire-truck-app/.venv/bin/python3 /home/cosigein/fire-truck-app/main.py
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

## Archivo: `.\services\updater.service`

```ini
[Unit]
Description=Comprueba e instala actualizaciones para fire-truck-app
After=network-online.target
Wants=network-online.target
# Asegúrate de que tu app principal no arranque hasta que el updater termine
Before=app.service

[Service]
Type=oneshot
TimeoutStartSec=5min
ExecStart=/home/cosigein/fire-truck-app/scripts/check_and_install_update.sh

[Install]
WantedBy=multi-user.target
```

## Archivo: `.\src\__init__.py`

```python

```

## Archivo: `.\src\core\alarm_monitor.py`

```python
import logging
import subprocess
import threading
import time
import RPi.GPIO as GPIO
import os

log = logging.getLogger(__name__)

class AlarmMonitor(threading.Thread):
    """
    Un hilo que monitoriza un pin GPIO para detectar una señal de ALARMA
    de la fuente de alimentación (ej. sobretemperatura). Cuando se detecta,
    inicia la secuencia de apagado controlado de la aplicación y el sistema.
    """

    def __init__(self, config: dict, app_controller):
        super().__init__(name="AlarmMonitor")
        self.app_controller = app_controller
        self.shutdown_event = app_controller.shutdown_event
        
        alarm_config = config.get('system', {}).get('alarm_monitor', {})
        self.pin = alarm_config.get('pin')
        
        pull_config_str = alarm_config.get('pull_up_down', 'PUD_DOWN').upper()

        try:
            self.pull_up_down = getattr(GPIO, pull_config_str)
        except AttributeError:
            log.critical(f"Valor de 'pull_up_down' inválido en config de AlarmMonitor: '{pull_config_str}'.")
            self.pin = None
            return

        # La lógica de disparo ahora se basa en la configuración
        if self.pull_up_down == GPIO.PUD_UP:
            self.trigger_state = GPIO.LOW
        else: # GPIO.PUD_DOWN
            self.trigger_state = GPIO.HIGH

    def run(self):
        if not self._setup():
            log.error("AlarmMonitor no pudo inicializarse. El hilo terminará.")
            return

        log.info(f"AlarmMonitor iniciado. Vigilando pin {self.pin} para estado '{'HIGH' if self.trigger_state == GPIO.HIGH else 'LOW'}'...")

        while not self.shutdown_event.is_set():
            if GPIO.input(self.pin) == self.trigger_state:
                log.critical("¡ALARMA DE FUENTE DE ALIMENTACIÓN DETECTADA! Iniciando apagado de emergencia.")
                
                # 1. Indicar a la aplicación que se apague (esto ejecutará la subida final)
                self.app_controller.shutdown()
                
                # 2. Esperar a que la aplicación termine su secuencia de apagado.
                log.info("AlarmMonitor esperando a que los servicios de la app finalicen...")
                self.app_controller.processor_thread.join(timeout=60.0)
                
                if self.app_controller.processor_thread.is_alive():
                    log.error("El procesador de la app no terminó a tiempo. Forzando apagado del sistema.")
                else:
                    log.info("Los servicios de la app han finalizado correctamente.")

                # 3. Apagar el sistema operativo
                self._shutdown_system()
                break

            self.shutdown_event.wait(1.0) # Comprobar el estado cada segundo

        self._cleanup()
        log.info("AlarmMonitor detenido.")

    def _setup(self) -> bool:
        if self.pin is None:
            log.critical("No se ha especificado un pin GPIO para AlarmMonitor en la configuración.")
            return False
        return True

    def _cleanup(self):
        log.debug("AlarmMonitor: limpieza finalizada.")

    # Modifica el método de apagado/reinicio del sistema
    def _shutdown_system(self):
        log.critical("APAGANDO EL SISTEMA OPERATIVO AHORA.")
        try:
            # Descomenta para producción
            subprocess.call(['sudo', 'shutdown', 'now'])
            
            # Línea para pruebas
            # print("SIMULACIÓN: sudo shutdown now")
            log.info("Comando de apagado del sistema ejecutado.")
        except Exception as e:
            log.critical(f"Fallo al ejecutar el comando de apagado del sistema: {e}")
```

## Archivo: `.\src\core\app_controller.py`

```python
import logging
import threading
from queue import Queue, Empty
import time
from datetime import datetime
import os
import ftplib
import RPi.GPIO as GPIO

from .session_manager import SessionManager
from src.data_acquirers.can_acquirer import CANAcquirer
from src.data_acquirers.gps_acquirer import GPSAcquirer
from src.data_acquirers.imu_acquirer import IMUAcquirer
from src.data_acquirers.gpio_acquirer import GPIOAcquirer
from src.transmitters.ftp_transmitter import FTPTransmitter
from .power_monitor import PowerMonitor
from .alarm_monitor import AlarmMonitor
from .reboot_monitor import RebootMonitor

log = logging.getLogger(__name__)

class AppController:
    def __init__(self, config: dict):
        self.config = config
        self.data_queue = Queue()
        self.shutdown_event = threading.Event()
        self.workers = []
        self.active_data_types = []
        self.latest_data = {}
        self.data_lock = threading.Lock()
        
        self.session_manager = SessionManager(config)

        # Instanciar todos los componentes (trabajadores)
        self._initialize_workers()
        
        # Hilo para procesar la cola de datos
        self.processor_thread = threading.Thread(
            target=self._process_data_queue,
            name="DataProcessor"
        )
        
    def _initialize_workers(self):
        """Crea instancias de todos los recolectores y transmisores."""
        sources_config = self.config.get('data_sources', {})
        
        if sources_config.get('can', {}).get('enabled', False):
            self.workers.append(CANAcquirer(self.config, self.data_queue, self.shutdown_event))
            self.active_data_types.append('can')
        
        if sources_config.get('gps', {}).get('enabled', False):
            self.workers.append(GPSAcquirer(self.config, self.data_queue, self.shutdown_event))
            self.active_data_types.append('gps')
            
        if sources_config.get('estabilometro', {}).get('enabled', False):
            self.workers.append(IMUAcquirer(self.config, self.data_queue, self.shutdown_event))
            self.active_data_types.append('estabilometro')
            
        if sources_config.get('gpio_rotativo', {}).get('enabled', False):
            self.workers.append(GPIOAcquirer(self.config, self.data_queue, self.shutdown_event))
            self.active_data_types.append('rotativo')

        if self.config.get('ftp', {}).get('enabled', False):
            log.info("FTP Transmitter está habilitado. Inicializando...")
            self.workers.append(FTPTransmitter(self.config, self.shutdown_event, self.session_manager))
        
        if self.config.get('system', {}).get('power_monitor', {}).get('enabled', False):
            log.info("Power Monitor está habilitado. Inicializando...")
            self.workers.append(PowerMonitor(self.config, self))

        if self.config.get('system', {}).get('alarm_monitor', {}).get('enabled', False):
            log.info("Alarm Monitor está habilitado. Inicializando...")
            self.workers.append(AlarmMonitor(self.config, self))

        if self.config.get('system', {}).get('reboot_monitor', {}).get('enabled', False):
            log.info("Reboot Monitor está habilitado. Inicializando...")
            self.workers.append(RebootMonitor(self.config, self))
            
        log.info(f"{len(self.workers)} trabajadores inicializados.")
        log.info(f"Tipos de datos activos: {self.active_data_types}")

        self._setup_gpio_pins()
        
        # Crear los directorios de datos necesarios (ej. /datos/CAN, /datos/GPS)
        self.session_manager.ensure_data_directories(self.active_data_types)


    def _setup_gpio_pins(self):
        """
        Configura todos los pines GPIO de los monitores de forma centralizada
        para evitar condiciones de carrera.
        """
        log.info("Configurando pines GPIO de forma centralizada...")
        try:
            GPIO.setmode(GPIO.BCM)
            # Desactivar advertencias sobre canales en uso
            GPIO.setwarnings(False) 

            # Configurar cada pin que se va a usar
            for worker in self.workers:
                if isinstance(worker, (PowerMonitor, AlarmMonitor, RebootMonitor)):
                    if worker.pin is not None:
                        if isinstance(worker, RebootMonitor):
                            # El RebootMonitor actúa como un actuador, estableciendo una señal de 'OK'
                            log.info(f"Configurando pin {worker.pin} para {worker.name} como SALIDA.")
                            GPIO.setup(worker.pin, GPIO.OUT)
                        else:
                            # Los otros monitores son sensores de entrada
                            log.info(f"Configurando pin {worker.pin} para {worker.name} como ENTRADA con pull_up_down={worker.pull_up_down}")
                            GPIO.setup(worker.pin, GPIO.IN, pull_up_down=worker.pull_up_down)
            
            log.info("Configuración centralizada de GPIO completada.")
        except Exception as e:
            log.critical(f"FALLO CRÍTICO durante la configuración centralizada de GPIO: {e}")

    def _write_session_headers(self):
        """
        Escribe la cabecera de la nueva sesión en cada archivo de log diario
        y prepara los archivos RealTime.
        """
        log.info("Escribiendo cabeceras de sesión en archivos de log diarios...")
        for data_type in self.active_data_types:
            try:
                # Escribir cabecera en el archivo de log principal
                log_path = self.session_manager.get_log_path(data_type)
                session_header = self.session_manager.get_session_header(data_type)
                with open(log_path, 'a') as f:
                    f.write(session_header)
                
                # Preparar el archivo de tiempo real
                rt_path = self.session_manager.get_realtime_log_path(data_type)
                with open(rt_path, 'w') as f:
                    f.write(f"Session {self.session_manager.current_session_id} started. Waiting for data...\n")
                    
            except IOError as e:
                log.error(f"No se pudo escribir la cabecera para '{data_type}': {e}")
        log.info("Escritura de cabeceras de sesión completada.")

    def start(self):
        """Inicia todos los hilos de trabajo y el procesador de datos."""
        log.info("Iniciando todos los servicios del controlador...")
        
        self._write_session_headers()
        
        self.processor_thread.start()
        for worker in self.workers:
            worker.start()
        log.info("Todos los servicios del controlador han sido iniciados.")
        
    def shutdown(self):
        """Detiene de forma ordenada todos los hilos."""
        if self.shutdown_event.is_set():
            return
            
        log.info("Iniciando secuencia de apagado...")
        self.shutdown_event.set()

        # Esperar a que los hilos de trabajo terminen
        for worker in self.workers:
            if isinstance(worker, (PowerMonitor, AlarmMonitor, RebootMonitor)):
                continue
            worker.join(timeout=5.0)
            if worker.is_alive():
                log.warning(f"El trabajador {worker.name} no se detuvo a tiempo.")
        
        # Esperar al hilo del procesador
        self.processor_thread.join(timeout=5.0)
        if self.processor_thread.is_alive():
            log.warning("El hilo procesador no se detuvo a tiempo.")
            
        log.info("Secuencia de apagado completada.")

    def is_shutting_down(self) -> bool:
        return self.shutdown_event.is_set()

    def _process_data_queue(self):
        """

        Bucle principal que consume la cola de datos, los registra y actualiza el estado.
        """
        log.info("Procesador de datos iniciado.")
        while not self.shutdown_event.is_set():
            try:
                data_packet = self.data_queue.get(timeout=1)
                
                data_type = data_packet['type']
                timestamp = data_packet['timestamp']
                data_content = data_packet['data']

                with self.data_lock:
                    self.latest_data[data_type] = data_packet
                
                log_file_path = self.session_manager.get_log_path(data_type)
                try:
                    # El modo 'a' (append) es clave para el log diario
                    with open(log_file_path, 'a') as f:
                        log_line = f"{timestamp};{data_content}\n"
                        f.write(log_line)
                except Exception as e:
                    log.error(f"No se pudo escribir en el log para {data_type}: {e}")

                self._update_realtime_file(data_type, data_packet)

            except Empty:
                continue
            except Exception as e:
                log.error(f"Error inesperado en el procesador de datos: {e}")

        log.info("Procesador de datos detenido.")
    
    def _update_realtime_file(self, data_type: str, data_packet: dict):
        """Actualiza el archivo _RealTime.txt para un tipo de dato específico."""
        try:
            rt_path = self.session_manager.get_realtime_log_path(data_type)
            with open(rt_path, 'w') as f:
                f.write(f"Timestamp: {data_packet['timestamp']}\n")
                f.write(f"Data: {data_packet['data']}\n")
        except Exception as e:
            log.error(f"No se pudo actualizar el archivo RealTime para {data_type}: {e}")

    def get_latest_data(self) -> dict:
        """Devuelve una copia del último estado de los datos de forma segura."""
        with self.data_lock:
            return self.latest_data.copy()

    def get_service_status(self) -> dict:
        """Devuelve el estado de cada trabajador (hilo)."""
        status = {}
        for worker in self.workers:
            status[worker.name] = "Running" if worker.is_alive() else "Stopped"
        status[self.processor_thread.name] = "Running" if self.processor_thread.is_alive() else "Stopped"
        return status
```

## Archivo: `.\src\core\power_monitor.py`

```python
import logging
import subprocess
import threading
import time
import RPi.GPIO as GPIO 
import os

log = logging.getLogger(__name__)

class PowerMonitor(threading.Thread):
    """
    Un hilo que monitoriza un pin GPIO para detectar una señal de apagado del vehículo.
    Cuando se detecta, inicia la secuencia de apagado controlado de la aplicación
    y, finalmente, apaga el sistema operativo.
    """

    def __init__(self, config: dict, app_controller):
        super().__init__(name="PowerMonitor")
        self.app_controller = app_controller
        self.shutdown_event = app_controller.shutdown_event
        
        power_config = config.get('system', {}).get('power_monitor', {})
        self.pin = power_config.get('pin')
        
        pull_config_str = power_config.get('pull_up_down', 'PUD_UP').upper()
        
        try:
            self.pull_up_down = getattr(GPIO, pull_config_str)
        except AttributeError:
            log.critical(f"Valor de 'pull_up_down' inválido en la configuración: '{pull_config_str}'.")
            log.critical("Debe ser 'PUD_UP' o 'PUD_DOWN'.")
            self.pin = None 
            return

        # La lógica de disparo ahora se basa en la configuración
        if self.pull_up_down == GPIO.PUD_UP:
            self.trigger_state = GPIO.LOW  # Con pull-up, el pin está en HIGH y se dispara en LOW.
        else: # GPIO.PUD_DOWN
            self.trigger_state = GPIO.HIGH

    def run(self):
        if not self._setup():
            log.error("PowerMonitor no pudo inicializarse. El hilo terminará.")
            return

        log.info(f"PowerMonitor iniciado. Vigilando pin {self.pin} para estado '{'LOW' if self.trigger_state == GPIO.LOW else 'HIGH'}'...")

        while not self.shutdown_event.is_set():
            if GPIO.input(self.pin) == self.trigger_state:
                log.critical("¡Señal de apagado del vehículo detectada! Iniciando apagado controlado.")
                
                # 1. Indicar a la aplicación que se apague (esto ejecutará la subida final)
                self.app_controller.shutdown()
                
                # 2. Esperar a que la aplicación termine su secuencia de apagado.
                #    Esperamos por el hilo del procesador de datos, que es uno de los últimos en parar.
                log.info("PowerMonitor esperando a que los servicios de la app finalicen...")
                self.app_controller.processor_thread.join(timeout=60.0) # Espera hasta 60s
                
                if self.app_controller.processor_thread.is_alive():
                    log.error("El procesador de la app no terminó a tiempo. Forzando apagado del sistema.")
                else:
                    log.info("Los servicios de la app han finalizado correctamente.")

                # 3. Apagar el sistema operativo
                self._shutdown_system()
                break # Salir del bucle

            self.shutdown_event.wait(1.0) # Comprobar el estado cada segundo

        self._cleanup()
        log.info("PowerMonitor detenido.")

    def _setup(self) -> bool:
        # --- BLOQUE ELIMINADO ---
        # Ya no se necesita la comprobación de GPIO_AVAILABLE
        
        if self.pin is None:
            log.critical("No se ha especificado un pin GPIO para PowerMonitor en la configuración.")
            return False
        return True

    def _cleanup(self):
        log.debug("PowerMonitor: limpieza finalizada.")

    # Modifica el método de apagado/reinicio del sistema
    def _shutdown_system(self):
        log.critical("APAGANDO EL SISTEMA OPERATIVO AHORA.")
        try:
            # Descomenta para producción
            subprocess.call(['sudo', 'shutdown', 'now'])
            
            # Línea para pruebas
            # print("SIMULACIÓN: sudo shutdown now")
            log.info("Comando de apagado del sistema ejecutado.")
        except Exception as e:
            log.critical(f"Fallo al ejecutar el comando de apagado del sistema: {e}")
```

## Archivo: `.\src\core\reboot_monitor.py`

```python
import logging
import subprocess
import threading
import time
import RPi.GPIO as GPIO
import os

log = logging.getLogger(__name__)

class RebootMonitor(threading.Thread):
    """
    Gestiona la señal de 'handshake' con la fuente de alimentación.
    Al arrancar, este hilo pone un pin GPIO en estado ALTO (HIGH) para notificar
    a la fuente de alimentación que la Raspberry Pi ha arrancado correctamente.
    Si esta señal no se establece, la fuente podría reiniciar el sistema.
    """

    def __init__(self, config: dict, app_controller):
        super().__init__(name="RebootMonitor")
        self.app_controller = app_controller
        self.shutdown_event = app_controller.shutdown_event
        
        reboot_config = config.get('system', {}).get('reboot_monitor', {})
        self.pin = reboot_config.get('pin')
        # El valor de pull_up_down se ignora ya que el pin es de salida.
        self.pull_up_down = None

    def run(self):
        if not self._setup():
            log.error("RebootMonitor no pudo inicializarse. El hilo terminará.")
            return

        # No hay bucle. La única tarea es poner el pin en ALTO.
        # La configuración del pin como salida se hace en AppController.
        try:
            GPIO.output(self.pin, GPIO.HIGH)
            log.info(f"RebootMonitor: Pin {self.pin} establecido en ALTO (HIGH) como señal de arranque correcto para la fuente.")
        except Exception as e:
            log.critical(f"RebootMonitor: No se pudo establecer el pin {self.pin} en ALTO: {e}")
            
        # El trabajo de este hilo ha terminado. El pin se mantendrá en ALTO.
        log.info("RebootMonitor ha completado su tarea y el hilo finalizará.")

    def _setup(self) -> bool:
        if self.pin is None:
            log.critical("No se ha especificado un pin GPIO para RebootMonitor en la configuración.")
            return False
        return True

    def _cleanup(self):
        # La limpieza de GPIO es global, no hay nada que hacer aquí.
        log.debug("RebootMonitor: limpieza finalizada.")
```

## Archivo: `.\src\core\session_manager.py`

```python
import json
import logging
import os
from datetime import datetime
from threading import Lock

log = logging.getLogger(__name__)

class SessionManager:
    """
    Gestiona la creación de archivos de log diarios y el versionado de sesiones
    dentro de esos archivos.
    """
    # Mapeo de tipos de datos internos a los nombres de carpeta/fichero deseados
    DATA_TYPE_MAP = {
        "can": "CAN",
        "gps": "GPS",
        "estabilometro": "ESTABILIDAD",
        "rotativo": "ROTATIVO"
    }

    def __init__(self, config: dict):
        self.config = config
        paths_config = config.get('paths', {})
        system_config = config.get('system', {})

        self.data_root = paths_config.get('data_root', '/tmp/fire-truck-app_data')
        self.db_path = paths_config.get('session_db', '/tmp/fire-truck-app_session.json')
        
        device_number = system_config.get('device_number', '000')
        # Construimos el nombre del dispositivo según el formato requerido
        self.device_name = f"DOBACK{device_number}"

        self.lock = Lock()
        
        now = datetime.now()
        self.today_str_ymd = now.strftime('%Y%m%d') # Formato para nombre de archivo
        self.session_time = now # Guardamos el objeto datetime para la cabecera

        self.current_session_id = self._initialize_session()
        log.info(f"Sesión activa: {self.current_session_id} para el día {self.today_str_ymd}.")

    def _load_session_db(self) -> dict:
        """Carga el estado de la sesión desde el archivo JSON."""
        if not os.path.exists(self.db_path):
            log.warning(f"Archivo de sesión no encontrado en {self.db_path}. Creando uno nuevo.")
            return {"session_counters": {}}
        try:
            with open(self.db_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.error(f"No se pudo cargar o parsear el archivo de sesión. Creando uno nuevo. Error: {e}")
            return {"session_counters": {}}

    def _save_session_db(self, session_data: dict):
        """Guarda el estado actual de la sesión en el archivo JSON."""
        try:
            with open(self.db_path, 'w') as f:
                json.dump(session_data, f, indent=4)
        except IOError as e:
            log.error(f"No se pudo guardar el archivo de sesión en {self.db_path}: {e}")
            
    def _initialize_session(self) -> int:
        """
        Determina el ID de la sesión actual.
        Si es un nuevo día, el contador de sesión se resetea a 1.
        Si es el mismo día, el contador se incrementa.
        """
        with self.lock:
            session_data = self._load_session_db()
            counters = session_data.get("session_counters", {})
            
            last_session_today = counters.get(self.today_str_ymd, 0)
            new_session_id = last_session_today + 1
            
            counters[self.today_str_ymd] = new_session_id
            session_data["session_counters"] = counters
            self._save_session_db(session_data)
            
            return new_session_id

    def _get_data_type_name(self, internal_type: str) -> str:
        """Devuelve el nombre de tipo de dato formateado (ej. 'CAN', 'ESTABILIDAD')."""
        return self.DATA_TYPE_MAP.get(internal_type, internal_type.upper())

    def ensure_data_directories(self, active_data_types: list):
        """Crea los directorios base para cada tipo de dato si no existen."""
        log.info("Asegurando la existencia de directorios de datos...")
        for data_type in active_data_types:
            type_name = self._get_data_type_name(data_type)
            dir_path = os.path.join(self.data_root, type_name)
            try:
                os.makedirs(dir_path, exist_ok=True)
            except OSError as e:
                log.critical(f"No se pudo crear el directorio de datos '{dir_path}': {e}")

    def get_log_path(self, data_type: str) -> str:
        """
        Obtiene la ruta del archivo de log diario para un tipo de dato.
        Ej: /datos/CAN/CAN_DOBACK001_20251001.log
        """
        type_name = self._get_data_type_name(data_type)
        filename = f"{type_name}_{self.device_name}_{self.today_str_ymd}.log"
        return os.path.join(self.data_root, type_name, filename)

    def get_realtime_log_path(self, data_type: str) -> str:
        """
        Obtiene la ruta para el archivo de estado en tiempo real.
        Ej: /datos/CAN/CAN_DOBACK001_RealTime.txt
        """
        type_name = self._get_data_type_name(data_type)
        filename = f"{type_name}_{self.device_name}_RealTime.txt"
        return os.path.join(self.data_root, type_name, filename)

    def get_session_header(self, data_type: str) -> str:
        """
        Genera la cabecera de la sesión para ser escrita en el archivo de log.
        Ej: ESTABILIDAD;01/10/2025 09:36:54;DOBACK024;Sesión:1;
        """
        type_name = self._get_data_type_name(data_type)
        timestamp_str = self.session_time.strftime('%d/%m/%Y %H:%M:%S')
        header = (
            f"\n{type_name};{timestamp_str};{self.device_name};"
            f"Sesión:{self.current_session_id};\n"
        )
        return header
```

## Archivo: `.\src\core\__init__.py`

```python

```

## Archivo: `.\src\data_acquirers\base_acquirer.py`

```python
import abc
import threading
import logging
from datetime import datetime
from queue import Queue

log = logging.getLogger(__name__)

class BaseAcquirer(threading.Thread, abc.ABC):
    """
    Clase base abstracta para todos los recolectores de datos.
    Gestiona el ciclo de vida del hilo y define la interfaz común.
    """
    def __init__(self, config: dict, data_queue: "Queue", shutdown_event: threading.Event, name: str, config_key: str):
        super().__init__(name=name)
        self.config = config.get('data_sources', {}).get(config_key, {})
        if not self.config:
            log.warning(f"No se encontró la sección de configuración '{config_key}' para el trabajador {name}.")
        
        self.system_config = config
        self.data_queue = data_queue
        self.shutdown_event = shutdown_event
        log.info(f"Inicializando {self.name}...")

    def run(self):
        """
        Método principal del hilo. Llama al método de inicialización específico
        y luego entra en el bucle de adquisición.
        """
        log.info(f"Iniciando hilo para {self.name}.")
        if not self._setup():
            log.error(f"{self.name} no pudo inicializarse. El hilo terminará.")
            return
            
        while not self.shutdown_event.is_set():
            try:
                self._acquire_data()
            except Exception as e:
                log.error(f"Error no controlado en el bucle de adquisición de {self.name}: {e}")
                self.shutdown_event.wait(5.0)

        self._cleanup()
        log.info(f"Hilo para {self.name} detenido limpiamente.")

    @abc.abstractmethod
    def _setup(self) -> bool:
        pass

    @abc.abstractmethod
    def _acquire_data(self):
        pass
        
    @abc.abstractmethod
    def _cleanup(self):
        pass

    def _create_data_packet(self, data_type: str, data: any) -> dict:
        """Crea un paquete de datos estandarizado."""
        return {
            "type": data_type,
            "timestamp": datetime.now().isoformat(),
            "data": data
        }
```

## Archivo: `.\src\data_acquirers\can_acquirer.py`

```python
import logging
import time
from typing import Optional, Dict, Any
import can

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class CANAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="CANAcquirer", config_key="can")
        self.bus = None
        
        # Cargar configuración específica de J1939
        self.pgn_config = self.config.get('pgn_to_listen', [])
        
        # Crear un mapa para búsqueda rápida de PGN a su configuración
        self.pgn_map = {item['pgn']: item for item in self.pgn_config}
        
    def _setup(self) -> bool:
        if not self.pgn_config:
            log.warning("CANAcquirer está habilitado pero no se han definido 'pgn_to_listen' en la configuración. El hilo no hará nada.")
            return True # No es un error fatal

        try:
            interface = self.config.get('interface', 'can0')
            bitrate = self.config.get('bitrate', 250000)
            
            # --- Configuración de filtros para J1939 ---
            # Un ID de J1939 (29 bits) contiene el PGN en los bits 8-25.
            # La máscara 0x1FFFF00 aísla estos bits, ignorando la prioridad y la dirección de origen.
            can_filters = []
            for pgn_item in self.pgn_config:
                pgn = pgn_item['pgn']
                # El ID a filtrar se construye desplazando el PGN a su posición en el ID de 29 bits.
                can_id = pgn << 8
                can_filters.append({"can_id": can_id, "can_mask": 0x1FFFF00, "extended": True})

            self.bus = can.interface.Bus(channel=interface, bustype='socketcan', bitrate=bitrate, can_filters=can_filters)
            log.info(f"Bus CAN (J1939) conectado en '{interface}' con bitrate {bitrate}.")
            log.info(f"Escuchando PGNs: {list(self.pgn_map.keys())}")
            return True
        except (OSError, can.CanError) as e:
            log.critical(f"FATAL: Error al inicializar el bus CAN: {e}. ¿Está la interfaz '{interface}' activa?")
            return False

    def _parse_j1939_message(self, pgn: int, data: bytes) -> Optional[Dict[str, Any]]:
        """
        Parsea los datos de un mensaje J1939 basado en su PGN.
        Referencia: J1939 Digital Annex (SPNs)
        """
        value = None
        try:
            # PGN 61444 (0xF004) - Electronic Engine Controller 1 (EEC1)
            if pgn == 61444:
                # SPN 190: Engine Speed
                # Bytes 4-5, resolución 0.125 rpm/bit, offset 0
                value = (data[4] * 256 + data[3]) * 0.125
            
            # PGN 65265 (0xFEF1) - Cruise Control/Vehicle Speed (CCVS)
            elif pgn == 65265:
                # SPN 84: Wheel-Based Vehicle Speed
                # Bytes 2-3, resolución 1/256 km/h por bit, offset 0
                value = (data[2] * 256 + data[1]) / 256.0

            # PGN 65262 (0xFEEE) - Engine Temperature 1 (ET1)
            elif pgn == 65262:
                # SPN 110: Engine Coolant Temperature
                # Byte 1, resolución 1 °C/bit, offset -40 °C
                value = data[0] - 40.0
                
            # PGN 61443 (0xF003) - Electronic Engine Controller 2 (EEC2)
            elif pgn == 61443:
                 # SPN 91: Accelerator Pedal Position 1
                 # Byte 2, resolución 0.4 %/bit, offset 0
                 value = data[1] * 0.4

            # --- Añadir más decodificadores de PGN aquí ---

            if value is not None:
                return {"value": round(value, 2)}
                
        except IndexError:
            log.warning(f"Índice fuera de rango al parsear PGN {pgn}. Longitud de datos: {len(data)}")

        return None

    def _acquire_data(self):
        if not self.pgn_map or not self.bus:
            self.shutdown_event.wait(1.0) # Esperar si no hay nada que hacer
            return

        # Bucle de escucha pasiva. `recv` bloqueará hasta que llegue un mensaje
        # que pase los filtros configurados o hasta que expire el timeout.
        msg = self.bus.recv(timeout=1.0)

        if msg:
            # Extraer el PGN del ID de arbitraje de 29 bits
            pgn = (msg.arbitration_id >> 8) & 0x1FFFF
            
            if pgn in self.pgn_map:
                parsed_data = self._parse_j1939_message(pgn, msg.data)
                
                if parsed_data:
                    pgn_info = self.pgn_map[pgn]
                    final_data = {
                        "pgn_name": pgn_info['name'],
                        "pgn": pgn,
                        "value": parsed_data['value'],
                        "unit": pgn_info.get('unit', 'N/A'),
                        "raw_data": msg.data.hex().upper()
                    }
                    packet = self._create_data_packet("can", final_data)
                    self.data_queue.put(packet)
                    log.debug(f"Mensaje J1939 recibido y procesado para {pgn_info['name']}: {final_data['value']} {final_data['unit']}")
                else:
                    log.warning(f"Mensaje J1939 con PGN {pgn} ('{self.pgn_map[pgn]['name']}') recibido pero no se pudo parsear. Datos: {msg.data.hex().upper()}")

    def _cleanup(self):
        if self.bus:
            self.bus.shutdown()
            log.info("Bus CAN desconectado.")
```

## Archivo: `.\src\data_acquirers\gpio_acquirer.py`

```python
# Archivo: .\src\data_acquirers\gpio_acquirer.py

import logging
from datetime import datetime
import RPi.GPIO as GPIO # Importación directa.

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class GPIOAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="GPIOAcquirer", config_key="gpio_rotativo")
        self.pin = self.config.get("pin")
        self.period = self.config.get("log_period_sec", 15)

    def _setup(self) -> bool:
        if self.pin is None:
            log.critical("FATAL: No se ha especificado un pin GPIO para el sensor rotativo en la configuración.")
            return False
        
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            log.info(f"Configurado pin GPIO {self.pin} para sensor rotativo.")
            return True
        except (RuntimeError, ValueError) as e:
            log.critical(f"FATAL: Error al configurar GPIO: {e}. ¿Estás ejecutando como root o tienes permisos?")
            return False

    def _acquire_data(self):
        self.shutdown_event.wait(self.period)
        
        if self.shutdown_event.is_set(): # Añadimos una comprobación para salir rápido
            return

        status_int = GPIO.input(self.pin)
        status_str = "ON" if status_int == GPIO.HIGH else "OFF"
        
        data = {
            "pin": self.pin,
            "status": status_str
        }
        packet = self._create_data_packet("rotativo", data)
        self.data_queue.put(packet)

    def _cleanup(self):
        # La limpieza se hará de forma centralizada al final de la aplicación.
        log.info("GPIOAcquirer finalizando. La limpieza de GPIO se gestionará globalmente.")
```

## Archivo: `.\src\data_acquirers\gps_acquirer.py`

```python
import logging
import time
from datetime import datetime
from typing import Optional
import smbus2 as smbus

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class GPSAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="GPSAcquirer", config_key="gps")
        self.bus = None
        self.i2c_addr = self.config.get('i2c_addr', 0x42)
        self.read_buffer = b''

    def _setup(self) -> bool:
        try:
            bus_id = self.config.get('i2c_bus', 1)
            self.bus = smbus.SMBus(bus_id)
            # Prueba de lectura simple para confirmar que el dispositivo está presente
            self.bus.read_byte(self.i2c_addr)
            log.info(f"Comunicación I2C para GPS (u-blox) iniciada en bus {bus_id}, dirección {hex(self.i2c_addr)}.")
            return True
        except (IOError, FileNotFoundError) as e:
            log.critical(f"FATAL: No se pudo inicializar I2C para GPS: {e}. Compruebe conexiones, permisos y configuración.")
            self.bus = None
            return False

    def _parse_nmea_lat_lon(self, raw_val: str, direction: str) -> Optional[str]:
        """Convierte una coordenada NMEA a grados decimales."""
        if not raw_val or not direction:
            return None
        
        try:
            val_float = float(raw_val)
            degrees = int(val_float / 100)
            minutes = val_float - (degrees * 100)
            decimal_degrees = degrees + (minutes / 60)
            
            if direction in ['S', 'W']:
                decimal_degrees *= -1
                
            return f"{decimal_degrees:.6f}"
        except (ValueError, TypeError):
            log.warning(f"Valor de coordenada GPS inválido: val='{raw_val}', dir='{direction}'")
            return None

    def _process_buffer(self):
        """Procesa el búfer de lectura en busca de sentencias NMEA completas."""
        # Las sentencias NMEA terminan en \r\n
        while b'\r\n' in self.read_buffer:
            line, self.read_buffer = self.read_buffer.split(b'\r\n', 1)
            line_str = line.decode('ascii', errors='ignore').strip()
            
            if line_str.startswith('$GPRMC'):
                self._parse_gprmc(line_str)

    def _parse_gprmc(self, line: str):
        """Parsea una línea GPRMC y la pone en la cola si es válida."""
        parts = line.split(',')
        # GPRMC debe tener al menos 12 campos y el estado (parts[2]) debe ser 'A' (Activo)
        if len(parts) < 12:
            log.debug(f"Trama GPRMC malformada o incompleta: {line}")
            return
            
        if parts[2] != 'A':
            log.info("GPS no tiene un fix válido (estado != 'A'). Esperando señal.")
            return

        lat = self._parse_nmea_lat_lon(parts[3], parts[4])
        lon = self._parse_nmea_lat_lon(parts[5], parts[6])
        
        # Solo enviar paquete si tenemos coordenadas válidas
        if lat is not None and lon is not None:
            data = {
                "latitude": lat,
                "longitude": lon,
                "speed_knots": parts[7] if parts[7] else "0.0",
                "fix_status": "Active"
            }
            packet = self._create_data_packet("gps", data)
            self.data_queue.put(packet)
            log.debug(f"Paquete GPS válido procesado: {data}")

    def _acquire_data(self):
        try:
            # Los módulos u-blox en I2C tienen registros para saber cuántos bytes hay disponibles
            bytes_available_high = self.bus.read_byte_data(self.i2c_addr, 0xFD)
            bytes_available_low = self.bus.read_byte_data(self.i2c_addr, 0xFE)
            bytes_to_read = (bytes_available_high << 8) | bytes_available_low

            if bytes_to_read > 0:
                # Leer todos los bytes disponibles (hasta un máximo razonable por ciclo)
                # La lectura en bloques es más eficiente
                read_len = min(bytes_to_read, 256) 
                raw_bytes = self.bus.read_i2c_block_data(self.i2c_addr, 0xFF, read_len)
                self.read_buffer += bytes(raw_bytes)
                
                # Procesar el búfer para extraer líneas completas
                self._process_buffer()

        except IOError as e:
            log.warning(f"Error de I/O al leer el GPS: {e}. Reintentando...")
        
        # Esperar un poco antes de la siguiente lectura para no saturar el bus I2C
        self.shutdown_event.wait(0.5)

    def _cleanup(self):
        if self.bus:
            self.bus.close()
            log.info("Bus I2C para GPS cerrado.")
```

## Archivo: `.\src\data_acquirers\imu_acquirer.py`

```python
import logging
from datetime import datetime
from typing import Optional
import serial

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class IMUAcquirer(BaseAcquirer):
    # Claves que coinciden exactamente con la cabecera de la trama
    DATA_KEYS = [
        "ax", "ay", "az", "gx", "gy", "gz",
        "roll", "pitch", "yaw", "timeantwifi",
        "usciclo1", "usciclo2", "usciclo3", "usciclo4", "usciclo5",
        "si", "accmag", "microsds", "k3"
    ]
    EXPECTED_VALUES = len(DATA_KEYS)

    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="IMUAcquirer", config_key="estabilometro")
        self.ser = None

    def _setup(self) -> bool:
        try:
            port = self.config.get("serial_port")
            baud_rate = self.config.get("baud_rate")
            self.ser = serial.Serial(port, baud_rate, timeout=1)
            log.info(f"Puerto serie '{port}' abierto para Estabilómetro/IMU a {baud_rate} baudios.")
            return True
        except serial.SerialException as e:
            log.critical(f"FATAL: No se pudo abrir el puerto serie para Estabilómetro/IMU: {e}. Compruebe permisos y conexión.")
            self.ser = None
            return False

    def _parse_stabilometer_data(self, line: str) -> Optional[dict]:
        """Parsea la trama del estabilómetro, convirtiendo valores a float si es posible."""
        try:
            values = [v.strip() for v in line.split(';')]
            if len(values) != self.EXPECTED_VALUES:
                log.warning(f"Trama con número incorrecto de valores. "
                            f"Esperados: {self.EXPECTED_VALUES}, Recibidos: {len(values)}. Trama: '{line}'")
                return None
            
            # Intentar convertir todos los valores a float. Si falla el primero,
            # probablemente es una cabecera, así que la ignoramos.
            try:
                float(values[0])
            except ValueError:
                log.info(f"Línea ignorada (posible cabecera): {line}")
                return None

            # Construir el diccionario convirtiendo cada valor
            data_dict = {}
            for key, value_str in zip(self.DATA_KEYS, values):
                try:
                    data_dict[key] = float(value_str)
                except ValueError:
                    # Si un valor no es numérico, lo guardamos como string
                    data_dict[key] = value_str 
            
            return data_dict

        except Exception as e:
            log.error(f"Error al parsear la línea del estabilómetro '{line}': {e}")
            return None

    def _acquire_data(self):
        try:
            if self.ser and self.ser.in_waiting > 0:
                line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    parsed_data = self._parse_stabilometer_data(line)
                    if parsed_data:
                        packet = self._create_data_packet("estabilometro", parsed_data)
                        self.data_queue.put(packet)
        except serial.SerialException as e:
            log.error(f"Error grave de puerto serie durante la lectura: {e}. El hilo terminará.")
            if self.ser and self.ser.is_open:
                self.ser.close()
            self.ser = None
            # Relanzamos la excepción para que el gestor principal sepa que el hilo ha muerto
            raise e
        except Exception as e:
            log.error(f"Error inesperado en adquisición de estabilómetro: {e}")

        # Pequeña pausa para no consumir 100% de CPU si no hay datos
        self.shutdown_event.wait(0.01)

    def _cleanup(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            log.info("Puerto serie del Estabilómetro/IMU cerrado.")
```

## Archivo: `.\src\data_acquirers\__init__.py`

```python

```

## Archivo: `.\src\gui\gui_controller.py`

```python
class GuiController:
    """
    Intermediario entre la lógica de la aplicación (AppController) y la GUI (MainWindow y sus vistas).
    """
    def __init__(self, app_controller, main_window):
        self.app_controller = app_controller
        self.main_window = main_window

    def update_all_views(self):
        """Pide los datos más recientes al backend y actualiza todas las vistas."""
        latest_data = self.app_controller.get_latest_data()
        service_status = self.app_controller.get_service_status()

        # Actualizar la vista de estado
        if self.main_window.status_view:
            self.main_window.status_view.update_data(latest_data, service_status)
        
        # Aquí se llamarían a los métodos de actualización de otras vistas
        # ej. self.main_window.can_view.update_data(...)
```

## Archivo: `.\src\gui\main_window.py`

```python
import tkinter as tk
from tkinter import ttk
from .gui_controller import GuiController
from .views.status_view import StatusView

class MainWindow:
    def __init__(self, app_controller):
        self.app_controller = app_controller
        self.is_running_flag = False

        self.root = tk.Tk()
        self.root.title("fire-truck-app Control Panel")
        self.root.geometry("800x600")

        self.gui_controller = GuiController(app_controller, self)
        
        self.create_widgets()

        # Iniciar el ciclo de actualización de la UI
        self.update_ui()
        
        # Manejar el cierre de la ventana
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def create_widgets(self):
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # Aquí se podrían añadir más vistas en un Notebook (pestañas) o en un panel lateral
        self.status_view = StatusView(self.main_frame)
        self.status_view.pack(fill=tk.BOTH, expand=True)

    def update_ui(self):
        """Llama al controlador para que actualice los datos de las vistas."""
        if not self.is_running_flag:
            return
        
        self.gui_controller.update_all_views()
        # Reprogramar la próxima actualización en 1 segundo (1000 ms)
        self.root.after(1000, self.update_ui)

    def run(self):
        """Inicia el bucle principal de la GUI."""
        self.is_running_flag = True
        self.root.mainloop()

    def close(self):
        """Cierra la ventana de la GUI."""
        if self.is_running_flag:
            self.is_running_flag = False
            self.root.destroy()
            
    def is_running(self) -> bool:
        return self.is_running_flag
```

## Archivo: `.\src\gui\__init__.py`

```python

```

## Archivo: `.\src\gui\views\status_view.py`

```python
import tkinter as tk
from tkinter import ttk

class StatusView(ttk.Frame):
    """
    Una vista de la GUI que muestra el estado general del sistema y los últimos datos recibidos.
    """
    def __init__(self, parent):
        super().__init__(parent, padding="10")
        
        self.data_labels = {}
        self.status_labels = {}

        # --- Sección de Estado de Servicios ---
        status_frame = ttk.LabelFrame(self, text="Estado de los Servicios", padding="10")
        status_frame.pack(fill=tk.X, pady=10)
        self.status_container = status_frame

        # --- Sección de Últimos Datos ---
        data_frame = ttk.LabelFrame(self, text="Últimos Datos Recibidos", padding="10")
        data_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        self.data_container = data_frame

    def update_data(self, latest_data: dict, service_status: dict):
        """Recibe datos del GuiController y actualiza las etiquetas."""
        
        # Actualizar estado de servicios
        for service_name, status in service_status.items():
            if service_name not in self.status_labels:
                frame = ttk.Frame(self.status_container)
                ttk.Label(frame, text=f"{service_name}:").pack(side=tk.LEFT)
                self.status_labels[service_name] = ttk.Label(frame, text="Desconocido", width=10)
                self.status_labels[service_name].pack(side=tk.LEFT, padx=5)
                frame.pack(anchor="w")
            
            self.status_labels[service_name].config(text=status, foreground="green" if status == "Running" else "red")
            
        # Actualizar últimos datos
        for data_type, packet in latest_data.items():
            if data_type not in self.data_labels:
                self.data_labels[data_type] = ttk.Label(self.data_container, text="", justify=tk.LEFT)
                self.data_labels[data_type].pack(anchor="w", pady=2)
            
            timestamp = packet.get('timestamp', 'N/A').split('.')[0] # Quitar microsegundos
            data_str = ", ".join(f"{k}: {v}" for k, v in packet.get('data', {}).items())
            self.data_labels[data_type].config(text=f"[{data_type.upper()}] @ {timestamp} -> {data_str}")
```

## Archivo: `.\src\gui\views\__init__.py`

```python

```

## Archivo: `.\src\processing\__init__.py`

```python

```

## Archivo: `.\src\transmitters\ftp_transmitter.py`

```python
import ftplib
import logging
import os
import threading
import time
from datetime import datetime

log = logging.getLogger(__name__)

class FTPTransmitter(threading.Thread):
    """
    Gestiona la subida de archivos al servidor FTP con intervalos separados.
    - Sube archivos de log de días anteriores (.log) periódicamente.
    - Sube archivos de estado en tiempo real (_RealTime.txt) con mayor frecuencia.
    """

    def __init__(self, config: dict, shutdown_event: threading.Event, session_manager):
        super().__init__(name="FTPTransmitter")
        self.ftp_config = config.get('ftp', {})
        self.paths_config = config.get('paths', {})
        self.shutdown_event = shutdown_event
        self.session_manager = session_manager
        
        self.log_scan_interval = self.ftp_config.get('log_upload_interval_sec', 300)
        self.realtime_scan_interval = self.ftp_config.get('realtime_upload_interval_sec', 30)

    def run(self):
        log.info(f"Iniciando transmisor FTP. Logs cada {self.log_scan_interval}s, RealTime cada {self.realtime_scan_interval}s.")
        
        # Ejecutar un ciclo de subida completo al arrancar
        log.info("Realizando ciclo de subida inicial completo (logs y tiempo real)...")
        self._perform_log_upload_cycle()
        self._perform_realtime_upload_cycle()
        log.info("Ciclo de subida inicial completado.")
        
        last_log_scan_time = time.time()
        last_realtime_scan_time = time.time()

        while not self.shutdown_event.is_set():
            current_time = time.time()

            # Comprobar si es momento de subir logs históricos
            if current_time - last_log_scan_time >= self.log_scan_interval:
                log.info("Iniciando ciclo de subida de archivos de log...")
                self._perform_log_upload_cycle()
                last_log_scan_time = current_time

            # Comprobar si es momento de subir archivos de tiempo real
            if current_time - last_realtime_scan_time >= self.realtime_scan_interval:
                log.info("Iniciando ciclo de subida de archivos de tiempo real...")
                self._perform_realtime_upload_cycle()
                last_realtime_scan_time = current_time
            
            # Esperar un poco para no consumir CPU
            self.shutdown_event.wait(1)

        log.info("Transmisor FTP detenido.")

    def _connect_ftp(self):
        """Establece y devuelve una conexión FTP, o None si falla."""
        try:
            ftp = ftplib.FTP()
            ftp.connect(self.ftp_config['host'], self.ftp_config['port'], timeout=20)
            ftp.login(self.ftp_config['user'], self.ftp_config['pass'])
            base_remote_dir = "datos_doback"
            if base_remote_dir not in ftp.nlst():
                ftp.mkd(base_remote_dir)
            ftp.cwd(base_remote_dir)
            log.debug("Conexión FTP establecida y en directorio 'datos_doback'.")
            return ftp
        except ftplib.all_errors as e:
            log.error(f"Error de conexión FTP: {e}")
            return None

    def _scan_and_upload(self, file_filter: callable):
        """Función genérica para escanear y subir archivos que cumplan un criterio."""
        data_root = self.paths_config.get('data_root')
        if not os.path.isdir(data_root):
            log.warning(f"El directorio raíz '{data_root}' no existe. No hay nada que subir.")
            return

        ftp = self._connect_ftp()
        if not ftp:
            log.warning("No se pudo conectar a FTP. Se reintentará en el próximo ciclo.")
            return

        try:
            for data_type_dir in os.listdir(data_root):
                local_type_path = os.path.join(data_root, data_type_dir)
                if not os.path.isdir(local_type_path):
                    continue

                for filename in os.listdir(local_type_path):
                    if self.shutdown_event.is_set():
                        log.warning("Señal de apagado recibida, abortando ciclo de subida.")
                        return

                    if file_filter(filename):
                        local_file_path = os.path.join(local_type_path, filename)
                        self._upload_file(ftp, local_file_path)

        except Exception as e:
            log.error(f"Error inesperado durante el ciclo de subida FTP: {e}", exc_info=True)
        finally:
            if ftp:
                ftp.quit()

    def _perform_log_upload_cycle(self):
        """Escanea y sube solo los archivos de log de días anteriores."""
        today_str = datetime.now().strftime('%Y%m%d')
        
        def log_filter(filename):
            if not filename.endswith(".log"):
                return False
            try:
                file_date_str = filename.split('_')[-1].split('.')[0]
                return file_date_str < today_str
            except IndexError:
                log.warning(f"No se pudo extraer fecha de '{filename}'. Omitiendo.")
                return False
        
        self._scan_and_upload(log_filter)

    def _perform_realtime_upload_cycle(self):
        """Escanea y sube solo los archivos _RealTime.txt."""
        self._scan_and_upload(lambda filename: filename.endswith("_RealTime.txt"))

    def _upload_file(self, ftp, local_path: str) -> bool:
        """
        Sube un único archivo al servidor FTP, creando la estructura de directorios
        remota en minúsculas: datos_doback/dobackXXX/tipo_dato/archivo.
        """
        try:
            filename = os.path.basename(local_path)
            # Ej: 'CAN' o 'ESTABILIDAD' -> convertido a 'can', 'estabilidad'
            data_type_name = os.path.basename(os.path.dirname(local_path)).lower()
            # Ej: 'DOBACK001' -> convertido a 'doback001'
            device_name = self.session_manager.device_name.lower()

            # --- Navegar o crear directorios remotos ---
            # Estamos en 'datos_doback/', ahora creamos 'dobackXXX/'
            if device_name not in ftp.nlst():
                ftp.mkd(device_name)
            ftp.cwd(device_name)
            
            # Ahora creamos 'tipo_dato/'
            if data_type_name not in ftp.nlst():
                ftp.mkd(data_type_name)
            ftp.cwd(data_type_name)

            log.info(f"  -> Subiendo {filename} a {ftp.pwd()}...")
            with open(local_path, 'rb') as f:
                ftp.storbinary(f'STOR {filename}', f)
            
            # Volver al directorio base 'datos_doback' para el siguiente archivo
            ftp.cwd('/datos_doback')
            return True
            
        except ftplib.all_errors as e:
            log.error(f"Error de FTP al subir el archivo {local_path}: {e}")
            try:
                ftp.cwd('/datos_doback')
            except ftplib.all_errors:
                log.error("No se pudo volver al directorio FTP base después de un error.")
            return False
        except FileNotFoundError:
            log.warning(f"El archivo {local_path} desapareció antes de poder subirlo.")
            return True
        except Exception as e:
            log.error(f"Error inesperado al subir {local_path}: {e}")
            return False
```

## Archivo: `.\src\transmitters\__init__.py`

```python

```

## Archivo: `.\src\utils\config_loader.py`

```python
import yaml
import logging

log = logging.getLogger(__name__)

class ConfigLoader:
    """Carga y proporciona acceso a la configuración desde un archivo YAML."""
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self):
        log.info(f"Cargando configuración desde: {self.config_path}")
        with open(self.config_path, 'r') as f:
            try:
                config_data = yaml.safe_load(f)
                return config_data
            except yaml.YAMLError as e:
                log.error(f"Error al parsear el archivo YAML: {e}")
                raise
    
    def get_config(self) -> dict:
        """Devuelve el diccionario de configuración completo."""
        return self.config
    
    def get_section(self, section_name: str) -> dict:
        """Devuelve una sección específica de la configuración."""
        return self.config.get(section_name, {})
```

## Archivo: `.\src\utils\unified_logger.py`

```python
import logging
import sys
from logging.handlers import RotatingFileHandler
import os

def setup_logging(config: dict):
    """Configura el sistema de logging para toda la aplicación."""
    log_config = config.get('paths', {})
    log_level_str = config.get('system', {}).get('log_level', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    log_dir = log_config.get('app_logs', '/tmp/fire-truck-app_logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'fire-truck-app_app.log')

    # Formato del log
    log_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Handler para la consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_format)

    # Handler para el archivo con rotación
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5*1024*1024, backupCount=5 # 5 MB por archivo, 5 archivos de respaldo
    )
    file_handler.setFormatter(log_format)

    # Configurar el logger raíz
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear() # Limpiar handlers previos
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logging.info(f"Logging configurado. Nivel: {log_level_str}. Archivo: {log_file}")
```

## Archivo: `.\src\utils\__init__.py`

```python

```

