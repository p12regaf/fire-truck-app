#!/bin/bash

# =============================================================================
#  INSTALADOR AUTOMÁTICO PARA fire-truck-app
# =============================================================================
#
#  Este script realiza una instalación completa en una Raspberry Pi:
#  0. Habilita una señal de alimentación en GPIO 12 (3.3V).
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
#  2. Hazlo ejecutable: chmod +x fire_truck_app_installer.sh
#  3. Ejecútalo con sudo: sudo ./fire_truck_app_installer.sh
#
# =============================================================================

# --- Salir inmediatamente si un comando falla ---
set -e

# --- Variables de Configuración ---
TARGET_USER="cosigein"
APP_DIR="/home/${TARGET_USER}/fire-truck-app"
LOG_DIR="/home/${TARGET_USER}/logs"
DATA_DIR="/home/${TARGET_USER}/datos"
BOOT_CONFIG_FILE="/boot/firmware/config.txt"
POWER_OK_GPIO=12 # Pin BCM 12 (BOARD 32) para la señal de alimentación

# --- Colores para la Salida ---
C_HEADER='\033[95m'
C_OKBLUE='\033[94m'
C_OKCYAN='\033[96m'
C_OKGREEN='\033[92m'
C_WARNING='\033[93m'
C_FAIL='\033[91m'
C_ENDC='\033[0m'
C_BOLD='\033[1m'

# --- Funciones de Logging ---
log_step() {
    echo -e "\n${C_HEADER}${C_BOLD}>>> $1${C_ENDC}"
}
log_info() {
    echo -e "${C_OKCYAN}    $1${C_ENDC}"
}
log_ok() {
    echo -e "${C_OKGREEN}[OK] $1${C_ENDC}"
}
log_warn() {
    echo -e "${C_WARNING}[WARN] $1${C_ENDC}"
}
log_fail() {
    echo -e "${C_FAIL}[FAIL] $1${C_ENDC}" >&2
    exit 1
}

# --- Función para asegurar líneas en config.txt ---
# Uso: ensure_config_line "patrón_a_buscar" "línea_completa_a_añadir" "comentario"
ensure_config_line() {
    local pattern="$1"
    local line="$2"
    local comment="$3"
    
    if ! grep -qE "^${pattern}" "${BOOT_CONFIG_FILE}"; then
        log_info "Añadiendo: ${line}"
        # Añade un salto de línea antes del comentario si este existe
        if [ -n "$comment" ]; then
            echo "" >> "${BOOT_CONFIG_FILE}"
            echo "$comment" >> "${BOOT_CONFIG_FILE}"
        fi
        echo "$line" >> "${BOOT_CONFIG_FILE}"
    else
        log_info "La configuración '${pattern}' ya existe. Omitiendo."
    fi
}

# =============================================================================
# --- INICIO DEL SCRIPT DE INSTALACIÓN ---
# =============================================================================

# --- PASO 0: Comprobaciones Previas ---
log_step "Paso 0: Realizando comprobaciones previas..."

# Comprobar si se ejecuta como root
if [ "$(id -u)" -ne 0 ]; then
    log_fail "Este script debe ser ejecutado como root. Por favor, usa 'sudo'."
fi

# ### AÑADIDO ###: PASO DE INICIALIZACIÓN DE HARDWARE ESENCIAL
# -----------------------------------------------------------------------------
log_step "Paso de Inicialización: Habilitando señal de alimentación..."

# Comprobamos si la herramienta raspi-gpio está disponible
if ! command -v raspi-gpio &> /dev/null; then
    log_warn "La herramienta 'raspi-gpio' no se encuentra. Es parte del paquete 'raspi-config'."
    log_fail "Asegúrate de estar en un sistema Raspberry Pi OS con las herramientas base."
fi

