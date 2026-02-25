#!/usr/bin/env python3
import subprocess
import sys
import tempfile
import os
from pathlib import Path

# --- Config fija ---
REMOTE_USER = "cosigein"
REMOTE_APP_DIR = "/home/cosigein/fire-truck-app"
REMOTE_SCRIPT = f"{REMOTE_APP_DIR}/setup.py"
LOCAL_SCRIPT = Path("setup.py")
# -------------------

def run(cmd):
    print(f">>> {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

def main():
    host = input("IP o hostname de la placa: ").strip()
    if not host:
        print("Host no válido")
        sys.exit(1)

    if not LOCAL_SCRIPT.exists():
        print(f"Error: no existe {LOCAL_SCRIPT}")
        sys.exit(1)

    target = f"{REMOTE_USER}@{host}"

    # Usar SSH ControlMaster para pedir la contraseña una sola vez
    ctrl_socket = os.path.join(tempfile.gettempdir(), f"ssh-deploy-{host}")
    ssh_opts = ["-o", f"ControlPath={ctrl_socket}", "-o", "ControlMaster=auto", "-o", "ControlPersist=60"]

    try:
        # Crear el directorio remoto (esto abre la conexión y pide la contraseña)
        run(["ssh", *ssh_opts, target, f"mkdir -p {REMOTE_APP_DIR}"])

        # 1. Copiar script (reutiliza la conexión, sin contraseña)
        run(["scp", *ssh_opts, str(LOCAL_SCRIPT), f"{target}:{REMOTE_SCRIPT}"])

        # 2. Ejecutar script en la placa (reutiliza la conexión)
        run(["ssh", *ssh_opts, target, f"python3 {REMOTE_SCRIPT}"])

        print("✔ Script enviado y ejecutado correctamente")
    finally:
        # Cerrar la conexión de control
        subprocess.run(["ssh", "-O", "exit", "-o", f"ControlPath={ctrl_socket}", target],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if __name__ == "__main__":
    main()