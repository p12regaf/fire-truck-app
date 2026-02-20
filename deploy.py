#!/usr/bin/env python3
import subprocess
import sys
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

    # 1. Copiar script
    run([
        "scp",
        str(LOCAL_SCRIPT),
        f"{target}:{REMOTE_SCRIPT}"
    ])

    # 2. Ejecutar script en la placa
    run([
        "ssh",
        target,
        f"python3 {REMOTE_SCRIPT}"
    ])

    print("✔ Script enviado y ejecutado correctamente")

if __name__ == "__main__":
    main()