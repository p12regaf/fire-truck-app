import ftplib
import logging
import os
import threading
import time
from datetime import datetime

log = logging.getLogger(__name__)

class FTPTransmitter(threading.Thread):
    """
    Gestiona la subida de archivos al servidor FTP con intervalos separados.
    - Sube archivos de log de días anteriores (.log) periódicamente.
    - Sube archivos de estado en tiempo real (_RealTime.txt) con mayor frecuencia.
    """

    def __init__(self, config: dict, shutdown_event: threading.Event, session_manager):
        super().__init__(name="FTPTransmitter")
        self.ftp_config = config.get('ftp', {})
        self.paths_config = config.get('paths', {})
        self.shutdown_event = shutdown_event
        self.session_manager = session_manager
        
        self.log_scan_interval = self.ftp_config.get('log_upload_interval_sec', 300)
        self.realtime_scan_interval = self.ftp_config.get('realtime_upload_interval_sec', 30)

    def run(self):
        log.info(f"Iniciando transmisor FTP. Logs cada {self.log_scan_interval}s, RealTime cada {self.realtime_scan_interval}s.")
        
        # Ejecutar un ciclo de subida completo al arrancar
        log.info("Realizando ciclo de subida inicial completo (logs y tiempo real)...")
        self._perform_log_upload_cycle()
        self._perform_realtime_upload_cycle()
        log.info("Ciclo de subida inicial completado.")
        
        last_log_scan_time = time.time()
        last_realtime_scan_time = time.time()

        while not self.shutdown_event.is_set():
            current_time = time.time()

            # Comprobar si es momento de subir logs históricos
            if current_time - last_log_scan_time >= self.log_scan_interval:
                log.info("Iniciando ciclo de subida de archivos de log...")
                self._perform_log_upload_cycle()
                last_log_scan_time = current_time

            # Comprobar si es momento de subir archivos de tiempo real
            if current_time - last_realtime_scan_time >= self.realtime_scan_interval:
                log.info("Iniciando ciclo de subida de archivos de tiempo real...")
                self._perform_realtime_upload_cycle()
                last_realtime_scan_time = current_time
            
            # Esperar un poco para no consumir CPU
            self.shutdown_event.wait(1)

        log.info("Transmisor FTP detenido.")

    def _connect_ftp(self):
        """Establece y devuelve una conexión FTP, o None si falla."""
        try:
            ftp = ftplib.FTP()
            ftp.connect(self.ftp_config['host'], self.ftp_config['port'], timeout=20)
            ftp.login(self.ftp_config['user'], self.ftp_config['pass'])
            base_remote_dir = "datos_doback"
            if base_remote_dir not in ftp.nlst():
                ftp.mkd(base_remote_dir)
            ftp.cwd(base_remote_dir)
            log.debug("Conexión FTP establecida y en directorio 'datos_doback'.")
            return ftp
        except ftplib.all_errors as e:
            log.error(f"Error de conexión FTP: {e}")
            return None

    def _scan_and_upload(self, file_filter: callable):
        """Función genérica para escanear y subir archivos que cumplan un criterio."""
        data_root = self.paths_config.get('data_root')
        if not os.path.isdir(data_root):
            log.warning(f"El directorio raíz '{data_root}' no existe. No hay nada que subir.")
            return

        ftp = self._connect_ftp()
        if not ftp:
            log.warning("No se pudo conectar a FTP. Se reintentará en el próximo ciclo.")
            return

        try:
            for data_type_dir in os.listdir(data_root):
                local_type_path = os.path.join(data_root, data_type_dir)
                if not os.path.isdir(local_type_path):
                    continue

                for filename in os.listdir(local_type_path):
                    if self.shutdown_event.is_set():
                        log.warning("Señal de apagado recibida, abortando ciclo de subida.")
                        return

                    if file_filter(filename):
                        local_file_path = os.path.join(local_type_path, filename)
                        self._upload_file(ftp, local_file_path)

        except Exception as e:
            log.error(f"Error inesperado durante el ciclo de subida FTP: {e}", exc_info=True)
        finally:
            if ftp:
                ftp.quit()

    def _perform_log_upload_cycle(self):
        """Escanea y sube solo los archivos de log de días anteriores."""
        today_str = datetime.now().strftime('%Y%m%d')
        
        def log_filter(filename):
            if not filename.endswith(".log"):
                return False
            try:
                file_date_str = filename.split('_')[-1].split('.')[0]
                return file_date_str < today_str
            except IndexError:
                log.warning(f"No se pudo extraer fecha de '{filename}'. Omitiendo.")
                return False
        
        self._scan_and_upload(log_filter)

    def _perform_realtime_upload_cycle(self):
        """Escanea y sube solo los archivos _RealTime.txt."""
        self._scan_and_upload(lambda filename: filename.endswith("_RealTime.txt"))

    def _upload_file(self, ftp, local_path: str) -> bool:
        """
        Sube un único archivo al servidor FTP, creando la estructura de directorios
        remota en minúsculas: datos_doback/dobackXXX/tipo_dato/archivo.
        """
        try:
            filename = os.path.basename(local_path)
            # Ej: 'CAN' o 'ESTABILIDAD' -> convertido a 'can', 'estabilidad'
            data_type_name = os.path.basename(os.path.dirname(local_path)).lower()
            # Ej: 'DOBACK001' -> convertido a 'doback001'
            device_name = self.session_manager.device_name.lower()

            # --- Navegar o crear directorios remotos ---
            # Estamos en 'datos_doback/', ahora creamos 'dobackXXX/'
            if device_name not in ftp.nlst():
                ftp.mkd(device_name)
            ftp.cwd(device_name)
            
            # Ahora creamos 'tipo_dato/'
            if data_type_name not in ftp.nlst():
                ftp.mkd(data_type_name)
            ftp.cwd(data_type_name)

            log.info(f"  -> Subiendo {filename} a {ftp.pwd()}...")
            with open(local_path, 'rb') as f:
                ftp.storbinary(f'STOR {filename}', f)
            
            # Volver al directorio base 'datos_doback' para el siguiente archivo
            ftp.cwd('/datos_doback')
            return True
            
        except ftplib.all_errors as e:
            log.error(f"Error de FTP al subir el archivo {local_path}: {e}")
            try:
                ftp.cwd('/datos_doback')
            except ftplib.all_errors:
                log.error("No se pudo volver al directorio FTP base después de un error.")
            return False
        except FileNotFoundError:
            log.warning(f"El archivo {local_path} desapareció antes de poder subirlo.")
            return True
        except Exception as e:
            log.error(f"Error inesperado al subir {local_path}: {e}")
            return False