log_info "Activando señal de 'keep-alive' para la fuente de alimentación en GPIO ${POWER_OK_GPIO} (Pin 32)."
log_info "Configurando GPIO ${POWER_OK_GPIO} como salida y poniéndolo en estado ALTO (3.3V)..."
# 'op' establece el modo a 'output'
# 'dh' establece el nivel a 'drive high' (3.3V)
raspi-gpio set ${POWER_OK_GPIO} op dh
log_ok "Señal de alimentación habilitada."
# -----------------------------------------------------------------------------
# ### FIN DEL AÑADIDO ###


# Obtener el directorio donde se encuentra el script
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
KEY_SRC_PATH="${SCRIPT_DIR}/deploy_key"

# Comprobar si la clave de despliegue existe
if [ ! -f "${KEY_SRC_PATH}" ]; then
    log_fail "No se encontró el archivo 'deploy_key' en el mismo directorio que el instalador."
fi
log_ok "Comprobaciones previas superadas."


# --- PASO 1: Recopilar Información ---
log_step "Paso 1: Recopilando información necesaria..."
read -p "Introduce la URL SSH de tu repositorio Git (ej. git@github.com:p12regaf/fire-truck-app.git): " REPO_URL
read -p "Introduce el nombre de la rama a desplegar (ej. main) [main]: " GIT_BRANCH
GIT_BRANCH=${GIT_BRANCH:-main}
log_ok "Información recopilada."


# --- PASO 2: Preparación del Sistema ---
log_step "Paso 2: Actualizando sistema e instalando dependencias..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get upgrade -y
apt-get install -y git python3-pip python3-venv can-utils i2c-tools
log_ok "Sistema y dependencias listos."


# --- PASO 3: Creación de Usuario y Directorios ---
log_step "Paso 3: Configurando usuario y directorios..."
if ! id -u "${TARGET_USER}" >/dev/null 2>&1; then
    log_info "Creando usuario '${TARGET_USER}'..."
    useradd -m -s /bin/bash -G sudo,gpio,i2c,dialout "${TARGET_USER}"
    log_warn "¡ACCIÓN REQUERIDA! Se ha creado el usuario '${TARGET_USER}'."
    log_warn "Por favor, establece una contraseña para él ejecutando 'sudo passwd ${TARGET_USER}' después de la instalación."
else
    log_info "El usuario '${TARGET_USER}' ya existe. Asegurando membresía de grupos..."
    usermod -a -G sudo,gpio,i2c,dialout "${TARGET_USER}"
fi

log_info "Creando directorios de la aplicación..."
mkdir -p "${APP_DIR}" "${LOG_DIR}" "${DATA_DIR}"
chown -R "${TARGET_USER}:${TARGET_USER}" "/home/${TARGET_USER}"
log_ok "Usuario y directorios configurados."


# --- PASO 4: Instalación de la Deploy Key ---
log_step "Paso 4: Instalando Deploy Key de Git..."
KEY_DIR="/home/${TARGET_USER}/.ssh"
KEY_DEST_PATH="${KEY_DIR}/id_ed25519"

# Ejecutamos los comandos como el usuario final para asegurar permisos correctos
sudo -u "${TARGET_USER}" bash << EOF
set -e
mkdir -p "${KEY_DIR}"
chmod 700 "${KEY_DIR}"
cat "${KEY_SRC_PATH}" > "${KEY_DEST_PATH}"
chmod 600 "${KEY_DEST_PATH}"
EOF

GIT_HOST=$(echo "${REPO_URL}" | cut -d '@' -f2 | cut -d ':' -f1)
log_info "Añadiendo el host de Git (${GIT_HOST}) a known_hosts..."
ssh-keyscan "${GIT_HOST}" >> "${KEY_DIR}/known_hosts"
chown "${TARGET_USER}:${TARGET_USER}" "${KEY_DIR}/known_hosts"
sort -u "${KEY_DIR}/known_hosts" -o "${KEY_DIR}/known_hosts"
log_ok "Deploy Key instalada correctamente."


