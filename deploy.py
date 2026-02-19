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

# --- Colores ---
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
    def __init__(self, host, user, password):
        self.host = host
        self.user = user
        self.password = password
        self.client = None
        self.sftp = None

    def connect(self):
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
        if self.sftp:
            self.sftp.close()
        if self.client:
            self.client.close()
        print_ok("Conexión SSH cerrada.")

    def execute(self, command, use_sudo=False, ignore_errors=False):
        print_info(f"Ejecutando: {command}")

        full_command = command
        if use_sudo:
            full_command = f"sudo -S -p '' {command}"

        try:
            stdin, stdout, stderr = self.client.exec_command(full_command, timeout=300)

            if use_sudo:
                stdin.write(self.password + '\n')
                stdin.flush()
                stdin.channel.shutdown_write()

            out = stdout.read().decode(errors="ignore").strip()
            err = stderr.read().decode(errors="ignore").strip()
            exit_code = stdout.channel.recv_exit_status()

            if out:
                print_info(f"stdout: {out}")

            if err and "Warning:" not in err:
                print_warn(f"stderr: {err}")

            if exit_code != 0 and not ignore_errors:
                raise RuntimeError(f"Exit code {exit_code}: {err}")

            return out
        except Exception as e:
            print_fail(f"Error ejecutando comando: {e}")
            raise

    def upload_file(self, local_path, remote_path):
        print_info(f"Subiendo {local_path} → {remote_path}")
        self.sftp.put(local_path, remote_path)

def main():
    parser = argparse.ArgumentParser(description="Despliegue fire-truck-app")
    parser.add_argument("host")
    args = parser.parse_args()

    password = getpass.getpass(f"Contraseña para {TARGET_USER}@{args.host}: ")
    repo_url = input("URL SSH del repo Git: ").strip()
    git_branch = input("Rama a desplegar [main]: ").strip() or "main"

    deployer = SSHDeployer(args.host, TARGET_USER, password)

    try:
        deployer.connect()

        # PASO 1
        print_step("Actualizando sistema")
        env = "DEBIAN_FRONTEND=noninteractive"
        #deployer.execute(f"{env} apt-get update", use_sudo=True)
        #deployer.execute(f"{env} apt-get upgrade -y", use_sudo=True)
        deployer.execute(
            f"{env} apt-get install -y git python3-pip python3-venv can-utils i2c-tools",
            use_sudo=True
        )

        # PASO 2
        print_step("Creando directorios")
        deployer.execute(f"mkdir -p {APP_DIR} {LOG_DIR} {DATA_DIR}")

        # PASO 3
        print_step("Configurando Deploy Key")
        ssh_dir = f"/home/{TARGET_USER}/.ssh"
        key_path = f"{ssh_dir}/id_ed25519"
        pub_key = f"{key_path}.pub"

        deployer.execute(f"mkdir -p {ssh_dir}")
        deployer.execute(f"chmod 700 {ssh_dir}")
        deployer.execute(f"chmod 600 {key_path}", ignore_errors=True)

        exists = deployer.execute(f"[ -f {pub_key} ] && echo yes || echo no")
        if exists == "no":
            deployer.execute(f"ssh-keygen -t ed25519 -f {key_path} -N ''")
            deployer.execute(f"chmod 600 {key_path}")

        print(deployer.execute(f"cat {pub_key}"))
        input("Añade la clave como Deploy Key y pulsa Enter...")

        git_host = repo_url.split("@")[1].split(":")[0]
        deployer.execute(f"ssh-keyscan {git_host} >> {ssh_dir}/known_hosts", ignore_errors=True)
        deployer.execute(f"sort -u {ssh_dir}/known_hosts -o {ssh_dir}/known_hosts")

        # PASO 4
        print_step("Repositorio")
        repo_exists = deployer.execute(f"[ -d {APP_DIR}/.git ] && echo yes || echo no")
        if repo_exists == "yes":
            deployer.execute(
                f"cd {APP_DIR} && git fetch --all && "
                f"git reset --hard origin/{git_branch} && git clean -fdx"
            )
        else:
            deployer.execute(f"git clone -b {git_branch} {repo_url} {APP_DIR}")

        deployer.execute(f"python3 -m venv {APP_DIR}/.venv")
        deployer.execute(f"{APP_DIR}/.venv/bin/pip install -r {APP_DIR}/requirements.txt")

        # PASO 5
        print_step("Permisos")
        update_script = f"{APP_DIR}/scripts/check_and_install_update.sh"
        try:
            deployer.sftp.stat(update_script)
            deployer.execute(f"chmod +x {update_script}")
        except FileNotFoundError:
            print_warn("Script de actualización no encontrado")

        deployer.execute(
            f"usermod -a -G gpio,i2c,dialout {TARGET_USER}",
            use_sudo=True
        )

        sudo_rule = f"{TARGET_USER} ALL=(ALL) NOPASSWD: /sbin/shutdown, /sbin/reboot"
        deployer.execute(
            f"echo '{sudo_rule}' | tee /etc/sudoers.d/99-fire-truck-app > /dev/null",
            use_sudo=True
        )
        deployer.execute("chmod 0440 /etc/sudoers.d/99-fire-truck-app", use_sudo=True)

        # PASO 6
        print_step("CAN + RTC")
        can_cfg = "\n# fire-truck CAN\ndtparam=spi=on\ndtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25"
        rtc_cfg = "\n# RTC\ndtoverlay=i2c-rtc,ds3231"

        deployer.execute(
            f"grep -q 'mcp2515-can0' /boot/firmware/config.txt || "
            f"printf '{can_cfg}' | tee -a /boot/firmware/config.txt",
            use_sudo=True
        )

        deployer.execute(
            f"grep -q 'i2c-rtc,ds3231' /boot/firmware/config.txt || "
            f"printf '{rtc_cfg}' | tee -a /boot/firmware/config.txt",
            use_sudo=True
        )

        deployer.execute("apt-get -y remove fake-hwclock", use_sudo=True, ignore_errors=True)

        # PASO 7
        print_step("Servicios systemd")
        for svc in ("app.service", "updater.service"):
            deployer.upload_file(f"services/{svc}", f"/tmp/{svc}")
            deployer.execute(f"mv /tmp/{svc} /etc/systemd/system/", use_sudo=True)

        deployer.execute("systemctl daemon-reload", use_sudo=True)
        deployer.execute("systemctl enable app.service updater.service", use_sudo=True)
        deployer.execute("systemctl restart app.service updater.service", use_sudo=True)

        print_ok("Despliegue completado")

        if input("¿Reiniciar ahora? (s/n): ").lower() == "s":
            deployer.execute("reboot", use_sudo=True, ignore_errors=True)

    except Exception as e:
        print_fail(str(e))
        sys.exit(1)
    finally:
        deployer.disconnect()

if __name__ == "__main__":
    main()
