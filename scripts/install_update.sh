#!/bin/bash

# -- Configuración --
APP_DIR="/home/cosigein/fire-truck-app"
APP_SERVICE="app.service"
UPDATE_FLAG="/tmp/update_pending"
DOWNLOAD_PATH="/tmp/update.tar.gz"
BACKUP_DIR="/home/cosigein/fire-truck-app-backup"
LOG_FILE="/home/cosigein/logs/updater.log"

# -- Función de Logging --
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - [INSTALLER] - $1" | tee -a $LOG_FILE
}

log "Iniciando script de instalación de actualización."

# 1. Verificar si la actualización es realmente necesaria
if [ ! -f "$UPDATE_FLAG" ]; then
    log "No hay bandera de actualización. No hay nada que hacer."
    exit 0
fi

if [ ! -f "$DOWNLOAD_PATH" ]; then
    log "ERROR: La bandera de actualización existe, pero el archivo descargado no se encuentra en $DOWNLOAD_PATH. Abortando."
    rm -f $UPDATE_FLAG
    exit 1
fi

# 2. Parar el servicio por si acaso (aunque ya debería estar en proceso de parada)
log "Asegurando que el servicio app.service está detenido."
sudo systemctl stop $APP_SERVICE

# 3. Crear un backup de la versión actual
log "Creando backup de la aplicación actual en $BACKUP_DIR..."
rm -rf $BACKUP_DIR # Elimina backup antiguo
mv $APP_DIR $BACKUP_DIR

# 4. Descomprimir la nueva versión
log "Descomprimiendo la nueva versión en $APP_DIR..."
mkdir -p $APP_DIR
tar -xzf $DOWNLOAD_PATH -C $APP_DIR --strip-components=1 # --strip-components=1 es útil si tu .tar.gz tiene una carpeta raíz

# 5. Restaurar configuración si es necesario (MUY IMPORTANTE)
# Esto evita que cada actualización sobreescriba una configuración personalizada.
if [ -f "$BACKUP_DIR/config/config.yaml" ]; then
    log "Restaurando config.yaml del backup."
    cp "$BACKUP_DIR/config/config.yaml" "$APP_DIR/config/config.yaml"
fi

# 6. Mover el nuevo archivo de servicio
if [ -f "$APP_DIR/services/$APP_SERVICE" ]; then
    log "Moviendo el nuevo archivo de servicio a /etc/systemd/system/..."
    sudo cp "$APP_DIR/services/$APP_SERVICE" "/etc/systemd/system/$APP_SERVICE"
    sudo systemctl daemon-reload
else
    log "ADVERTENCIA: No se encontró un nuevo archivo .service en el paquete de actualización."
fi

# 7. Instalar/actualizar dependencias de Python
if [ -f "$APP_DIR/requirements.txt" ]; then
    log "Instalando/actualizando dependencias desde requirements.txt..."
    # Es importante usar el python3 correcto y quizás un entorno virtual si lo usas
    python3 -m pip install -r "$APP_DIR/requirements.txt"
fi

# 8. Limpieza
log "Limpiando archivos temporales..."
rm -f $DOWNLOAD_PATH
rm -f $UPDATE_FLAG

log "¡Actualización completada con éxito! El sistema se apagará ahora."
exit 0