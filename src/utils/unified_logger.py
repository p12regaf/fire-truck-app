import shutil
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler

class ArchivingRotatingFileHandler(RotatingFileHandler):
    """
    Handler que, al alcanzar el límite de archivos de rotación, 
    combina todos los backups en un único archivo de log "archivado"
    en lugar de borrar el más antiguo.
    """
    def __init__(self, filename, mode='a', maxBytes=0, backupCount=0, encoding=None, delay=False, archive_dir=None):
        super().__init__(filename, mode, maxBytes, backupCount, encoding, delay)
        self.archive_dir = archive_dir
        if self.archive_dir:
            os.makedirs(self.archive_dir, exist_ok=True)

    def doRollover(self):
        """
        Realiza la rotación. Si se va a sobrepasar backupCount, combina los archivos.
        """
        if self.stream:
            self.stream.close()
            self.stream = None
            
        # Si el archivo más antiguo ya existe, es que vamos a rotar y perderlo.
        # En ese caso, archivamos todos los actuales.
        oldest_backup = self.rotation_filename(f"{self.baseFilename}.{self.backupCount}")
        
        if self.backupCount > 0 and os.path.exists(oldest_backup):
            self._archive_logs()
        
        super().doRollover()
        
    def _archive_logs(self):
        """Combina app.log.1...N y app.log en un solo archivo en archive_dir."""
        try:
            files_to_merge = []
            # De más antiguo a más nuevo para orden cronológico
            for i in range(self.backupCount, 0, -1):
                f = self.rotation_filename(f"{self.baseFilename}.{i}")
                if os.path.exists(f):
                    files_to_merge.append(f)
            
            if os.path.exists(self.baseFilename):
                files_to_merge.append(self.baseFilename)
            
            if not files_to_merge or not self.archive_dir:
                return

            # Obtener timestamp de la primera línea del archivo más antiguo para el nombre
            archive_name = f"log_archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                with open(files_to_merge[0], 'r', encoding=self.encoding, errors='ignore') as f:
                    first_line = f.readline()
                    # Buscar patrones de fecha YYYY-MM-DD o DD/MM/YYYY
                    match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})|(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})', first_line)
                    if match:
                        ts_str = match.group(0).replace(' ', '_').replace(':', '-').replace('/', '-')
                        archive_name = f"log_{ts_str}"
            except Exception:
                pass

            archive_path = os.path.join(self.archive_dir, f"{archive_name}.txt")
            
            # Combinar archivos
            with open(archive_path, 'wb') as outfile:
                for f_path in files_to_merge:
                    with open(f_path, 'rb') as infile:
                        shutil.copyfileobj(infile, outfile)
                        outfile.write(b"\n--- ARCHIVE CHUNK DIVIDER ---\n")
            
            # Borrar los backups procesados (el actual se rotará normal por super.doRollover)
            for f_path in files_to_merge[:-1]:
                try:
                    os.remove(f_path)
                except OSError:
                    pass
                    
            logging.getLogger().info(f"Logs archivados localmente para FTP: {archive_path}")

        except Exception as e:
            # Usamos print porque el logger podría estar en estado inestable durante el rollover
            print(f"Error crítico archivando logs: {e}")

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

    # Handler personalizado para el archivo con rotación y archivado
    file_handler = ArchivingRotatingFileHandler(
        log_file, 
        maxBytes=5*1024*1024, 
        backupCount=15,
        archive_dir=archive_dir
    )
    file_handler.setFormatter(log_format)

    # Configurar el logger raíz
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear() # Limpiar handlers previos
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logging.info(f"Logging configurado. Modo: Archivo + Local Archive. Nivel: {log_level_str}.")