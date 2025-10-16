# -*- coding: utf-8 -*-

"""
# =============================================================================
#  SCRIPT DE DESPLIEGUE AUTOMÁTICO PARA fire-truck-app
# =============================================================================
#
# Descripción:
#   Este script automatiza la instalación y configuración completa de la aplicación
#   'fire-truck-app' en una Raspberry Pi donde el usuario 'cosigein' es el
#   usuario principal y administrador (con privilegios sudo).
#
#   Acciones realizadas:
#     - Se conecta directamente como el usuario 'cosigein'.
#     - Instala dependencias del sistema (git, python, can-utils).
#     - Crea los directorios de la aplicación (/datos, /logs).
#     - Asigna permisos de hardware (gpio, i2c, dialout) al propio usuario.
#     - Configura una Deploy Key SSH para acceder a Git.
#     - Clona o fuerza la actualización del repositorio desde GitHub.
#     - Instala las dependencias de Python en un entorno virtual.
#     - Configura sudoers para permitir el reinicio/apagado sin contraseña.
#     - Configura el bus CAN en /boot/config.txt.
#     - Instala y habilita los servicios de systemd.
#
# -----------------------------------------------------------------------------
#
# Requisitos Previos:
#
#   1. En la máquina LOCAL (donde se ejecuta este script):
#      - Python 3 instalado.
#      - La librería 'paramiko': pip install paramiko
#
#   2. En la Raspberry Pi de DESTINO:
#      - Raspberry Pi OS instalado.
#      - El usuario 'cosigein' debe ser el usuario principal con acceso sudo.
#
# -----------------------------------------------------------------------------
#
# Estructura de Archivos requerida (en la máquina LOCAL):
#
#   .
#   ├── deploy.py             <-- Este script
#   └── services/
#       ├── app.service
#       └── updater.service
#
# -----------------------------------------------------------------------------
#
# Uso:
#
#   1. Abre una terminal en la carpeta donde guardaste este script.
#   2. Ejecuta el siguiente comando, reemplazando la IP por la de tu RPi:
#
#      python deploy.py <IP_o_HOSTNAME_de_la_RPi>
#
#      Ejemplo:
#      python deploy.py 192.168.1.55
#
# -----------------------------------------------------------------------------
#
# Proceso Interactivo:
#
#   El script te pedirá la siguiente información:
#
#   1. Contraseña del usuario 'cosigein'.
#   2. URL SSH del repositorio Git. Deberás introducir:
#      git@github.com:p12regaf/fire-truck-app.git
#   3. Nombre de la rama a desplegar (puedes presionar Enter para usar 'main').
#   4. (Solo la primera vez) Te mostrará una Deploy Key para que la añadas
#      a la configuración de tu repositorio en GitHub.
#
# =============================================================================
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
            stdin, stdout, stderr = self.client.exec_command(full_command)
            if use_sudo:
                stdin.write(self.password + '\n')
                stdin.flush()

            exit_code = stdout.channel.recv_exit_status()
            
            out = stdout.read().decode('utf-8').strip()
            err = stderr.read().decode('utf-8').strip()
            if out:
                print_info(f"  stdout: {out}")
            if err and "Warning: " not in err:
                print_warn(f"  stderr: {err}")

            if exit_code != 0 and not ignore_errors:
                raise Exception(f"El comando falló con código de salida {exit_code}. Error: {err}")
            
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
        deployer.execute("apt update", use_sudo=True)
        deployer.execute("apt upgrade -y", use_sudo=True)
        deployer.execute("apt install -y git python3-pip python3-venv can-utils", use_sudo=True)
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
            deployer.execute(f"git clone {repo_url} {APP_DIR}")

        print_info("Configurando el entorno virtual de Python...")
        deployer.execute(f"python3 -m venv {APP_DIR}/.venv")
        deployer.execute(f"{APP_DIR}/.venv/bin/pip install -r {APP_DIR}/requirements.txt")
        print_ok("Repositorio y dependencias listos.")
        
        # --- PASO 5: Configuración de Permisos ---
        print_step("Paso 5: Configurando permisos de hardware y sudo...")
        deployer.execute(f"chmod +x {APP_DIR}/scripts/check_and_install_update.sh")
        deployer.execute(f"usermod -a -G gpio,i2c,dialout {TARGET_USER}", use_sudo=True)
        
        sudo_rule = f'{TARGET_USER} ALL=(ALL) NOPASSWD: /sbin/shutdown, /sbin/reboot'
        deployer.execute(f"echo '{sudo_rule}' > /etc/sudoers.d/99-fire-truck-app", use_sudo=True)
        deployer.execute(f"chmod 0440 /etc/sudoers.d/99-fire-truck-app", use_sudo=True)
        print_ok("Permisos configurados.")

        # --- PASO 6: Configuración del Bus CAN ---
        print_step("Paso 6: Configurando bus CAN en /boot/config.txt...")
        can_config = "\\n# Habilitar CAN bus (fire-truck-app)\\ndtparam=spi=on\\ndtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25"
        check_can_cmd = "grep -q 'mcp2515-can0' /boot/config.txt"
        if deployer.execute(check_can_cmd, use_sudo=True, ignore_errors=True) == "":
             print_info("Añadiendo configuración del bus CAN a /boot/config.txt...")
             deployer.execute(f'printf "{can_config}" | sudo tee -a /boot/config.txt')
             print_ok("Bus CAN configurado.")
        else:
            print_info("La configuración del bus CAN ya parece existir. Omitiendo.")
        
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
            deployer.execute("reboot", use_sudo=True)
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