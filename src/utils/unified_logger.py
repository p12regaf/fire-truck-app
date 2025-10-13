import logging
import sys
from logging.handlers import RotatingFileHandler
import os

def setup_logging(config: dict):
    """Configura el sistema de logging para toda la aplicación."""
    log_config = config.get('paths', {})
    log_level_str = config.get('system', {}).get('log_level', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    log_dir = log_config.get('app_logs', '/tmp/fire-truck-app_logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'fire-truck-app_app.log')

    # Formato del log
    log_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Handler para la consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_format)

    # Handler para el archivo con rotación
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5*1024*1024, backupCount=5 # 5 MB por archivo, 5 archivos de respaldo
    )
    file_handler.setFormatter(log_format)

    # Configurar el logger raíz
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear() # Limpiar handlers previos
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logging.info(f"Logging configurado. Nivel: {log_level_str}. Archivo: {log_file}")