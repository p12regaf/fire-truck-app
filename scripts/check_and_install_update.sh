#!/bin/bash

APP_DIR="/home/cosigein/fire-truck-app"
LOG_FILE="/home/cosigein/logs/updater.log"
APP_SERVICE="app.service"
# Obtiene el nombre de la rama actual para hacerlo más genérico
GIT_BRANCH=$(git -C $APP_DIR rev-parse --abbrev-ref HEAD)

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - [UPDATER] - $1" | tee -a $LOG_FILE
}

log "--- Iniciando script de comprobación e instalación ---"

# Navegar al directorio de la aplicación
cd $APP_DIR || { log "ERROR: No se pudo acceder a $APP_DIR"; exit 1; }

# Comprobar estado de Git
# NOTA: Ya no se necesita 'sudo -u cosigein' porque el servicio se ejecuta como ese usuario
log "Actualizando el repositorio remoto..."
git remote update &> /dev/null

log "Comprobando el estado del repositorio local..."
GIT_STATUS=$(LANG= C git status -uno)

if [[ $GIT_STATUS == *"Your branch is behind"* ]]; then
    log "¡Nueva versión detectada! Iniciando proceso de actualización."

    log "Deteniendo el servicio $APP_SERVICE..."
    # Necesitamos usar sudo aquí para interactuar con systemctl
    sudo systemctl stop $APP_SERVICE

    log "Forzando la actualización desde el repositorio remoto (git reset --hard)..."
    if ! git fetch --all || ! git reset --hard origin/$GIT_BRANCH; then
        log "ERROR: El proceso de 'git fetch/reset' falló. Se reintentará en el próximo arranque."
        sudo systemctl start $APP_SERVICE
        exit 1
    fi

    log "Limpiando archivos no rastreados..."
    git clean -fdx

    # No es necesario recrear el venv, solo instalar dependencias es suficiente y más rápido
    # log "Recreando el entorno virtual de Python..."
    # python3 -m venv .venv
    
    log "Instalando/actualizando dependencias..."
    # Asegurarse de que el venv exista
    if [ -f ".venv/bin/pip" ]; then
        .venv/bin/pip install -r requirements.txt
    else
        log "ERROR: Entorno virtual no encontrado. No se pueden instalar dependencias."
        sudo systemctl start $APP_SERVICE
        exit 1
    fi
    
    log "¡Actualización completada! Reiniciando el sistema para aplicar los cambios."
    # Necesitamos sudo para reiniciar
    sudo reboot

elif [[ $GIT_STATUS == *"Your branch is up to date"* ]]; then
    log "La aplicación ya está actualizada."
    exit 0
else
    log "Estado de Git no reconocido o error. Saliendo."
    log "Salida de Git: $GIT_STATUS"
    exit 1
fi