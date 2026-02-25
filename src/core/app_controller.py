import json
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
from .connectivity_monitor import ConnectivityMonitor
from src.utils.update_manager import UpdateManager

from src.data_acquirers.simulated_acquirers import (
    SimulatedCANAcquirer, SimulatedGPSAcquirer,
    SimulatedIMUAcquirer, SimulatedGPIOAcquirer
)

from src.utils.network import check_internet_connection

log = logging.getLogger(__name__)

class AppController:
    def __init__(self, config: dict, simulate: bool = False):
        self.config = config
        self.simulate = simulate
        self.data_queue = Queue()
        self.shutdown_event = threading.Event()
        self.workers = []
        self.active_data_types = []
        self.latest_data = {}
        self.data_lock = threading.Lock()
        self.log_line_counters = {} # Contador de líneas para los logs
        self.self_test_passed = False
        
        # Sistema de monitoreo externo
        self.monitors = []
        
        # Tracking de conectividad para el sistema de rollback
        self.had_internet = False
        self.session_health_file = config.get('paths', {}).get(
            'session_health_file',
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'session_health.json')
        )
        
        self.session_manager = SessionManager(config)
        self.update_manager = UpdateManager(config)

        self.ftp_transmitter = None

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
            if self.simulate:
                self.workers.append(SimulatedCANAcquirer(self.config, self.data_queue, self.shutdown_event))
            else:
                self.workers.append(CANAcquirer(self.config, self.data_queue, self.shutdown_event))
            self.active_data_types.append('can')
        
        if sources_config.get('gps', {}).get('enabled', False):
            if self.simulate:
                self.workers.append(SimulatedGPSAcquirer(self.config, self.data_queue, self.shutdown_event))
            else:
                self.workers.append(GPSAcquirer(self.config, self.data_queue, self.shutdown_event))
            self.active_data_types.append('gps')
            
        if sources_config.get('estabilometro', {}).get('enabled', False):
            if self.simulate:
                self.workers.append(SimulatedIMUAcquirer(self.config, self.data_queue, self.shutdown_event))
            else:
                self.workers.append(IMUAcquirer(self.config, self.data_queue, self.shutdown_event))
            self.active_data_types.append('estabilometro')
            
        if sources_config.get('gpio_rotativo', {}).get('enabled', False):
            if self.simulate:
                self.workers.append(SimulatedGPIOAcquirer(self.config, self.data_queue, self.shutdown_event))
            else:
                self.workers.append(GPIOAcquirer(self.config, self.data_queue, self.shutdown_event))
            self.active_data_types.append('rotativo')

        if self.config.get('ftp', {}).get('enabled', False):
            log.info("FTP Transmitter está habilitado. Inicializando...")
            self.ftp_transmitter = FTPTransmitter(self.config, self.shutdown_event, self.session_manager, app_controller=self)
            self.workers.append(self.ftp_transmitter)
        
        if self.config.get('system', {}).get('power_monitor', {}).get('enabled', False):
            log.info("Power Monitor está habilitado. Inicializando...")
            self.workers.append(PowerMonitor(self.config, self))

        if self.config.get('system', {}).get('alarm_monitor', {}).get('enabled', False):
            log.info("Alarm Monitor está habilitado. Inicializando...")
            self.workers.append(AlarmMonitor(self.config, self))

        if self.config.get('system', {}).get('reboot_monitor', {}).get('enabled', False):
            log.info("Reboot Monitor está habilitado. Inicializando...")
            self.workers.append(RebootMonitor(self.config, self))

        # El ConnectivityMonitor siempre debe estar activo si queremos el log de red
        log.info("Inicializando Connectivity Monitor...")
        self.workers.append(ConnectivityMonitor(self.config, self))
            
        log.info(f"{len(self.workers)} trabajadores inicializados.")
        log.info(f"Tipos de datos activos: {self.active_data_types}")

        if not self.simulate:
            self._setup_gpio_pins()
        else:
            log.info("Modo SIMULACIÓN: Saltando configuración física de GPIO.")
        
        # Crear los directorios de datos necesarios (ej. /datos/CAN, /datos/GPS)
        self.session_manager.ensure_data_directories(self.active_data_types)


    def _setup_gpio_pins(self):
        """
        Configura todos los pines GPIO de los monitores y acquirers de forma centralizada
        para evitar condiciones de carrera.
        """
        log.info("Configurando pines GPIO de forma centralizada...")
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)

            for worker in self.workers:
                if isinstance(worker, (PowerMonitor, AlarmMonitor)):
                    if worker.pin is not None:
                        log.info(f"Configurando pin {worker.pin} para {worker.name} como ENTRADA con pull_up_down={worker.pull_up_down}")
                        GPIO.setup(worker.pin, GPIO.IN, pull_up_down=worker.pull_up_down)
                
                elif isinstance(worker, RebootMonitor):
                    if worker.pin is not None:
                        log.info(f"Configurando pin {worker.pin} para {worker.name} como SALIDA.")
                        GPIO.setup(worker.pin, GPIO.OUT)

                elif isinstance(worker, GPIOAcquirer):
                    if worker.pin is not None:
                        log.info(f"Configurando pin {worker.pin} para {worker.name} como ENTRADA con pull_down.")
                        GPIO.setup(worker.pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            
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


    def set_internet_detected(self):
        """Marca que se ha detectado conexión a internet en esta sesión."""
        if not self.had_internet:
            self.had_internet = True
            log.info("Conectividad a internet detectada para esta sesión.")

    def register_monitor(self, monitor_queue: Queue):
        """Registra una cola externa para recibir copias de todos los paquetes de datos."""
        self.monitors.append(monitor_queue)
        log.info(f"Monitor externo registrado. Total monitors: {len(self.monitors)}")

    def _check_initial_connectivity(self):
        """Comprobación de conectividad al arranque, independiente de FTP."""
        log.info("Realizando comprobación inicial de conectividad...")
        if check_internet_connection():
            self.set_internet_detected()
        else:
            log.warning("No se detectó conexión a internet en la comprobación inicial.")

    def _write_session_health(self):
        """Escribe el estado de conectividad de la sesión para que setup.py lo lea en el próximo arranque."""
        try:
            version = "unknown"
            version_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.version')
            if os.path.exists(version_file):
                with open(version_file, 'r') as f:
                    version = f.read().strip()
            
            health_data = {
                "had_internet": self.had_internet,
                "version": version,
                "timestamp": datetime.now().isoformat()
            }
            with open(self.session_health_file, 'w') as f:
                json.dump(health_data, f, indent=2)
            log.info(f"Estado de salud de sesión guardado: had_internet={self.had_internet}, version={version}")
        except IOError as e:
            log.error(f"No se pudo escribir session_health.json: {e}")

    def start(self):
        """Inicia todos los hilos de trabajo y el procesador de datos."""
        log.info("Iniciando todos los servicios del controlador...")
        
<<<<<<< Working
        self._check_initial_connectivity()
=======
        # 1. Realizar Self-Test antes de arrancar todo
        if not self._perform_self_test():
            log.critical("¡FALLO EN EL SELF-TEST! El sistema podría no ser estable.")
            # Dependiendo de la severidad, podríamos continuar o abortar.
            # Aquí continuamos pero con la flag en False, para que RebootMonitor lo sepa.
        else:
            log.info("Self-Test completado con ÉXITO.")
            self.self_test_passed = True

>>>>>>> main
        self._write_session_headers()
        
        self.processor_thread.start()
        for worker in self.workers:
            worker.start()
        log.info("Todos los servicios del controlador han sido iniciados.")

    def _perform_self_test(self) -> bool:
        """
        Realiza comprobaciones críticas de arranque.
        """
        log.info("Ejecutando secuencia de Self-Test...")
        try:
            # 1. Verificar directorios de datos
            self.session_manager.ensure_data_directories(self.active_data_types)
            
            # 2. Verificar que los trabajadores críticos están instanciados
            # (ej. si el RebootMonitor es obligatorio y no está, fallar)
            # Por ahora, consideramos éxito si no hay excepciones críticas en el setup previo.
            
            return True
        except Exception as e:
            log.error(f"Error durante el Self-Test: {e}")
            return False
        
    def shutdown(self):
        """Detiene de forma ordenada todos los hilos."""
        if self.shutdown_event.is_set():
            return
            
        log.info("Iniciando secuencia de apagado...")
        self.shutdown_event.set()

<<<<<<< Working
        # Guardar el estado de salud de la sesión ANTES de la subida final
        self._write_session_health()
=======
        # Evaluar la salud de la sesión antes de cerrar todo
        self._evaluate_session_health()
>>>>>>> main

        if self.ftp_transmitter:
            log.info("Iniciando subida final de logs de sistema...")
            # Esta es una llamada síncrona/bloqueante para asegurar que se completa
            self.ftp_transmitter.upload_final_logs()

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
        """Formatea una línea de log según el tipo de dato, de forma unificada."""
        data_type = data_packet['type']
        data = data_packet['data']
        ts_obj = datetime.fromisoformat(data_packet['timestamp'])
        ts_str = ts_obj.strftime('%d/%m/%Y %H:%M:%S')

        if data_type == "estabilometro":
            # Asegura el orden correcto de las columnas usando las claves del acquirer
            ordered_values = [str(data.get(key, '')) for key in IMUAcquirer.DATA_KEYS]
            return ";".join(ordered_values) + ";\n"
        
        elif data_type == "gps":
            if data.get("status") == "Valid":
                return (
                    f"{ts_str};"
                    f"{data.get('gps_date', '')};"
                    f"{data.get('gps_time', '')};"
                    f"{data.get('latitude', '')};"
                    f"{data.get('longitude', '')};"
                    f"{data.get('altitude_m', '')};"
                    f"{data.get('hdop', '')};"
                    f"{data.get('fix_quality', '')};"
                    f"{data.get('num_sats', '')};"
                    f"{data.get('speed_kmph', '')};\n"
                )
            else:
                 # Si no hay fix, se loguea una línea con N/A y '0' en fix
                 return f"{ts_str};;;;;;;;;;\n"

        elif data_type == "rotativo":
            return f"{ts_str};{data.get('status', 0)};\n"
            
        elif data_type == "can":
            raw_data_hex = data.get('raw_data', '')
            data_bytes = bytes.fromhex(raw_data_hex)
            data_len_str = f"[{len(data_bytes)}]"
            data_str_spaced = ' '.join(f'{b:02X}' for b in data_bytes)
            
            return (
                f"{ts_str};"
                f"{data.get('interface', '')};"
                f"{data.get('arbitration_id_hex', '')};"
                f"{data_len_str};"
                f"{data_str_spaced};\n"
            )
            
        return f"{ts_str};{data};\n" # Fallback

    def _process_data_queue(self):
        """
        Bucle que consume la cola, formatea los datos según el nuevo
        estándar y los escribe en los archivos de log.
        """
        log.info("Procesador de datos iniciado.")
        while not self.shutdown_event.is_set():
            try:
                data_packet = self.data_queue.get(timeout=1)
                data_type = data_packet['type']
                
                with self.data_lock:
                    self.latest_data[data_type] = data_packet
                
                # Formatear la línea de log
                log_line = self._format_log_line(data_packet)

                # Escribir en el archivo de log diario
                log_file_path = self.session_manager.get_log_path(data_type)
                try:
                    with open(log_file_path, 'a') as f:
                        f.write(log_line)
                        
                        if data_type == "estabilometro":
                            # Inicializa el contador si no existe para este tipo de dato
                            self.log_line_counters.setdefault("estabilometro", 0)
                            
                            # Incrementa el contador
                            self.log_line_counters["estabilometro"] += 1
                            
                            # Si el contador llega a 10, escribe la marca de tiempo y lo resetea
                            if self.log_line_counters["estabilometro"] >= 10:
                                timestamp_line = f"{datetime.now().strftime('%H:%M:%S')}\n"
                                f.write(timestamp_line)
                                self.log_line_counters["estabilometro"] = 0

                except Exception as e:
                    log.error(f"No se pudo escribir en el log para {data_type}: {e}")

                # Actualizar el archivo de tiempo real
                self._update_realtime_file(data_packet['type'], log_line)

                # Notificar a los monitores registrados
                for monitor_queue in self.monitors:
                    try:
                        monitor_queue.put_nowait(data_packet)
                    except Exception:
                        pass # Evitar que un monitor lento bloquee el procesamiento

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

    def _evaluate_session_health(self):
        """
        Evalúa si la sesión actual ha sido exitosa (dispositivos ok, red ok).
        Si falla, informa al UpdateManager para forzar un rollback.
        """
        log.info("Evaluando salud de la sesión actual...")
        errors = []

        # 1. ¿Pasó el self-test inicial?
        if not self.self_test_passed:
            errors.append("Fallo en Self-Test inicial")

        # 2. ¿Hubo conectividad en algún momento?
        connectivity_worker = next((w for w in self.workers if hasattr(w, 'connectivity_seen') and w.connectivity_seen), None)
        if not connectivity_worker:
            errors.append("Sin conectividad a Internet")

        # 3. ¿Los dispositivos críticos reportaron datos válidos?
        for worker in self.workers:
            if hasattr(worker, 'data_seen') and not worker.data_seen:
                # Solo consideramos fallos si el nombre contiene "Acquirer" (CAN, GPS, etc.)
                if "Acquirer" in worker.name:
                    errors.append(f"El dispositivo {worker.name} no reportó datos válidos")
            if hasattr(worker, 'fatal_error') and worker.fatal_error:
                errors.append(f"Error fatal en dispositivo {worker.name}")

        # 4. Verificar integridad básica de logs
        for data_type in self.active_data_types:
            try:
                log_path = self.session_manager.get_log_path(data_type)
                if not os.path.exists(log_path) or os.path.getsize(log_path) == 0:
                    errors.append(f"Archivo de log vacío o inexistente para {data_type}")
            except Exception:
                errors.append(f"Error accediendo a log de {data_type}")

        if errors:
            reason = "; ".join(errors)
            log.error(f"Sesión evaluada como NO SALUDABLE: {reason}")
            self.update_manager.mark_as_unstable(reason)
        else:
            log.info("Sesión evaluada como SALUDABLE.")
            # Si todo fue bien, nos aseguramos de que esté marcado como estable
            self.update_manager.mark_as_stable()