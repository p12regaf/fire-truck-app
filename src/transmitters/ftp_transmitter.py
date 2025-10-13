import ftplib
import logging
import os
import threading
import time
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

class FTPTransmitter(threading.Thread):
    """
    Gestiona la subida de archivos al servidor FTP.
    - Al iniciar, busca y sube todas las sesiones pasadas no subidas.
    - Periódicamente, repite este proceso.
    - Periódicamente, sube los archivos de estado en tiempo real de la sesión actual.
    """
    UPLOAD_FLAG_FILENAME = ".ftp_uploaded"

    def __init__(self, config: dict, shutdown_event: threading.Event, session_manager):
        super().__init__(name="FTPTransmitter")
        self.ftp_config = config.get('ftp', {})
        self.paths_config = config.get('paths', {})
        self.shutdown_event = shutdown_event
        self.session_manager = session_manager
        
        self.scan_interval = self.ftp_config.get('upload_interval_sec', 300)
        self.realtime_interval = self.ftp_config.get('realtime_interval_sec', 30)
        self.last_realtime_upload_time = 0

    def run(self):
        log.info("Iniciando transmisor FTP.")
        
        # Realizar un primer ciclo de subida inmediatamente al arrancar.
        log.info("Realizando ciclo de subida inicial...")
        self._perform_upload_cycle()
        log.info("Ciclo de subida inicial completado.")
        
        while not self.shutdown_event.is_set():
            # Esperar para el próximo ciclo de escaneo completo.
            # Usamos wait con un timeout más corto para poder reaccionar antes al apagado.
            wait_time = self.scan_interval
            while wait_time > 0 and not self.shutdown_event.is_set():
                # Comprobar si es hora de subir los archivos en tiempo real
                if time.time() - self.last_realtime_upload_time > self.realtime_interval:
                    self._upload_current_session_realtime()

                sleep_chunk = min(wait_time, 5.0) # Dormir en trozos de 5s
                time.sleep(sleep_chunk)
                wait_time -= sleep_chunk

            if self.shutdown_event.is_set():
                break

            log.info("Iniciando ciclo de subida periódico...")
            self._perform_upload_cycle()

        log.info("Transmisor FTP detenido.")

    def _connect_ftp(self):
        """Establece y devuelve una conexión FTP, o None si falla."""
        try:
            ftp = ftplib.FTP()
            ftp.connect(self.ftp_config['host'], self.ftp_config['port'], timeout=20)
            ftp.login(self.ftp_config['user'], self.ftp_config['pass'])
            log.debug("Conexión FTP establecida.")
            return ftp
        except ftplib.all_errors as e:
            log.error(f"Error de conexión FTP: {e}")
            return None
    
    def _upload_current_session_realtime(self):
        """Sube solo los archivos _RealTime.txt de la sesión actual."""
        log.debug("Iniciando subida de archivos en tiempo real.")
        ftp = self._connect_ftp()
        if not ftp:
            return

        try:
            session_path = self.session_manager.session_path
            for filename in os.listdir(session_path):
                if "RealTime.txt" in filename:
                    local_path = os.path.join(session_path, filename)
                    self._upload_file(ftp, local_path)
            self.last_realtime_upload_time = time.time()
        except Exception as e:
            log.error(f"Error inesperado subiendo archivos en tiempo real: {e}")
        finally:
            ftp.quit()

    def _perform_upload_cycle(self):
        """
        Escanea todos los directorios de sesión. Sube las sesiones pasadas y
        los archivos en tiempo real de la sesión actual.
        """
        log.info("Iniciando nuevo ciclo de escaneo para subida FTP...")
        data_root = self.paths_config.get('data_root')
        current_session_path = self.session_manager.session_path

        if not os.path.isdir(data_root):
            log.warning(f"El directorio raíz de datos '{data_root}' no existe. No hay nada que subir.")
            return

        ftp = self._connect_ftp()
        if not ftp:
            log.warning("No se pudo conectar a FTP. Se reintentará en el próximo ciclo.")
            return

        try:
            # Iterar sobre las carpetas de fecha (ej. '20231225')
            for date_dir in sorted(os.listdir(data_root)):
                date_path = os.path.join(data_root, date_dir)
                if not os.path.isdir(date_path): continue

                # Iterar sobre las carpetas de sesión (ej. 'session_001_12-34-56')
                for session_dir in sorted(os.listdir(date_path)):
                    session_path = os.path.join(date_path, session_dir)
                    if not os.path.isdir(session_path): continue
                    
                    if self.shutdown_event.is_set():
                        log.warning("Señal de apagado recibida durante el ciclo de subida. Abortando.")
                        return

                    # Comprobar si la sesión es la actual o una pasada
                    if session_path == current_session_path:
                        # Para la sesión actual, no hacemos nada aquí, se gestiona con _upload_current_session_realtime
                        continue
                    else:
                        # Es una sesión pasada, procesarla para subirla si es necesario
                        self._process_past_session(ftp, session_path)
        
        except Exception as e:
            log.error(f"Error inesperado durante el ciclo de subida FTP: {e}")
        finally:
            ftp.quit()

    def _process_past_session(self, ftp, session_path: str):
        """
        Sube todos los archivos de una sesión pasada si no ha sido subida antes.
        """
        flag_file_path = os.path.join(session_path, self.UPLOAD_FLAG_FILENAME)
        if os.path.exists(flag_file_path):
            log.debug(f"La sesión {os.path.basename(session_path)} ya fue subida. Omitiendo.")
            return

        log.info(f"Nueva sesión para subir encontrada: {os.path.basename(session_path)}")
        
        files_to_upload = [f for f in os.listdir(session_path) if os.path.isfile(os.path.join(session_path, f))]
        
        if not files_to_upload:
            log.warning(f"La sesión {os.path.basename(session_path)} está vacía. Marcando como subida.")
            self._create_upload_flag(session_path)
            return

        success = True
        for filename in files_to_upload:
            local_path = os.path.join(session_path, filename)
            if not self._upload_file(ftp, local_path):
                success = False
                log.error(f"Fallo al subir el archivo {filename} de la sesión {os.path.basename(session_path)}. Se reintentará en el próximo ciclo.")
                break # Si un archivo falla, no marcar la sesión como subida
        
        if success:
            log.info(f"Todos los archivos de la sesión {os.path.basename(session_path)} subidos con éxito.")
            self._create_upload_flag(session_path)

    def _upload_file(self, ftp, local_path: str) -> bool:
        """Sube un único archivo al servidor FTP, creando la estructura de directorios necesaria."""
        try:
            # Extraer 'fecha/sesion/archivo' de la ruta local
            parts = local_path.split(os.sep)
            remote_filename = parts[-1]
            remote_session_dir = parts[-2]
            remote_date_dir = parts[-3]

            # Navegar o crear directorios remotos
            if remote_date_dir not in ftp.nlst():
                log.info(f"Creando directorio remoto: {remote_date_dir}")
                ftp.mkd(remote_date_dir)
            ftp.cwd(remote_date_dir)
            
            if remote_session_dir not in ftp.nlst():
                log.info(f"Creando directorio remoto de sesión: {remote_session_dir}")
                ftp.mkd(remote_session_dir)
            ftp.cwd(remote_session_dir)

            log.info(f"  -> Subiendo {remote_filename}...")
            with open(local_path, 'rb') as f:
                ftp.storbinary(f'STOR {remote_filename}', f)
            
            # Volver al directorio raíz del FTP para el siguiente archivo
            ftp.cwd('/')
            return True
            
        except ftplib.all_errors as e:
            log.error(f"Error de FTP al subir el archivo {local_path}: {e}")
            ftp.cwd('/') # Intentar volver a la raíz en caso de error
            return False
        except FileNotFoundError:
            log.warning(f"El archivo {local_path} desapareció antes de poder subirlo.")
            return True # Considerar éxito para no bloquear la subida de la sesión
        except Exception as e:
            log.error(f"Error inesperado al subir {local_path}: {e}")
            return False

    def _create_upload_flag(self, session_path: str):
        """Crea un archivo vacío para marcar la sesión como subida."""
        try:
            flag_file_path = os.path.join(session_path, self.UPLOAD_FLAG_FILENAME)
            with open(flag_file_path, 'w') as f:
                pass # Crear archivo vacío
            log.info(f"Sesión {os.path.basename(session_path)} marcada como subida.")
        except IOError as e:
            log.error(f"No se pudo crear el flag de subida para la sesión {os.path.basename(session_path)}: {e}")