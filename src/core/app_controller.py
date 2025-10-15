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
from src.data_acquirers.imu_acquirer import IMUAcquirer
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
        Escribe la cabecera de la nueva sesión en cada archivo de log diario
        y prepara los archivos RealTime.
        """
        log.info("Escribiendo cabeceras de sesión en archivos de log diarios...")
        for data_type in self.active_data_types:
            try:
                # Escribir cabecera en el archivo de log principal
                log_path = self.session_manager.get_log_path(data_type)
                session_header = self.session_manager.get_session_header(data_type)
                with open(log_path, 'a') as f:
                    f.write(session_header)
                
                # Preparar el archivo de tiempo real
                rt_path = self.session_manager.get_realtime_log_path(data_type)
                with open(rt_path, 'w') as f:
                    f.write(f"Session {self.session_manager.current_session_id} started. Waiting for data...\n")
                    
            except IOError as e:
                log.error(f"No se pudo escribir la cabecera para '{data_type}': {e}")
        log.info("Escritura de cabeceras de sesión completada.")

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

    def _process_data_queue(self):
        """

        Bucle principal que consume la cola de datos, los registra y actualiza el estado.
        """
        log.info("Procesador de datos iniciado.")
        while not self.shutdown_event.is_set():
            try:
                data_packet = self.data_queue.get(timeout=1)
                
                data_type = data_packet['type']
                timestamp = data_packet['timestamp']
                data_content = data_packet['data']

                with self.data_lock:
                    self.latest_data[data_type] = data_packet
                
                log_file_path = self.session_manager.get_log_path(data_type)
                try:
                    # El modo 'a' (append) es clave para el log diario
                    with open(log_file_path, 'a') as f:
                        log_line = f"{timestamp};{data_content}\n"
                        f.write(log_line)
                except Exception as e:
                    log.error(f"No se pudo escribir en el log para {data_type}: {e}")

                self._update_realtime_file(data_type, data_packet)

            except Empty:
                continue
            except Exception as e:
                log.error(f"Error inesperado en el procesador de datos: {e}")

        log.info("Procesador de datos detenido.")
    
    def _update_realtime_file(self, data_type: str, data_packet: dict):
        """Actualiza el archivo _RealTime.txt para un tipo de dato específico."""
        try:
            rt_path = self.session_manager.get_realtime_log_path(data_type)
            with open(rt_path, 'w') as f:
                f.write(f"Timestamp: {data_packet['timestamp']}\n")
                f.write(f"Data: {data_packet['data']}\n")
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