# -*- coding: utf-8 -*-

"""
# =============================================================================
#  SCRIPT DE DESPLIEGUE AUTOMÁTICO PARA fire-truck-app
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
            # -S lee la contraseña de stdin, -p '' evita que sudo muestre su propio prompt.
            full_command = f"sudo -S -p '' {command}"

        try:
            # No usamos get_pty=True para que `sudo -S` funcione de manera fiable.
            stdin, stdout, stderr = self.client.exec_command(full_command, timeout=300) # Añadimos un timeout largo (5 min)
            
            if use_sudo:
                stdin.write(self.password + '\n')
                stdin.flush()
                # Cerramos el canal de entrada después de enviar la contraseña.
                # Esto es crucial para que el proceso remoto sepa que no hay más input.
                stdin.channel.shutdown_write()

            # Leer la salida ANTES de esperar el código de finalización para evitar bloqueos.
            out = stdout.read().decode('utf-8', errors='ignore').strip()
            err = stderr.read().decode('utf-8', errors='ignore').strip()
            
            # Ahora que hemos leído la salida, podemos esperar a que el comando termine.
            exit_code = stdout.channel.recv_exit_status()
            
            # Imprimir salida estándar si la hay (útil para depuración)
            if out:
                # Opcional: imprimir la salida para ver qué está pasando.
                # Puede ser mucho texto para `apt-get update`, así que puedes comentarlo si quieres.
                print_info(f"  stdout: {out}")

            # Imprimir errores, ignorando advertencias comunes de sudo sin tty.
            if err and "Warning: " not in err:
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
        deployer.execute(f"{env} apt-get install -y git python3-pip python3-venv can-utils i2c-tools", use_sudo=True)
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

        if repo_status == 'exists':
            print_info("El repositorio ya existe. Forzando actualización desde el origen...")
            force_update_cmds = f"cd {APP_DIR} && git fetch --all && git reset --hard origin/{git_branch} && git clean -fdx"
            deployer.execute(force_update_cmds)
        else:
            print_info("El repositorio no existe. Clonando...") # Mensaje corregido para mayor claridad
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

        # --- PASO 6.5: Configuración del Reloj en Tiempo Real (RTC) ---
        print_step("Paso 6.5: Configurando Reloj en Tiempo Real (RTC) en /boot/firmware/config.txt")
        # Asumimos un RTC DS3231, que es muy común. Cambiar si es otro modelo.
        rtc_config = "\\n# Habilitar RTC (DS3231)\\ndtoverlay=i2c-rtc,ds3231"
        check_rtc_cmd = "grep -q 'i2c-rtc,ds3231' /boot/firmware/config.txt"

        try:
            deployer.execute(check_rtc_cmd, use_sudo=True)
            print_info("La configuración del RTC ya parece existir. Omitiendo.")
        except Exception:
            print_info("Añadiendo configuración del RTC a /boot/firmware/config.txt...")
            deployer.execute(f'printf "{rtc_config}" | sudo tee -a /boot/firmware/config.txt > /dev/null')
            print_ok("RTC configurado. Se requiere un reinicio para que el kernel lo reconozca.")

        print_info("Deshabilitando fake-hwclock para dar prioridad al RTC real...")
        deployer.execute("apt-get -y remove fake-hwclock", use_sudo=True, ignore_errors=True)
        deployer.execute("update-rc.d -f fake-hwclock remove", use_sudo=True, ignore_errors=True)
        
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