# --- PASO 5: Configuración de Hardware de la RPi ---
log_step "Paso 5: Configurando periféricos en ${BOOT_CONFIG_FILE}..."
ensure_config_line "dtparam=i2c_arm=" "dtparam=i2c_arm=on" "# Habilitar I2C y SPI"
ensure_config_line "dtparam=spi=" "dtparam=spi=on" ""
ensure_config_line "dtoverlay=mcp2515-can0" "dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=23" "# Habilitar CAN bus (fire-truck-app)"
ensure_config_line "dtoverlay=i2c-rtc,ds3231" "dtoverlay=i2c-rtc,ds3231" "# Habilitar RTC DS3231 (fire-truck-app)"
ensure_config_line "enable_uart=" "enable_uart=1" "# Habilitar UART y deshabilitar Bluetooth (fire-truck-app)"
ensure_config_line "dtoverlay=disable-bt" "dtoverlay=disable-bt" ""

log_info "Deshabilitando fake-hwclock para dar prioridad al RTC físico..."
apt-get -y remove fake-hwclock || true
update-rc.d -f fake-hwclock remove || true
log_ok "Configuración de hardware completada."


# --- PASO 6: Clonar Repositorio de la Aplicación ---
log_step "Paso 6: Clonando repositorio de la aplicación..."
if [ -d "${APP_DIR}/.git" ]; then
    log_warn "El directorio de la aplicación ya existe. Se forzará la actualización."
    sudo -u "${TARGET_USER}" git -C "${APP_DIR}" fetch --all
    sudo -u "${TARGET_USER}" git -C "${APP_DIR}" reset --hard "origin/${GIT_BRANCH}"
    sudo -u "${TARGET_USER}" git -C "${APP_DIR}" clean -fdx
else
    sudo -u "${TARGET_USER}" git clone --branch "${GIT_BRANCH}" "${REPO_URL}" "${APP_DIR}"
fi
log_ok "Repositorio clonado/actualizado en ${APP_DIR}."


# --- PASO 7: Configuración del Entorno Python ---
log_step "Paso 7: Configurando entorno virtual y dependencias..."
sudo -u "${TARGET_USER}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${TARGET_USER}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
log_ok "Entorno Python listo."


# --- PASO 8: Configuración de Permisos Finales ---
log_step "Paso 8: Estableciendo permisos de sistema..."
log_info "Configurando sudo sin contraseña para shutdown/reboot..."
SUDOERS_FILE="/etc/sudoers.d/99-fire-truck-app"
echo "${TARGET_USER} ALL=(ALL) NOPASSWD: /sbin/shutdown, /sbin/reboot" > "${SUDOERS_FILE}"
chmod 0440 "${SUDOERS_FILE}"

log_info "Haciendo ejecutables los scripts necesarios..."
chmod +x "${APP_DIR}/scripts/check_and_install_update.sh"
log_ok "Permisos establecidos."


# --- PASO 9: Instalación de Servicios systemd ---
log_step "Paso 9: Instalando servicios systemd..."
cp "${APP_DIR}/services/app.service" /etc/systemd/system/
cp "${APP_DIR}/services/updater.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable app.service
systemctl enable updater.service
log_ok "Servicios instalados y habilitados para el arranque."


# --- PASO 10: Finalización ---
log_step "¡Instalación completada!"
log_warn "Es NECESARIO reiniciar el sistema para aplicar los cambios de hardware."
read -p "¿Deseas reiniciar la Raspberry Pi ahora? (s/n): " REBOOT_CHOICE
if [[ "${REBOOT_CHOICE}" == "s" || "${REBOOT_CHOICE}" == "S" ]]; then
    log_info "Reiniciando el sistema ahora..."
    reboot
else
    log_info "No se reiniciará. Recuerda hacerlo manualmente con 'sudo reboot'."
fi

exit 0