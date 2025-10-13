#!/bin/bash

# -- Configuración --
APP_DIR="/home/cosigein/fire-truck-app"
APP_SERVICE="app.service"
UPDATE_FLAG="/tmp/update_pending"
BACKUP_DIR="/home/cosigein/fire-truck-app-backup"
LOG_FILE="/home/cosigein/logs/updater.log"

# -- Función de Logging --
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - [INSTALLER] - $1" | sudo tee -a $LOG_FILE
}

log "Iniciando script de instalación de actualización."

if [ ! -f "$UPDATE_FLAG" ]; then
    log "No hay bandera de actualización. No hay nada que hacer."
    exit 0
fi

# 1. Parar el servicio
log "Asegurando que el servicio $APP_SERVICE está detenido."
sudo systemctl stop $APP_SERVICE

# 2. Crear backup (opcional pero recomendado)
log "Creando backup de la aplicación actual en $BACKUP_DIR..."
sudo rm -rf $BACKUP_DIR
sudo cp -r $APP_DIR $BACKUP_DIR

# 3. Ejecutar git pull para actualizar
log "Ejecutando 'git pull' para descargar los cambios..."
cd $APP_DIR
# Importante: Ejecutamos el comando como el usuario 'cosigein'
sudo -u cosigein git pull
if [ $? -ne 0 ]; then
    log "ERROR: 'git pull' falló. Revirtiendo desde el backup."
    sudo rm -rf $APP_DIR
    sudo mv $BACKUP_DIR $APP_DIR
    # No borramos la bandera. Se reintentará en el próximo apagado.
    exit 1
fi

# 4. Crear venv si no existe y luego instalar dependencias
VENV_DIR="$APP_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    log "El directorio venv no existe. Creándolo ahora..."
    # Creamos el venv como el usuario 'cosigein'
    sudo -u cosigein python3 -m venv $VENV_DIR
fi

if [ -f "$APP_DIR/requirements.txt" ]; then
    log "Instalando/actualizando dependencias en el venv..."
    sudo $VENV_DIR/bin/pip install -r "$APP_DIR/requirements.txt"
fi

# 5. Mover y recargar el servicio systemd si ha cambiado
if [ -f "$APP_DIR/services/$APP_SERVICE" ]; then
    # Comprobar si el archivo de servicio ha cambiado realmente
    if ! cmp -s "$APP_DIR/services/$APP_SERVICE" "/etc/systemd/system/$APP_SERVICE"; then
        log "El archivo de servicio ha cambiado. Actualizando..."
        sudo cp "$APP_DIR/services/$APP_SERVICE" "/etc/systemd/system/$APP_SERVICE"
        sudo systemctl daemon-reload
    else
        log "El archivo de servicio no ha cambiado."
    fi
fi

# 6. Limpieza
log "Limpiando archivos temporales..."
sudo rm -f $UPDATE_FLAG

log "¡Actualización completada con éxito! El sistema procederá con el apagado/reinicio."
exit 0