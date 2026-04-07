import logging
import sys
import os
import shutil
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler

# Eliminamos la clase ArchivingRotatingFileHandler ya que no se usará más la combinación de archivos.

def setup_logging(config: dict):
    """Configura el sistema de logging para toda la aplicación."""
    log_config = config.get('paths', {})
    log_level_str = config.get('system', {}).get('log_level', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    log_dir = log_config.get('app_logs', '/tmp/fire-truck-app_logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'fire-truck-app_app.log')

    # Directorio para archivos archivados (drop zone para FTP)
    data_root = log_config.get('data_root', '/tmp/fire-truck-app_data')
    archive_dir = os.path.join(data_root, 'log_archives')

    # Formato del log
    log_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Handler para la consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_format)

    # Handler principal con rotación estándar (5MB, 15 archivos)
    file_handler = RotatingFileHandler(
        log_file, 
        maxBytes=5*1024*1024, 
        backupCount=15
    )
    file_handler.setFormatter(log_format)

    # Handler para el LOG DIARIO (daily_YYYY-MM-DD.log)
    # Este archivo se subirá en el setup.py si su fecha es anterior a hoy.
    today_str = datetime.now().strftime('%Y-%m-%d')
    daily_log_file = os.path.join(log_dir, f'daily_{today_str}.log')
    daily_handler = logging.FileHandler(daily_log_file)
    daily_handler.setFormatter(log_format)

    # Configurar el logger raíz
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear() # Limpiar handlers previos
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(daily_handler)

    logging.info(f"Logging configurado. Modo: Archivo + Local Archive. Nivel: {log_level_str}.")