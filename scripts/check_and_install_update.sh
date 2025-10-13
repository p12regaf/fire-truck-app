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