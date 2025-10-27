import logging
import threading
from queue import Queue, Empty
import time
from datetime import datetime
import os
import ftplib
import RPi.GPIO as GPIO

from .session_manager import SessionManager
from src.data_acquirers.can_acquirer import CANAcquirer
from src.data_acquirers.gps_acquirer import GPSAcquirer
from src.data_acquirers.imu_acquirer import IMUAcquirer # Importante para las claves
from src.data_acquirers.gpio_acquirer import GPIOAcquirer
from src.transmitters.ftp_transmitter import FTPTransmitter
from .power_monitor import PowerMonitor
from .alarm_monitor import AlarmMonitor
from .reboot_monitor import RebootMonitor

log = logging.getLogger(__name__)

class AppController:
    def __init__(self, config: dict):
        self.config = config
        self.data_queue = Queue()
        self.shutdown_event = threading.Event()
        self.workers = []
        self.active_data_types = []
        self.latest_data = {}
        self.data_lock = threading.Lock()
        
        self.session_manager = SessionManager(config)

        # Instanciar todos los componentes (trabajadores)
        self._initialize_workers()
        
        # Hilo para procesar la cola de datos
        self.processor_thread = threading.Thread(
            target=self._process_data_queue,
            name="DataProcessor"
        )
        
    def _initialize_workers(self):
        """Crea instancias de todos los recolectores y transmisores."""
        sources_config = self.config.get('data_sources', {})
        
        if sources_config.get('can', {}).get('enabled', False):
            self.workers.append(CANAcquirer(self.config, self.data_queue, self.shutdown_event))
            self.active_data_types.append('can')
        
        if sources_config.get('gps', {}).get('enabled', False):
            self.workers.append(GPSAcquirer(self.config, self.data_queue, self.shutdown_event))
            self.active_data_types.append('gps')
            
        if sources_config.get('estabilometro', {}).get('enabled', False):
            self.workers.append(IMUAcquirer(self.config, self.data_queue, self.shutdown_event))
            self.active_data_types.append('estabilometro')
            
        if sources_config.get('gpio_rotativo', {}).get('enabled', False):
            self.workers.append(GPIOAcquirer(self.config, self.data_queue, self.shutdown_event))
            self.active_data_types.append('rotativo')

        if self.config.get('ftp', {}).get('enabled', False):
            log.info("FTP Transmitter está habilitado. Inicializando...")
            self.workers.append(FTPTransmitter(self.config, self.shutdown_event, self.session_manager))
        
        if self.config.get('system', {}).get('power_monitor', {}).get('enabled', False):
            log.info("Power Monitor está habilitado. Inicializando...")
            self.workers.append(PowerMonitor(self.config, self))

        if self.config.get('system', {}).get('alarm_monitor', {}).get('enabled', False):
            log.info("Alarm Monitor está habilitado. Inicializando...")
            self.workers.append(AlarmMonitor(self.config, self))

        if self.config.get('system', {}).get('reboot_monitor', {}).get('enabled', False):
            log.info("Reboot Monitor está habilitado. Inicializando...")
            self.workers.append(RebootMonitor(self.config, self))
            
        log.info(f"{len(self.workers)} trabajadores inicializados.")
        log.info(f"Tipos de datos activos: {self.active_data_types}")

        self._setup_gpio_pins()
        
        # Crear los directorios de datos necesarios (ej. /datos/CAN, /datos/GPS)
        self.session_manager.ensure_data_directories(self.active_data_types)


    def _setup_gpio_pins(self):
        """
        Configura todos los pines GPIO de los monitores de forma centralizada
        para evitar condiciones de carrera.
        """
        log.info("Configurando pines GPIO de forma centralizada...")
        try:
            GPIO.setmode(GPIO.BCM)
            # Desactivar advertencias sobre canales en uso
            GPIO.setwarnings(False) 

            # Configurar cada pin que se va a usar
            for worker in self.workers:
                if isinstance(worker, (PowerMonitor, AlarmMonitor, RebootMonitor)):
                    if worker.pin is not None:
                        if isinstance(worker, RebootMonitor):
                            # El RebootMonitor actúa como un actuador, estableciendo una señal de 'OK'
                            log.info(f"Configurando pin {worker.pin} para {worker.name} como SALIDA.")
                            GPIO.setup(worker.pin, GPIO.OUT)
                        else:
                            # Los otros monitores son sensores de entrada
                            log.info(f"Configurando pin {worker.pin} para {worker.name} como ENTRADA con pull_up_down={worker.pull_up_down}")
                            GPIO.setup(worker.pin, GPIO.IN, pull_up_down=worker.pull_up_down)
            
            log.info("Configuración centralizada de GPIO completada.")
        except Exception as e:
            log.critical(f"FALLO CRÍTICO durante la configuración centralizada de GPIO: {e}")

    def _write_session_headers(self):
        """
        Escribe la cabecera de la nueva sesión Y la cabecera de columnas
        en cada archivo de log diario y prepara los archivos RealTime.
        """
        log.info("Escribiendo cabeceras de sesión y de columnas en archivos de log...")
        for data_type in self.active_data_types:
            try:
                # Escribir cabeceras en el archivo de log principal
                log_path = self.session_manager.get_log_path(data_type)
                session_header = self.session_manager.get_session_header(data_type)
                column_header = self.session_manager.get_column_header(data_type)
                
                with open(log_path, 'a') as f:
                    f.write(session_header)
                    f.write(column_header)
                
                # Preparar el archivo de tiempo real con sus cabeceras
                rt_path = self.session_manager.get_realtime_log_path(data_type)
                with open(rt_path, 'w') as f:
                    f.write(session_header)
                    f.write(column_header)
                    f.write("Esperando datos...\n")
                    
            except IOError as e:
                log.error(f"No se pudo escribir la cabecera para '{data_type}': {e}")
        log.info("Escritura de cabeceras completada.")


    def start(self):
        """Inicia todos los hilos de trabajo y el procesador de datos."""
        log.info("Iniciando todos los servicios del controlador...")
        
        self._write_session_headers()
        
        self.processor_thread.start()
        for worker in self.workers:
            worker.start()
        log.info("Todos los servicios del controlador han sido iniciados.")
        
    def shutdown(self):
        """Detiene de forma ordenada todos los hilos."""
        if self.shutdown_event.is_set():
            return
            
        log.info("Iniciando secuencia de apagado...")
        self.shutdown_event.set()

        # Esperar a que los hilos de trabajo terminen
        for worker in self.workers:
            if isinstance(worker, (PowerMonitor, AlarmMonitor, RebootMonitor)):
                continue
            worker.join(timeout=5.0)
            if worker.is_alive():
                log.warning(f"El trabajador {worker.name} no se detuvo a tiempo.")
        
        # Esperar al hilo del procesador
        self.processor_thread.join(timeout=5.0)
        if self.processor_thread.is_alive():
            log.warning("El hilo procesador no se detuvo a tiempo.")
            
        log.info("Secuencia de apagado completada.")

    def is_shutting_down(self) -> bool:
        return self.shutdown_event.is_set()

    def _format_log_line(self, data_packet: dict) -> str:
        """Formatea una línea de log según el tipo de dato."""
        data_type = data_packet['type']
        data = data_packet['data']
        ts_obj = datetime.fromisoformat(data_packet['timestamp'])

        if data_type == "estabilometro":
            # Asegura el orden correcto de las columnas usando las claves del acquirer
            ordered_values = [str(data.get(key, 'N/A')) for key in IMUAcquirer.DATA_KEYS]
            return ";".join(ordered_values) + ";\n"
        
        elif data_type == "gps":
            # Formato: HoraRaspberry,Fecha,Hora(GPS),Latitud,Longitud,Altitud,HDOP,Fix,NumSats,Velocidad(km/h)
            if data.get("status") == "Valid":
                return (
                    f"{ts_obj.strftime('%H:%M:%S')},"
                    f"{data.get('gps_date', 'N/A')},"
                    f"{data.get('gps_time', 'N/A')},"
                    f"{data.get('latitude', 'N/A')},"
                    f"{data.get('longitude', 'N/A')},"
                    f"{data.get('altitude_m', 'N/A')},"
                    f"{data.get('hdop', 'N/A')},"
                    f"{data.get('fix_quality', 'N/A')},"
                    f"{data.get('num_sats', 'N/A')},"
                    f"{data.get('speed_kmph', 'N/A')}\n"
                )
            else:
                 # Si no hay fix, se loguea una línea con N/A
                 return f"{ts_obj.strftime('%H:%M:%S')},N/A,N/A,N/A,N/A,N/A,N/A,0,N/A,N/A\n"

        elif data_type == "rotativo":
            # Formato: Fecha-Hora;Estado
            ts_str = ts_obj.strftime('%d/%m/%Y-%H:%M:%S')
            return f"{ts_str};{data.get('status', 0)}\n"
            
        elif data_type == "can":
            # Formato: Fecha-Hora   InterfazCAN   PGN   [Bytes]   Datos
            ts_str = ts_obj.strftime('%d/%m/%Y-%H:%M:%S')
            raw_data_hex = data.get('raw_data', '')
            data_bytes = bytes.fromhex(raw_data_hex)
            data_len = len(data_bytes)
            # Añadir espacios entre bytes
            data_str_spaced = ' '.join(f'{b:02X}' for b in data_bytes)
            
            return (
                f"{ts_str}   "
                f"{data.get('interface', 'N/A')}  "
                f"{data.get('arbitration_id_hex', 'N/A'):>8}   " # Alineado a 8 caracteres
                f"[{data_len}]  {data_str_spaced}\n"
            )
            
        return f"{data_packet['timestamp']};{data}\n" # Fallback

    def _process_data_queue(self):
        """
        Bucle que consume la cola, formatea los datos según el nuevo
        estándar y los escribe en los archivos de log.
        """
        log.info("Procesador de datos iniciado.")
        while not self.shutdown_event.is_set():
            try:
                data_packet = self.data_queue.get(timeout=1)
                
                with self.data_lock:
                    self.latest_data[data_packet['type']] = data_packet
                
                # Formatear la línea de log
                log_line = self._format_log_line(data_packet)

                # Escribir en el archivo de log diario
                log_file_path = self.session_manager.get_log_path(data_packet['type'])
                try:
                    with open(log_file_path, 'a') as f:
                        f.write(log_line)
                except Exception as e:
                    log.error(f"No se pudo escribir en el log para {data_packet['type']}: {e}")

                # Actualizar el archivo de tiempo real
                self._update_realtime_file(data_packet['type'], log_line)

            except Empty:
                continue
            except Exception as e:
                log.error(f"Error inesperado en el procesador de datos: {e}", exc_info=True)

        log.info("Procesador de datos detenido.")
    
    def _update_realtime_file(self, data_type: str, formatted_log_line: str):
        """
        Actualiza el archivo _RealTime.txt, reescribiéndolo con las cabeceras
        y la última línea de datos.
        """
        try:
            rt_path = self.session_manager.get_realtime_log_path(data_type)
            session_header = self.session_manager.get_session_header(data_type)
            column_header = self.session_manager.get_column_header(data_type)
            
            with open(rt_path, 'w') as f:
                f.write(session_header)
                f.write(column_header)
                f.write(formatted_log_line)
        except Exception as e:
            log.error(f"No se pudo actualizar el archivo RealTime para {data_type}: {e}")


    def get_latest_data(self) -> dict:
        """Devuelve una copia del último estado de los datos de forma segura."""
        with self.data_lock:
            return self.latest_data.copy()

    def get_service_status(self) -> dict:
        """Devuelve el estado de cada trabajador (hilo)."""
        status = {}
        for worker in self.workers:
            status[worker.name] = "Running" if worker.is_alive() else "Stopped"
        status[self.processor_thread.name] = "Running" if self.processor_thread.is_alive() else "Stopped"
        return status