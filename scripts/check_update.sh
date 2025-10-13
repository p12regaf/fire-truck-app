#!/bin/bash

# -- Configuración --
# TODO: Cambia estos valores según la configuración
GITHUB_USER="p12regaf"
GITHUB_REPO="fire-truck-app"
APP_DIR="/home/cosigein/fire-truck-app"
LOG_FILE="/home/cosigein/logs/updater.log"
TOKEN_FILE="/etc/fire-truck-app/github.token"
UPDATE_FLAG="/tmp/update_pending"
DOWNLOAD_PATH="/tmp/update.tar.gz"

# -- Función de Logging --
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a $LOG_FILE
}

# 1. Comprobaciones iniciales
log "Iniciando comprobación de actualizaciones..."

if [ ! -f "$TOKEN_FILE" ]; then
    log "ERROR: El archivo de token de GitHub no existe en $TOKEN_FILE. Abortando."
    exit 1
fi
GITHUB_TOKEN=$(cat $TOKEN_FILE)

if [ -z "$GITHUB_TOKEN" ]; then
    log "ERROR: El archivo de token de GitHub está vacío. Abortando."
    exit 1
fi

# 2. Obtener la versión instalada localmente
CURRENT_VERSION_FILE="$APP_DIR/.version"
if [ ! -f "$CURRENT_VERSION_FILE" ]; then
    log "ADVERTENCIA: No se encontró el archivo de versión local en $CURRENT_VERSION_FILE. Asumiendo versión 0.0.0."
    CURRENT_VERSION="0.0.0"
else
    CURRENT_VERSION=$(cat $CURRENT_VERSION_FILE)
fi
log "Versión actual instalada: $CURRENT_VERSION"

# 3. Consultar la última release en GitHub usando la API
API_URL="https://api.github.com/repos/$GITHUB_USER/$GITHUB_REPO/releases/latest"
API_RESPONSE=$(curl -s -H "Authorization: token $GITHUB_TOKEN" $API_URL)

# Extraer la versión y la URL de descarga del release
LATEST_VERSION=$(echo $API_RESPONSE | jq -r .tag_name | sed 's/v//g') # Quita la 'v' si la usas en los tags (ej. v1.0.1 -> 1.0.1)
DOWNLOAD_URL=$(echo $API_RESPONSE | jq -r .assets[0].browser_download_url) # Asume que el primer asset es el correcto

if [ "$LATEST_VERSION" == "null" ] || [ "$DOWNLOAD_URL" == "null" ]; then
    log "ERROR: No se pudo obtener la última versión de GitHub. Respuesta de la API:"
    log "$API_RESPONSE"
    exit 1
fi
log "Última versión disponible en GitHub: $LATEST_VERSION"

# 4. Comparar versiones
if [ "$LATEST_VERSION" == "$CURRENT_VERSION" ]; then
    log "La aplicación ya está actualizada. No se requiere ninguna acción."
    # Limpiar bandera por si quedó de un intento fallido anterior
    rm -f $UPDATE_FLAG
    exit 0
fi

# El comando 'sort -V' compara versiones correctamente (e.g., 1.0.10 > 1.0.2)
if [ "$(printf '%s\n' "$LATEST_VERSION" "$CURRENT_VERSION" | sort -V | head -n 1)" == "$LATEST_VERSION" ]; then
    log "La versión de GitHub ($LATEST_VERSION) no es más nueva que la actual ($CURRENT_VERSION). No se requiere ninguna acción."
    rm -f $UPDATE_FLAG
    exit 0
fi

log "Nueva versión detectada. Descargando $LATEST_VERSION desde $DOWNLOAD_URL..."

# 5. Descargar la nueva versión
HTTP_CODE=$(curl -sL -o $DOWNLOAD_PATH -w "%{http_code}" -H "Authorization: token $GITHUB_TOKEN" -H "Accept: application/octet-stream" "$DOWNLOAD_URL")

if [ "$HTTP_CODE" -ne 200 ] && [ "$HTTP_CODE" -ne 302 ]; then
    log "ERROR: Falló la descarga. Código HTTP: $HTTP_CODE"
    rm -f $DOWNLOAD_PATH
    exit 1
fi

log "Descarga completada con éxito en $DOWNLOAD_PATH."

# 6. Crear el fichero bandera para señalar que la actualización está lista
touch $UPDATE_FLAG
log "Actualización lista para ser instalada en el próximo apagado."

exit 0