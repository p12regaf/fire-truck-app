import ftplib
import logging
import os
import threading
import time
import socket
from datetime import datetime

log = logging.getLogger(__name__)

def check_internet_connection(host="8.8.8.8", port=53, timeout=3):
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except socket.error:
        log.debug("No se pudo conectar a %s. Asumiendo que no hay internet.", host)
        return False

class FTPTransmitter(threading.Thread):
    """
    Gestiona la subida de archivos al servidor FTP.
    - Sube CUALQUIER sesión cerrada (archivos .txt que no son de la sesión actual).
    - Marca los archivos subidos renombrándolos a .enviado.
    - Sube archivos de estado en tiempo real (_RealTime.txt) constantemente.
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
        log.info(f"Iniciando transmisor FTP. Logs cada {self.log_scan_interval}s.")

        initial_wait_sec = 10
        log.info(f"Esperando {initial_wait_sec}s antes del primer ciclo...")
        if self.shutdown_event.wait(initial_wait_sec):
            return
        
        # 1. Ciclo Inicial: Subir TODO lo que falte al arrancar
        log.info("ARRANQUE: Buscando y subiendo sesiones pendientes...")
        self._perform_log_upload_cycle()
        self._perform_realtime_upload_cycle()
        log.info("Ciclo inicial completado.")
        
        last_log_scan_time = time.time()
        last_realtime_scan_time = time.time()

        while not self.shutdown_event.is_set():
            current_time = time.time()

            # Ciclo de logs históricos (sesiones cerradas)
            if current_time - last_log_scan_time >= self.log_scan_interval:
                self._perform_log_upload_cycle()
                last_log_scan_time = current_time

            # Ciclo de tiempo real
            if current_time - last_realtime_scan_time >= self.realtime_scan_interval:
                self._perform_realtime_upload_cycle()
                last_realtime_scan_time = current_time
            
            self.shutdown_event.wait(5)

        log.info("Transmisor FTP detenido.")

    def _connect_ftp(self):
        try:
            ftp = ftplib.FTP()
            ftp.connect(self.ftp_config['host'], self.ftp_config['port'], timeout=20)
            ftp.login(self.ftp_config['user'], self.ftp_config['pass'])
            base_remote_dir = "datos_doback"
            if base_remote_dir not in ftp.nlst():
                ftp.mkd(base_remote_dir)
            ftp.cwd(base_remote_dir)
            return ftp
        except ftplib.all_errors as e:
            log.error(f"Error de conexión FTP: {e}")
            return None

    def _scan_and_upload(self, file_filter: callable, rename_after_upload: bool = False):
        """
        Escanéa y sube.
        :param rename_after_upload: Si es True, cambia la extensión a .enviado tras subir.
        """
        if not check_internet_connection():
            return
        
        data_root = self.paths_config.get('data_root')
        if not os.path.isdir(data_root):
            return

        ftp = self._connect_ftp()
        if not ftp:
            return

        try:
            for data_type_dir in os.listdir(data_root):
                local_type_path = os.path.join(data_root, data_type_dir)
                if not os.path.isdir(local_type_path):
                    continue

                for filename in os.listdir(local_type_path):
                    if self.shutdown_event.is_set():
                        return

                    local_file_path = os.path.join(local_type_path, filename)

                    # Aplicar filtro personalizado
                    if file_filter(filename, local_file_path):
                        if self._upload_file(ftp, local_file_path):
                            # Si se subió correctamente y es un log histórico, lo marcamos
                            if rename_after_upload:
                                try:
                                    new_path = local_file_path.replace('.txt', '.enviado')
                                    os.rename(local_file_path, new_path)
                                    log.info(f"Archivo marcado como enviado: {filename}")
                                except OSError as e:
                                    log.error(f"Error renombrando {filename}: {e}")

        except Exception as e:
            log.error(f"Error ciclo FTP: {e}", exc_info=True)
        finally:
            if ftp:
                try:
                    ftp.quit()
                except:
                    pass

    def _perform_log_upload_cycle(self):
        """
        Sube sesiones pendientes.
        Criterio: Archivo termina en .txt Y NO es el archivo activo actual.
        Acción: Renombrar a .enviado tras éxito.
        """
        def log_filter(filename, full_path):
            # Solo procesar archivos .txt
            if not filename.endswith(".txt"):
                return False
            # Ignorar archivos de RealTime
            if "RealTime" in filename:
                return False
            # Ignorar el archivo que se está escribiendo AHORA mismo
            if self.session_manager.is_file_active(full_path):
                return False
            return True
        
        # Activamos rename_after_upload=True para marcar lo "que faltaba" como hecho
        self._scan_and_upload(log_filter, rename_after_upload=True)

    def _perform_realtime_upload_cycle(self):
        """Sube solo los archivos _RealTime.txt (sin renombrar)."""
        def rt_filter(filename, full_path):
            return filename.endswith("_RealTime.txt")
            
        self._scan_and_upload(rt_filter, rename_after_upload=False)

    def _upload_file(self, ftp, local_path: str) -> bool:
        try:
            filename = os.path.basename(local_path)
            data_type_name = os.path.basename(os.path.dirname(local_path)).lower()
            device_name = self.session_manager.device_name.lower()

            if device_name not in ftp.nlst():
                ftp.mkd(device_name)
            ftp.cwd(device_name)
            
            if data_type_name not in ftp.nlst():
                ftp.mkd(data_type_name)
            ftp.cwd(data_type_name)

            log.info(f"  -> Subiendo {filename}...")
            with open(local_path, 'rb') as f:
                ftp.storbinary(f'STOR {filename}', f)
            
            ftp.cwd('/datos_doback')
            return True
            
        except Exception as e:
            log.error(f"Fallo al subir {local_path}: {e}")
            try:
                ftp.cwd('/datos_doback')
            except:
                pass
            return False