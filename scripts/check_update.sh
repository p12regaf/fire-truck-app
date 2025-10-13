#!/bin/bash

# -- Configuración --
APP_DIR="/home/cosigein/fire-truck-app"
LOG_FILE="/home/cosigein/logs/updater.log"
UPDATE_FLAG="/tmp/update_pending"
GIT_BRANCH="main"

# -- Función de Logging --
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | sudo tee -a $LOG_FILE
}

log "Iniciando comprobación de actualizaciones vía Git..."

# Navegar al directorio de la aplicación
cd $APP_DIR || { log "ERROR: No se pudo acceder al directorio $APP_DIR"; exit 1; }

# 1. Actualizar el conocimiento del estado remoto sin cambiar los archivos locales
git remote update
if [ $? -ne 0 ]; then
    log "ERROR: 'git remote update' falló. ¿Hay conexión a internet y la deploy key es correcta?"
    exit 1
fi

# 2. Comprobar el estado
GIT_STATUS=$(git status -uno)

if echo "$GIT_STATUS" | grep -q "Your branch is up to date"; then
    log "La aplicación ya está actualizada. No se requiere ninguna acción."
    # Limpiar bandera por si quedó de un intento fallido anterior
    sudo rm -f $UPDATE_FLAG
    exit 0
fi

if echo "$GIT_STATUS" | grep -q "Your branch is behind"; then
    log "Nueva versión detectada en el repositorio. Preparando para actualizar en el próximo apagado."
    # Crear el fichero bandera para señalar que la actualización está lista
    sudo touch $UPDATE_FLAG
    log "Actualización lista para ser instalada."
    exit 0
fi

log "Estado de Git no reconocido. No se toma ninguna acción."
log "$GIT_STATUS"
exit 0