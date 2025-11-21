#!/bin/bash

APP_DIR="/home/cosigein/fire-truck-app"
LOG_FILE="/home/cosigein/logs/updater.log"
APP_SERVICE="app.service"
GIT_BRANCH=$(git -C $APP_DIR rev-parse --abbrev-ref HEAD)

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - [UPDATER] - $1" | tee -a $LOG_FILE
}

log "--- Iniciando script de comprobación e instalación ---"

cd $APP_DIR || { log "ERROR: No se pudo acceder a $APP_DIR"; exit 1; }

# Intentar actualizar repositorio. Si falla (sin internet), continuamos para arrancar la app de todos modos.
log "Actualizando el repositorio remoto..."
git remote update &> /dev/null

log "Comprobando el estado del repositorio local..."
GIT_STATUS=$(LANG= C git status -uno)

if [[ $GIT_STATUS == *"Your branch is behind"* ]]; then
    log "¡Nueva versión detectada! Iniciando proceso de actualización."

    log "Deteniendo el servicio $APP_SERVICE..."
    sudo systemctl stop $APP_SERVICE

    log "Forzando la actualización..."
    if ! git fetch --all || ! git reset --hard origin/$GIT_BRANCH; then
        log "ERROR: Falló git fetch/reset. Intentando arrancar la app existente..."
        sudo systemctl start $APP_SERVICE
        exit 1
    fi

    log "Limpiando archivos no rastreados..."
    git clean -fdx

    log "Instalando dependencias..."
    if [ -f ".venv/bin/pip" ]; then
        .venv/bin/pip install -r requirements.txt
    else
        log "ERROR: Entorno virtual no encontrado."
        # Intentar arrancar de todas formas por si acaso
        sudo systemctl start $APP_SERVICE
        exit 1
    fi
    
    log "¡Actualización completada! Reiniciando el sistema..."
    sudo reboot

elif [[ $GIT_STATUS == *"Your branch is up to date"* ]]; then
    log "La aplicación ya está actualizada."
    log "Iniciando aplicación principal..."
    # --- CORRECCIÓN: Arrancar explícitamente la app aquí ---
    sudo systemctl start $APP_SERVICE
    exit 0
else
    log "Estado de Git no reconocido o error de red. Arrancando versión actual por seguridad."
    log "Salida de Git: $GIT_STATUS"
    # --- CORRECCIÓN: Arrancar la app incluso en caso de error (modo fail-safe) ---
    sudo systemctl start $APP_SERVICE
    exit 1
fi