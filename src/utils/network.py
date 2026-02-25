import logging
import socket

log = logging.getLogger(__name__)

def check_internet_connection(host="8.8.8.8", port=53, timeout=3):
    """Comprueba si hay conexión a internet intentando conectar a un DNS público."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except socket.error:
        log.debug("No se pudo conectar a %s. Asumiendo que no hay internet.", host)
        return False
