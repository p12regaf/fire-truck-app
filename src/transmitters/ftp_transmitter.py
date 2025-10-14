import ftplib
import logging
import os
import threading
import time
from datetime import datetime

log = logging.getLogger(__name__)

class FTPTransmitter(threading.Thread):
    """
    Gestiona la subida de archivos al servidor FTP.
    - Periódicamente, escanea el directorio de datos.
    - Sube los archivos de log de días anteriores (.log).
    - Sube siempre los archivos de estado en tiempo real (_RealTime.txt).
    """

    def __init__(self, config: dict, shutdown_event: threading.Event, session_manager):
        super().__init__(name="FTPTransmitter")
        self.ftp_config = config.get('ftp', {})
        self.paths_config = config.get('paths', {})
        self.shutdown_event = shutdown_event
        self.session_manager = session_manager
        
        self.scan_interval = self.ftp_config.get('upload_interval_sec', 300)

    def run(self):
        log.info("Iniciando transmisor FTP.")
        
        # Realizar un primer ciclo de subida inmediatamente al arrancar.
        log.info("Realizando ciclo de subida inicial...")
        self._perform_upload_cycle()
        log.info("Ciclo de subida inicial completado.")
        
        while not self.shutdown_event.is_set():
            # Esperar para el próximo ciclo de escaneo completo.
            self.shutdown_event.wait(self.scan_interval)

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
            # Entrar en el directorio base de datos_doback
            base_remote_dir = "datos_doback"
            if base_remote_dir not in ftp.nlst():
                log.info(f"Creando directorio remoto base: {base_remote_dir}")
                ftp.mkd(base_remote_dir)
            ftp.cwd(base_remote_dir)
            log.debug("Conexión FTP establecida y en directorio 'datos_doback'.")
            return ftp
        except ftplib.all_errors as e:
            log.error(f"Error de conexión FTP: {e}")
            return None

    def _perform_upload_cycle(self):
        """
        Escanea todos los directorios de datos y sube los archivos pertinentes.
        - Archivos .log de días pasados.
        - Archivos _RealTime.txt siempre.
        """
        log.info("Iniciando nuevo ciclo de escaneo para subida FTP...")
        data_root = self.paths_config.get('data_root')
        today_str = datetime.now().strftime('%Y%m%d')

        if not os.path.isdir(data_root):
            log.warning(f"El directorio raíz de datos '{data_root}' no existe. No hay nada que subir.")
            return

        ftp = self._connect_ftp()
        if not ftp:
            log.warning("No se pudo conectar a FTP. Se reintentará en el próximo ciclo.")
            return

        try:
            # Iterar sobre los directorios de tipo de dato (CAN, GPS, etc.)
            for data_type_dir in os.listdir(data_root):
                local_type_path = os.path.join(data_root, data_type_dir)
                if not os.path.isdir(local_type_path):
                    continue

                # Iterar sobre los archivos dentro de cada directorio de tipo
                for filename in os.listdir(local_type_path):
                    if self.shutdown_event.is_set():
                        log.warning("Señal de apagado recibida durante el ciclo de subida. Abortando.")
                        return

                    local_file_path = os.path.join(local_type_path, filename)
                    
                    upload = False
                    if filename.endswith("_RealTime.txt"):
                        upload = True
                    elif filename.endswith(".log"):
                        # Extraer la fecha del nombre del archivo
                        try:
                            file_date_str = filename.split('_')[-1].split('.')[0]
                            if file_date_str < today_str:
                                upload = True
                        except IndexError:
                            log.warning(f"No se pudo extraer la fecha del nombre de archivo: {filename}. Omitiendo.")
                    
                    if upload:
                        self._upload_file(ftp, local_file_path)

        except Exception as e:
            log.error(f"Error inesperado durante el ciclo de subida FTP: {e}", exc_info=True)
        finally:
            ftp.quit()

    def _upload_file(self, ftp, local_path: str) -> bool:
        """
        Sube un único archivo al servidor FTP, creando la estructura de directorios
        remota: DOBACKXXX/TIPO_DATO/archivo.
        """
        try:
            filename = os.path.basename(local_path)
            # Ej: 'CAN' o 'ESTABILIDAD'
            data_type_name = os.path.basename(os.path.dirname(local_path))
            device_name = self.session_manager.device_name # Ej: 'DOBACK001'

            # --- Navegar o crear directorios remotos ---
            # Estamos en 'datos_doback/', ahora creamos 'DOBACKXXX/'
            if device_name not in ftp.nlst():
                log.info(f"Creando directorio remoto de dispositivo: {device_name}")
                ftp.mkd(device_name)
            ftp.cwd(device_name)
            
            # Ahora creamos 'TIPO_DATO/'
            if data_type_name not in ftp.nlst():
                log.info(f"Creando directorio remoto de tipo de dato: {data_type_name}")
                ftp.mkd(data_type_name)
            ftp.cwd(data_type_name)

            log.info(f"  -> Subiendo {filename} a {ftp.pwd()}...")
            with open(local_path, 'rb') as f:
                ftp.storbinary(f'STOR {filename}', f)
            
            # Volver al directorio base 'datos_doback' para el siguiente archivo
            ftp.cwd('../..') # Salir de TIPO_DATO y de DOBACKXXX
            return True
            
        except ftplib.all_errors as e:
            log.error(f"Error de FTP al subir el archivo {local_path}: {e}")
            try:
                # Intentar volver a la raíz en caso de error para no afectar a la siguiente subida
                ftp.cwd('/')
                ftp.cwd('datos_doback')
            except ftplib.all_errors:
                log.error("No se pudo volver al directorio FTP base después de un error.")
            return False
        except FileNotFoundError:
            log.warning(f"El archivo {local_path} desapareció antes de poder subirlo.")
            return True
        except Exception as e:
            log.error(f"Error inesperado al subir {local_path}: {e}")
            return False