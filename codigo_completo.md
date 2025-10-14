## Archivo: `.\main.py`

```python
import argparse
import logging
import signal
import sys
import time
from queue import Queue

from src.core.app_controller import AppController
from src.utils.config_loader import ConfigLoader
from src.utils.unified_logger import setup_logging

# Para la GUI, solo se importa si es necesario para evitar dependencias
# innecesarias en el modo de servicio.
try:
    from src.gui.main_window import MainWindow
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


def main():
    """Punto de entrada principal de la aplicación fire-truck-app."""
    parser = argparse.ArgumentParser(description="Sistema de Monitorización de Vehículos (fire-truck-app)")
    parser.add_argument("--config", default="config/config.yaml", help="Ruta al archivo de configuración.")
    parser.add_argument("--gui", action="store_true", help="Lanzar la aplicación con la interfaz gráfica.")
    args = parser.parse_args()

    # 1. Cargar configuración
    try:
        config_loader = ConfigLoader(args.config)
        config = config_loader.get_config()
    except FileNotFoundError:
        print(f"Error: El archivo de configuración '{args.config}' no fue encontrado.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error al cargar la configuración: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. Configurar logging centralizado
    setup_logging(config)
    log = logging.getLogger(__name__)
    log.info("Iniciando aplicación fire-truck-app...")

    # 3. Crear el controlador principal de la aplicación
    app_controller = AppController(config)

    # 4. Configurar manejo de señales para un apagado ordenado
    def signal_handler(sig, frame):
        log.warning(f"Señal {signal.Signals(sig).name} recibida. Iniciando apagado...")
        app_controller.shutdown()
        # Si se ejecuta la GUI, también se debe cerrar.
        if 'main_window' in locals() and main_window.is_running():
            main_window.close()

    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler) # systemctl stop

    # 5. Iniciar los servicios del backend
    app_controller.start()

    # 6. Decidir si arrancar en modo GUI o headless
    use_gui = args.gui or config.get('system', {}).get('start_with_gui', False)

    if use_gui:
        if not GUI_AVAILABLE:
            log.error("Se solicitó la GUI, pero los componentes (ej. tkinter) no están disponibles. Saliendo.")
            app_controller.shutdown()
            sys.exit(1)
        
        log.info("Iniciando en modo GUI...")
        main_window = MainWindow(app_controller)
        main_window.run() # Esto bloquea hasta que la ventana se cierra
        log.info("La ventana de la GUI se ha cerrado.")
        # Asegurarse de que el backend se apaga si la GUI se cierra primero
        if not app_controller.is_shutting_down():
            app_controller.shutdown()
            
    else:
        log.info("Iniciando en modo headless (servicio).")
        # Mantener el hilo principal vivo para que los hilos de trabajo puedan operar.
        # El bucle se romperá cuando el evento de apagado se active.
        while not app_controller.is_shutting_down():
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                # Esto es redundante si signal_handler funciona, pero es una buena práctica
                break

    log.info("La aplicación fire-truck-app se ha detenido limpiamente.")
    # Limpieza final de GPIO si se usó
    try:
        import RPi.GPIO as GPIO
        GPIO.cleanup()
        log.info("Limpieza de GPIO completada.")
    except (RuntimeError, ImportError):
        # No hacer nada si RPi.GPIO no está disponible o ya fue limpiado
        pass
    sys.exit(0)

if __name__ == "__main__":
    main()

```

## Archivo: `.\requirements.txt`

```text
PyYAML
python-can
pyserial
RPi.GPIO
smbus2
```

## Archivo: `.\config\config.yaml`

```text
# -----------------------------------------------------------------
# Configuración Unificada para la Aplicación fire-truck-app
# -----------------------------------------------------------------
system:
  device_user: "cosigein"
  device_number: "001"
  log_level: "DEBUG"       # DEBUG, INFO, WARNING, ERROR, CRITICAL
  start_with_gui: false 

  power_monitor:
    enabled: true
    # Pin GPIO (en modo BCM) conectado a la alimentación del vehículo.
    # El pin 36 (BOARD) es el 16 (BCM).
    pin: 16 
    # Cómo está conectado el pin. PULL_UP significa que el pin está en ALTO (HIGH)
    # cuando el motor está encendido, y va a BAJO (LOW) cuando se apaga.
    pull_up_down: "PUD_UP" 
  
  alarm_monitor:
    enabled: true
    # Pin GPIO (BCM) para la alarma de la fuente de alimentación. Pin 37 (BOARD) -> 26 (BCM).
    pin: 26
    # La alarma se activa (TRUE) cuando el pin pasa a estado HIGH.
    pull_up_down: "PUD_DOWN"
  
  reboot_monitor:
    enabled: true
    # Pin GPIO (BCM) para la señal de reinicio de la fuente. Pin 32 (BOARD) -> 12 (BCM).
    pin: 12
    # La señal de reinicio se activa (FALSE) cuando el pin pasa a estado LOW.
    # Esto implica que el pin debe estar mantenido en HIGH por un circuito externo.
    pull_up_down: "PUD_UP"

paths:
  data_root: "/home/cosigein/datos"
  app_logs: "/home/cosigein/logs"
  # Un único archivo para gestionar todos los contadores de sesión.
  session_db: "/home/cosigein/fire-truck-app_session_data.json"

ftp:
  enabled: true
  host: "***REMOVED***"
  port: 21
  user: "doback"
  pass: "***REMOVED***"
  # Intervalo para buscar y subir archivos de log completos.
  upload_interval_sec: 300
  # Intervalo para subir los archivos de estado en tiempo real.
  realtime_interval_sec: 30
  # Los archivos no se subirán si se han modificado en los últimos X segundos.
  upload_safety_margin_sec: 120

data_sources:
  can:
    enabled: true
    interface: "can0"
    bitrate: 500000
    inactivity_timeout_sec: 10
  
    # ID de la petición de diagnóstico estándar (broadcast)
    request_id: 0x7DF 
    # Intervalo en segundos entre cada consulta para no saturar el bus.
    query_interval_sec: 0.2
    # Tiempo máximo en segundos para esperar una respuesta a una consulta.
    response_timeout_sec: 0.5

    # Lista de PGNs (Parameter Group Numbers) de J1939 a escuchar.
    # La aplicación filtrará y parseará solo los mensajes con estos PGNs.
    pgn_to_listen:
      - name: "Engine Speed"
        pgn: 61444  # 0xF004
        unit: "rpm"
      
      - name: "Wheel-Based Vehicle Speed"
        pgn: 65265  # 0xFEF1
        unit: "km/h"
        
      - name: "Engine Coolant Temperature"
        pgn: 65262  # 0xFEEE
        unit: "°C"
        
      - name: "Accelerator Pedal Position 1"
        pgn: 61443 # 0xF003
        unit: "%"

  gps:
    enabled: true
    i2c_bus: 1
    i2c_addr: 0x42
    inactivity_timeout_sec: 600

  estabilometro:
    enabled: true
    serial_port: "/dev/serial0"
    baud_rate: 115200
    session_timeout_sec: 15

  gpio_rotativo:
    enabled: true
    pin: 22
    log_period_sec: 1
```

## Archivo: `.\scripts\check_and_install_update.sh`

```text
#!/bin/bash

APP_DIR="/home/cosigein/fire-truck-app"
LOG_FILE="/home/cosigein/logs/updater.log"
APP_SERVICE="app.service"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - [UPDATER] - $1" | tee -a $LOG_FILE
}

log "--- Iniciando script de comprobación e instalación ---"

# Navegar al directorio de la aplicación
cd $APP_DIR || { log "ERROR: No se pudo acceder a $APP_DIR"; exit 1; }

# Comprobar conectividad
if ! ping -c 1 -W 5 8.8.8.8 &> /dev/null; then
    log "No hay conexión a internet. Saliendo."
    exit 0
fi
log "Conexión a internet detectada."

# Comprobar estado de Git como el usuario correcto
log "Ejecutando comprobaciones de Git como usuario 'cosigein'..."
GIT_OUTPUT=$(sudo -u cosigein git remote update 2>&1 && sudo -u cosigein git status -uno 2>&1)

if [[ $GIT_OUTPUT == *"Your branch is behind"* ]]; then
    log "¡Nueva versión detectada! Iniciando proceso de actualización."

    log "Deteniendo el servicio $APP_SERVICE..."
    systemctl stop $APP_SERVICE

    log "Ejecutando 'git pull' como 'cosigein'..."
    if ! sudo -u cosigein git pull; then
        log "ERROR: 'git pull' falló. Se reintentará en el próximo arranque."
        systemctl start $APP_SERVICE
        exit 1
    fi

    log "Instalando/actualizando dependencias..."
    /home/cosigein/fire-truck-app/.venv/bin/pip install -r requirements.txt

    log "¡Actualización completada! Reiniciando el sistema para aplicar los cambios."
    reboot

elif [[ $GIT_OUTPUT == *"Your branch is up to date"* ]]; then
    log "La aplicación ya está actualizada."
    exit 0
else
    log "Estado de Git no reconocido o error. Saliendo."
    log "Salida de Git: $GIT_OUTPUT"
    exit 1
fi
```

## Archivo: `.\services\app.service`

```ini
[Unit]
Description=Servicio de Monitorización de Vehículos (fire-truck-app)
After=network.target

[Service]
User=cosigein
Group=cosigein
WorkingDirectory=/home/cosigein/fire-truck-app
# Inicia el servicio en modo "headless". Para la GUI, ejecutar manualmente.
ExecStart=/home/cosigein/fire-truck-app/.venv/bin/python3 /home/cosigein/fire-truck-app/main.py
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

## Archivo: `.\services\updater.service`

```ini
[Unit]
Description=Comprueba e instala actualizaciones para fire-truck-app
After=network-online.target
Wants=network-online.target
# Asegúrate de que tu app principal no arranque hasta que el updater termine
Before=app.service

[Service]
Type=oneshot
TimeoutStartSec=5min
ExecStart=/home/cosigein/fire-truck-app/scripts/check_and_install_update.sh

[Install]
WantedBy=multi-user.target
```

## Archivo: `.\src\__init__.py`

```python

```

## Archivo: `.\src\core\alarm_monitor.py`

```python
import logging
import subprocess
import threading
import time
import RPi.GPIO as GPIO
import os

log = logging.getLogger(__name__)

class AlarmMonitor(threading.Thread):
    """
    Un hilo que monitoriza un pin GPIO para detectar una señal de ALARMA
    de la fuente de alimentación (ej. sobretemperatura). Cuando se detecta,
    inicia la secuencia de apagado controlado de la aplicación y el sistema.
    """

    def __init__(self, config: dict, app_controller):
        super().__init__(name="AlarmMonitor")
        self.app_controller = app_controller
        self.shutdown_event = app_controller.shutdown_event
        
        alarm_config = config.get('system', {}).get('alarm_monitor', {})
        self.pin = alarm_config.get('pin')
        
        pull_config_str = alarm_config.get('pull_up_down', 'PUD_DOWN').upper()

        try:
            self.pull_up_down = getattr(GPIO, pull_config_str)
        except AttributeError:
            log.critical(f"Valor de 'pull_up_down' inválido en config de AlarmMonitor: '{pull_config_str}'.")
            self.pin = None
            return

        # La lógica de disparo ahora se basa en la configuración
        if self.pull_up_down == GPIO.PUD_UP:
            self.trigger_state = GPIO.LOW
        else: # GPIO.PUD_DOWN
            self.trigger_state = GPIO.HIGH

    def run(self):
        if not self._setup():
            log.error("AlarmMonitor no pudo inicializarse. El hilo terminará.")
            return

        log.info(f"AlarmMonitor iniciado. Vigilando pin {self.pin} para estado '{'HIGH' if self.trigger_state == GPIO.HIGH else 'LOW'}'...")

        while not self.shutdown_event.is_set():
            if GPIO.input(self.pin) == self.trigger_state:
                log.critical("¡ALARMA DE FUENTE DE ALIMENTACIÓN DETECTADA! Iniciando apagado de emergencia.")
                
                # 1. Indicar a la aplicación que se apague (esto ejecutará la subida final)
                self.app_controller.shutdown()
                
                # 2. Esperar a que la aplicación termine su secuencia de apagado.
                log.info("AlarmMonitor esperando a que los servicios de la app finalicen...")
                self.app_controller.processor_thread.join(timeout=60.0)
                
                if self.app_controller.processor_thread.is_alive():
                    log.error("El procesador de la app no terminó a tiempo. Forzando apagado del sistema.")
                else:
                    log.info("Los servicios de la app han finalizado correctamente.")

                # 3. Apagar el sistema operativo
                self._shutdown_system()
                break

            self.shutdown_event.wait(1.0) # Comprobar el estado cada segundo

        self._cleanup()
        log.info("AlarmMonitor detenido.")

    def _setup(self) -> bool:
        if self.pin is None:
            log.critical("No se ha especificado un pin GPIO para AlarmMonitor en la configuración.")
            return False
        return True

    def _cleanup(self):
        log.debug("AlarmMonitor: limpieza finalizada.")

    # Modifica el método de apagado/reinicio del sistema
    def _shutdown_system(self):
        log.critical("APAGANDO EL SISTEMA OPERATIVO AHORA.")
        try:
            # Descomenta para producción
            subprocess.call(['sudo', 'shutdown', 'now'])
            
            # Línea para pruebas
            # print("SIMULACIÓN: sudo shutdown now")
            log.info("Comando de apagado del sistema ejecutado.")
        except Exception as e:
            log.critical(f"Fallo al ejecutar el comando de apagado del sistema: {e}")
```

## Archivo: `.\src\core\app_controller.py`

```python
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
                        log.info(f"Configurando pin {worker.pin} para {worker.name} con pull_up_down={worker.pull_up_down}")
                        GPIO.setup(worker.pin, GPIO.IN, pull_up_down=worker.pull_up_down)
            
            log.info("Configuración centralizada de GPIO completada.")
        except Exception as e:
            log.critical(f"FALLO CRÍTICO durante la configuración centralizada de GPIO: {e}")
            # Opcional: podrías querer detener la app aquí si GPIO es esencial
            # self.shutdown_event.set() 

    def _precreate_log_files(self):
        """
        Crea archivos de log vacíos para todas las fuentes de datos activas
        al inicio de la sesión para asegurar su existencia.
        """
        log.info("Pre-creando archivos de log para la sesión actual...")
        for data_type in self.active_data_types:
            try:
                log_path = self.session_manager.get_log_path(data_type)
                with open(log_path, 'a'):
                    os.utime(log_path, None)
                
                rt_path = self.session_manager.get_realtime_log_path(data_type)
                with open(rt_path, 'w') as f:
                    f.write("Session started. Waiting for data...\n")
                    
            except IOError as e:
                log.error(f"No se pudo pre-crear el archivo de log para '{data_type}': {e}")
        log.info("Pre-creación de archivos completada.")

    def start(self):
        """Inicia todos los hilos de trabajo y el procesador de datos."""
        log.info("Iniciando todos los servicios del controlador...")
        
        self._precreate_log_files()
        
        self.processor_thread.start()
        for worker in self.workers:
            worker.start()
        log.info("Todos los servicios del controlador han sido iniciados.")
        
    def shutdown(self):
        """Detiene de forma ordenada todos los hilos."""
        if self.shutdown_event.is_set():
            return
            
        log.info("Iniciando secuencia de apagado...")

        # --- CAMBIO: Se ha eliminado la llamada a _perform_final_upload() ---
        # La nueva lógica del FTPTransmitter maneja las subidas de forma más robusta.

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

    # --- CAMBIO: El método _perform_final_upload() ha sido eliminado por completo ---

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
```

## Archivo: `.\src\core\power_monitor.py`

```python
import logging
import subprocess
import threading
import time
import RPi.GPIO as GPIO 
import os

log = logging.getLogger(__name__)

class PowerMonitor(threading.Thread):
    """
    Un hilo que monitoriza un pin GPIO para detectar una señal de apagado del vehículo.
    Cuando se detecta, inicia la secuencia de apagado controlado de la aplicación
    y, finalmente, apaga el sistema operativo.
    """

    def __init__(self, config: dict, app_controller):
        super().__init__(name="PowerMonitor")
        self.app_controller = app_controller
        self.shutdown_event = app_controller.shutdown_event
        
        power_config = config.get('system', {}).get('power_monitor', {})
        self.pin = power_config.get('pin')
        
        pull_config_str = power_config.get('pull_up_down', 'PUD_UP').upper()
        
        try:
            self.pull_up_down = getattr(GPIO, pull_config_str)
        except AttributeError:
            log.critical(f"Valor de 'pull_up_down' inválido en la configuración: '{pull_config_str}'.")
            log.critical("Debe ser 'PUD_UP' o 'PUD_DOWN'.")
            self.pin = None 
            return

        # La lógica de disparo ahora se basa en la configuración
        if self.pull_up_down == GPIO.PUD_UP:
            self.trigger_state = GPIO.LOW  # Con pull-up, el pin está en HIGH y se dispara en LOW.
        else: # GPIO.PUD_DOWN
            self.trigger_state = GPIO.HIGH

    def run(self):
        if not self._setup():
            log.error("PowerMonitor no pudo inicializarse. El hilo terminará.")
            return

        log.info(f"PowerMonitor iniciado. Vigilando pin {self.pin} para estado '{'LOW' if self.trigger_state == GPIO.LOW else 'HIGH'}'...")

        while not self.shutdown_event.is_set():
            if GPIO.input(self.pin) == self.trigger_state:
                log.critical("¡Señal de apagado del vehículo detectada! Iniciando apagado controlado.")
                
                # 1. Indicar a la aplicación que se apague (esto ejecutará la subida final)
                self.app_controller.shutdown()
                
                # 2. Esperar a que la aplicación termine su secuencia de apagado.
                #    Esperamos por el hilo del procesador de datos, que es uno de los últimos en parar.
                log.info("PowerMonitor esperando a que los servicios de la app finalicen...")
                self.app_controller.processor_thread.join(timeout=60.0) # Espera hasta 60s
                
                if self.app_controller.processor_thread.is_alive():
                    log.error("El procesador de la app no terminó a tiempo. Forzando apagado del sistema.")
                else:
                    log.info("Los servicios de la app han finalizado correctamente.")

                # 3. Apagar el sistema operativo
                self._shutdown_system()
                break # Salir del bucle

            self.shutdown_event.wait(1.0) # Comprobar el estado cada segundo

        self._cleanup()
        log.info("PowerMonitor detenido.")

    def _setup(self) -> bool:
        # --- BLOQUE ELIMINADO ---
        # Ya no se necesita la comprobación de GPIO_AVAILABLE
        
        if self.pin is None:
            log.critical("No se ha especificado un pin GPIO para PowerMonitor en la configuración.")
            return False
        return True

    def _cleanup(self):
        log.debug("PowerMonitor: limpieza finalizada.")

    # Modifica el método de apagado/reinicio del sistema
    def _shutdown_system(self):
        log.critical("APAGANDO EL SISTEMA OPERATIVO AHORA.")
        try:
            # Descomenta para producción
            subprocess.call(['sudo', 'shutdown', 'now'])
            
            # Línea para pruebas
            # print("SIMULACIÓN: sudo shutdown now")
            log.info("Comando de apagado del sistema ejecutado.")
        except Exception as e:
            log.critical(f"Fallo al ejecutar el comando de apagado del sistema: {e}")
```

## Archivo: `.\src\core\reboot_monitor.py`

```python
import logging
import subprocess
import threading
import time
import RPi.GPIO as GPIO
import os

log = logging.getLogger(__name__)

class RebootMonitor(threading.Thread):
    """
    Un hilo que monitoriza un pin GPIO para detectar una señal de REINICIO
    de la fuente de alimentación. Cuando se detecta, inicia la secuencia de 
    apagado controlado de la aplicación y luego reinicia el sistema.
    """

    def __init__(self, config: dict, app_controller):
        super().__init__(name="RebootMonitor")
        self.app_controller = app_controller
        self.shutdown_event = app_controller.shutdown_event
        
        reboot_config = config.get('system', {}).get('reboot_monitor', {})
        self.pin = reboot_config.get('pin')
        
        pull_config_str = reboot_config.get('pull_up_down', 'PUD_DOWN').upper()

        try:
            self.pull_up_down = getattr(GPIO, pull_config_str)
        except AttributeError:
            log.critical(f"Valor de 'pull_up_down' inválido en config de RebootMonitor: '{pull_config_str}'.")
            self.pin = None
            return

        # La lógica de disparo se basa en la configuración.
        if self.pull_up_down == GPIO.PUD_UP:
            # Si se usa pull-up interno, el estado normal es HIGH, se activa en LOW.
            self.trigger_state = GPIO.LOW 
        else: # GPIO.PUD_DOWN
            # Si se usa pull-down, el estado normal es LOW. Para que se active en LOW,
            # se necesita un circuito externo que mantenga el pin en HIGH.
            self.trigger_state = GPIO.LOW

    def run(self):
        if not self._setup():
            log.error("RebootMonitor no pudo inicializarse. El hilo terminará.")
            return

        log.info(f"RebootMonitor iniciado. Vigilando pin {self.pin} para estado 'LOW'...")

        while not self.shutdown_event.is_set():
            if GPIO.input(self.pin) == self.trigger_state:
                log.critical("¡Señal de REINICIO del sistema detectada! Iniciando apagado y reinicio.")
                
                # 1. Indicar a la aplicación que se apague (esto ejecutará la subida final)
                self.app_controller.shutdown()
                
                # 2. Esperar a que la aplicación termine su secuencia de apagado.
                log.info("RebootMonitor esperando a que los servicios de la app finalicen...")
                self.app_controller.processor_thread.join(timeout=60.0)
                
                if self.app_controller.processor_thread.is_alive():
                    log.error("El procesador de la app no terminó a tiempo. Forzando reinicio del sistema.")
                else:
                    log.info("Los servicios de la app han finalizado correctamente.")

                # 3. REINICIAR el sistema operativo
                self._reboot_system()
                break

            self.shutdown_event.wait(1.0) # Comprobar el estado cada segundo

        self._cleanup()
        log.info("RebootMonitor detenido.")

    def _setup(self) -> bool:
        if self.pin is None:
            log.critical("No se ha especificado un pin GPIO para RebootMonitor en la configuración.")
            return False
        return True

    def _cleanup(self):
        log.debug("RebootMonitor: limpieza finalizada.")

    # Modifica el método de apagado/reinicio del sistema
    def _reboot_system(self):
        log.critical("REINICIANDO EL SISTEMA OPERATIVO AHORA.")
        try:
            # Descomenta para producción
            subprocess.call(['sudo', 'reboot'])

            # Línea para pruebas
            # print("SIMULACIÓN: sudo reboot")
            log.info("Comando de reinicio del sistema ejecutado.")
        except Exception as e:
            log.critical(f"Fallo al ejecutar el comando de reinicio del sistema: {e}")
```

## Archivo: `.\src\core\session_manager.py`

```python
# Contenido COMPLETO para: ./src/core/session_manager.py

import json
import logging
import os
from datetime import datetime
from threading import Lock

log = logging.getLogger(__name__)

class SessionManager:
    """
    Gestiona la creación de archivos de log, rutas y una sesión global por cada
    arranque de la aplicación.
    """
    def __init__(self, config: dict):
        self.config = config
        paths_config = config.get('paths', {})
        self.data_root = paths_config.get('data_root', '/tmp/hums_data')
        self.db_path = paths_config.get('session_db', '/tmp/hums_session.json')
        self.device_id = config.get('system', {}).get('device_number', '000')
        self.lock = Lock()
        
        now = datetime.now()
        self.today_str = now.strftime('%Y%m%d')
        self.session_time_str = now.strftime('%H-%M-%S')
        
        self.current_session_id = self._initialize_session() # <--- Esta línea necesita el método de abajo
        
        session_folder_name = f"session_{self.current_session_id:03d}_{self.session_time_str}"
        self.session_path = os.path.join(self.data_root, self.today_str, session_folder_name)
        
        try:
            os.makedirs(self.session_path, exist_ok=True)
            log.info(f"Sesión activa: {self.current_session_id}. Directorio de datos: {self.session_path}")
        except OSError as e:
            log.critical(f"No se pudo crear el directorio de la sesión: {self.session_path}. Error: {e}")
            raise

    def _load_session_db(self) -> dict:
        """Carga el estado de la sesión desde el archivo JSON."""
        if not os.path.exists(self.db_path):
            log.warning(f"Archivo de sesión no encontrado en {self.db_path}. Creando uno nuevo.")
            return {"session_counters": {}}
        try:
            with open(self.db_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.error(f"No se pudo cargar o parsear el archivo de sesión. Creando uno nuevo. Error: {e}")
            return {"session_counters": {}}

    def _save_session_db(self, session_data: dict):
        """Guarda el estado actual de la sesión en el archivo JSON."""
        try:
            with open(self.db_path, 'w') as f:
                json.dump(session_data, f, indent=4)
        except IOError as e:
            log.error(f"No se pudo guardar el archivo de sesión en {self.db_path}: {e}")
            
    # --- MÉTODO QUE FALTABA ---
    def _initialize_session(self) -> int:
        """
        Determina el ID de la sesión actual.
        Si es un nuevo día, el contador de sesión se resetea a 1.
        Si es el mismo día, el contador se incrementa.
        Este método se ejecuta una sola vez al inicio de la aplicación.
        """
        with self.lock:
            session_data = self._load_session_db()
            counters = session_data.get("session_counters", {})
            
            last_session_today = counters.get(self.today_str, 0)
            
            new_session_id = last_session_today + 1
            
            counters[self.today_str] = new_session_id
            session_data["session_counters"] = counters
            self._save_session_db(session_data)
            
            return new_session_id
    # --- FIN DEL MÉTODO QUE FALTABA ---

    def get_log_path(self, data_type: str) -> str:
        """
        Obtiene la ruta del archivo de log para un tipo de dato dentro de la sesión actual.
        """
        filename = f"{self.today_str}_{self.device_id}_{data_type.upper()}.log"
        return os.path.join(self.session_path, filename)

    def get_realtime_log_path(self, data_type: str) -> str:
        """Obtiene la ruta para el archivo de estado en tiempo real dentro de la sesión actual."""
        filename = f"{self.today_str}_{self.device_id}_{data_type.upper()}_RealTime.txt"
        return os.path.join(self.session_path, filename)
```

## Archivo: `.\src\core\__init__.py`

```python

```

## Archivo: `.\src\data_acquirers\base_acquirer.py`

```python
import abc
import threading
import logging
from datetime import datetime
from queue import Queue

log = logging.getLogger(__name__)

class BaseAcquirer(threading.Thread, abc.ABC):
    """
    Clase base abstracta para todos los recolectores de datos.
    Gestiona el ciclo de vida del hilo y define la interfaz común.
    """
    def __init__(self, config: dict, data_queue: "Queue", shutdown_event: threading.Event, name: str, config_key: str):
        super().__init__(name=name)
        self.config = config.get('data_sources', {}).get(config_key, {})
        if not self.config:
            log.warning(f"No se encontró la sección de configuración '{config_key}' para el trabajador {name}.")
        
        self.system_config = config
        self.data_queue = data_queue
        self.shutdown_event = shutdown_event
        log.info(f"Inicializando {self.name}...")

    def run(self):
        """
        Método principal del hilo. Llama al método de inicialización específico
        y luego entra en el bucle de adquisición.
        """
        log.info(f"Iniciando hilo para {self.name}.")
        if not self._setup():
            log.error(f"{self.name} no pudo inicializarse. El hilo terminará.")
            return
            
        while not self.shutdown_event.is_set():
            try:
                self._acquire_data()
            except Exception as e:
                log.error(f"Error no controlado en el bucle de adquisición de {self.name}: {e}")
                self.shutdown_event.wait(5.0)

        self._cleanup()
        log.info(f"Hilo para {self.name} detenido limpiamente.")

    @abc.abstractmethod
    def _setup(self) -> bool:
        pass

    @abc.abstractmethod
    def _acquire_data(self):
        pass
        
    @abc.abstractmethod
    def _cleanup(self):
        pass

    def _create_data_packet(self, data_type: str, data: any) -> dict:
        """Crea un paquete de datos estandarizado."""
        return {
            "type": data_type,
            "timestamp": datetime.now().isoformat(),
            "data": data
        }
```

## Archivo: `.\src\data_acquirers\can_acquirer.py`

```python
import logging
import time
from typing import Optional, Dict, Any
import can

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class CANAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="CANAcquirer", config_key="can")
        self.bus = None
        
        # Cargar configuración específica de J1939
        self.pgn_config = self.config.get('pgn_to_listen', [])
        
        # Crear un mapa para búsqueda rápida de PGN a su configuración
        self.pgn_map = {item['pgn']: item for item in self.pgn_config}
        
    def _setup(self) -> bool:
        if not self.pgn_config:
            log.warning("CANAcquirer está habilitado pero no se han definido 'pgn_to_listen' en la configuración. El hilo no hará nada.")
            return True # No es un error fatal

        try:
            interface = self.config.get('interface', 'can0')
            bitrate = self.config.get('bitrate', 250000)
            
            # --- Configuración de filtros para J1939 ---
            # Un ID de J1939 (29 bits) contiene el PGN en los bits 8-25.
            # La máscara 0x1FFFF00 aísla estos bits, ignorando la prioridad y la dirección de origen.
            can_filters = []
            for pgn_item in self.pgn_config:
                pgn = pgn_item['pgn']
                # El ID a filtrar se construye desplazando el PGN a su posición en el ID de 29 bits.
                can_id = pgn << 8
                can_filters.append({"can_id": can_id, "can_mask": 0x1FFFF00, "extended": True})

            self.bus = can.interface.Bus(channel=interface, bustype='socketcan', bitrate=bitrate, can_filters=can_filters)
            log.info(f"Bus CAN (J1939) conectado en '{interface}' con bitrate {bitrate}.")
            log.info(f"Escuchando PGNs: {list(self.pgn_map.keys())}")
            return True
        except (OSError, can.CanError) as e:
            log.critical(f"FATAL: Error al inicializar el bus CAN: {e}. ¿Está la interfaz '{interface}' activa?")
            return False

    def _parse_j1939_message(self, pgn: int, data: bytes) -> Optional[Dict[str, Any]]:
        """
        Parsea los datos de un mensaje J1939 basado en su PGN.
        Referencia: J1939 Digital Annex (SPNs)
        """
        value = None
        try:
            # PGN 61444 (0xF004) - Electronic Engine Controller 1 (EEC1)
            if pgn == 61444:
                # SPN 190: Engine Speed
                # Bytes 4-5, resolución 0.125 rpm/bit, offset 0
                value = (data[4] * 256 + data[3]) * 0.125
            
            # PGN 65265 (0xFEF1) - Cruise Control/Vehicle Speed (CCVS)
            elif pgn == 65265:
                # SPN 84: Wheel-Based Vehicle Speed
                # Bytes 2-3, resolución 1/256 km/h por bit, offset 0
                value = (data[2] * 256 + data[1]) / 256.0

            # PGN 65262 (0xFEEE) - Engine Temperature 1 (ET1)
            elif pgn == 65262:
                # SPN 110: Engine Coolant Temperature
                # Byte 1, resolución 1 °C/bit, offset -40 °C
                value = data[0] - 40.0
                
            # PGN 61443 (0xF003) - Electronic Engine Controller 2 (EEC2)
            elif pgn == 61443:
                 # SPN 91: Accelerator Pedal Position 1
                 # Byte 2, resolución 0.4 %/bit, offset 0
                 value = data[1] * 0.4

            # --- Añadir más decodificadores de PGN aquí ---

            if value is not None:
                return {"value": round(value, 2)}
                
        except IndexError:
            log.warning(f"Índice fuera de rango al parsear PGN {pgn}. Longitud de datos: {len(data)}")

        return None

    def _acquire_data(self):
        if not self.pgn_map or not self.bus:
            self.shutdown_event.wait(1.0) # Esperar si no hay nada que hacer
            return

        # Bucle de escucha pasiva. `recv` bloqueará hasta que llegue un mensaje
        # que pase los filtros configurados o hasta que expire el timeout.
        msg = self.bus.recv(timeout=1.0)

        if msg:
            # Extraer el PGN del ID de arbitraje de 29 bits
            pgn = (msg.arbitration_id >> 8) & 0x1FFFF
            
            if pgn in self.pgn_map:
                parsed_data = self._parse_j1939_message(pgn, msg.data)
                
                if parsed_data:
                    pgn_info = self.pgn_map[pgn]
                    final_data = {
                        "pgn_name": pgn_info['name'],
                        "pgn": pgn,
                        "value": parsed_data['value'],
                        "unit": pgn_info.get('unit', 'N/A'),
                        "raw_data": msg.data.hex().upper()
                    }
                    packet = self._create_data_packet("can", final_data)
                    self.data_queue.put(packet)
                    log.debug(f"Mensaje J1939 recibido y procesado para {pgn_info['name']}: {final_data['value']} {final_data['unit']}")
                else:
                    log.warning(f"Mensaje J1939 con PGN {pgn} ('{self.pgn_map[pgn]['name']}') recibido pero no se pudo parsear. Datos: {msg.data.hex().upper()}")

    def _cleanup(self):
        if self.bus:
            self.bus.shutdown()
            log.info("Bus CAN desconectado.")
```

## Archivo: `.\src\data_acquirers\gpio_acquirer.py`

```python
# Archivo: .\src\data_acquirers\gpio_acquirer.py

import logging
from datetime import datetime
import RPi.GPIO as GPIO # Importación directa.

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class GPIOAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="GPIOAcquirer", config_key="gpio_rotativo")
        self.pin = self.config.get("pin")
        self.period = self.config.get("log_period_sec", 15)

    def _setup(self) -> bool:
        if self.pin is None:
            log.critical("FATAL: No se ha especificado un pin GPIO para el sensor rotativo en la configuración.")
            return False
        
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            log.info(f"Configurado pin GPIO {self.pin} para sensor rotativo.")
            return True
        except (RuntimeError, ValueError) as e:
            log.critical(f"FATAL: Error al configurar GPIO: {e}. ¿Estás ejecutando como root o tienes permisos?")
            return False

    def _acquire_data(self):
        self.shutdown_event.wait(self.period)
        
        if self.shutdown_event.is_set(): # Añadimos una comprobación para salir rápido
            return

        status_int = GPIO.input(self.pin)
        status_str = "ON" if status_int == GPIO.HIGH else "OFF"
        
        data = {
            "pin": self.pin,
            "status": status_str
        }
        packet = self._create_data_packet("rotativo", data)
        self.data_queue.put(packet)

    def _cleanup(self):
        # La limpieza se hará de forma centralizada al final de la aplicación.
        log.info("GPIOAcquirer finalizando. La limpieza de GPIO se gestionará globalmente.")
```

## Archivo: `.\src\data_acquirers\gps_acquirer.py`

```python
import logging
import time
from datetime import datetime
from typing import Optional
import smbus2 as smbus

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class GPSAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="GPSAcquirer", config_key="gps")
        self.bus = None
        self.i2c_addr = self.config.get('i2c_addr', 0x42)
        self.read_buffer = b''

    def _setup(self) -> bool:
        try:
            bus_id = self.config.get('i2c_bus', 1)
            self.bus = smbus.SMBus(bus_id)
            # Prueba de lectura simple para confirmar que el dispositivo está presente
            self.bus.read_byte(self.i2c_addr)
            log.info(f"Comunicación I2C para GPS (u-blox) iniciada en bus {bus_id}, dirección {hex(self.i2c_addr)}.")
            return True
        except (IOError, FileNotFoundError) as e:
            log.critical(f"FATAL: No se pudo inicializar I2C para GPS: {e}. Compruebe conexiones, permisos y configuración.")
            self.bus = None
            return False

    def _parse_nmea_lat_lon(self, raw_val: str, direction: str) -> Optional[str]:
        """Convierte una coordenada NMEA a grados decimales."""
        if not raw_val or not direction:
            return None
        
        try:
            val_float = float(raw_val)
            degrees = int(val_float / 100)
            minutes = val_float - (degrees * 100)
            decimal_degrees = degrees + (minutes / 60)
            
            if direction in ['S', 'W']:
                decimal_degrees *= -1
                
            return f"{decimal_degrees:.6f}"
        except (ValueError, TypeError):
            log.warning(f"Valor de coordenada GPS inválido: val='{raw_val}', dir='{direction}'")
            return None

    def _process_buffer(self):
        """Procesa el búfer de lectura en busca de sentencias NMEA completas."""
        # Las sentencias NMEA terminan en \r\n
        while b'\r\n' in self.read_buffer:
            line, self.read_buffer = self.read_buffer.split(b'\r\n', 1)
            line_str = line.decode('ascii', errors='ignore').strip()
            
            if line_str.startswith('$GPRMC'):
                self._parse_gprmc(line_str)

    def _parse_gprmc(self, line: str):
        """Parsea una línea GPRMC y la pone en la cola si es válida."""
        parts = line.split(',')
        # GPRMC debe tener al menos 12 campos y el estado (parts[2]) debe ser 'A' (Activo)
        if len(parts) < 12:
            log.debug(f"Trama GPRMC malformada o incompleta: {line}")
            return
            
        if parts[2] != 'A':
            log.info("GPS no tiene un fix válido (estado != 'A'). Esperando señal.")
            return

        lat = self._parse_nmea_lat_lon(parts[3], parts[4])
        lon = self._parse_nmea_lat_lon(parts[5], parts[6])
        
        # Solo enviar paquete si tenemos coordenadas válidas
        if lat is not None and lon is not None:
            data = {
                "latitude": lat,
                "longitude": lon,
                "speed_knots": parts[7] if parts[7] else "0.0",
                "fix_status": "Active"
            }
            packet = self._create_data_packet("gps", data)
            self.data_queue.put(packet)
            log.debug(f"Paquete GPS válido procesado: {data}")

    def _acquire_data(self):
        try:
            # Los módulos u-blox en I2C tienen registros para saber cuántos bytes hay disponibles
            bytes_available_high = self.bus.read_byte_data(self.i2c_addr, 0xFD)
            bytes_available_low = self.bus.read_byte_data(self.i2c_addr, 0xFE)
            bytes_to_read = (bytes_available_high << 8) | bytes_available_low

            if bytes_to_read > 0:
                # Leer todos los bytes disponibles (hasta un máximo razonable por ciclo)
                # La lectura en bloques es más eficiente
                read_len = min(bytes_to_read, 256) 
                raw_bytes = self.bus.read_i2c_block_data(self.i2c_addr, 0xFF, read_len)
                self.read_buffer += bytes(raw_bytes)
                
                # Procesar el búfer para extraer líneas completas
                self._process_buffer()

        except IOError as e:
            log.warning(f"Error de I/O al leer el GPS: {e}. Reintentando...")
        
        # Esperar un poco antes de la siguiente lectura para no saturar el bus I2C
        self.shutdown_event.wait(0.5)

    def _cleanup(self):
        if self.bus:
            self.bus.close()
            log.info("Bus I2C para GPS cerrado.")
```

## Archivo: `.\src\data_acquirers\imu_acquirer.py`

```python
import logging
from datetime import datetime
from typing import Optional
import serial

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class IMUAcquirer(BaseAcquirer):
    # Claves que coinciden exactamente con la cabecera de la trama
    DATA_KEYS = [
        "ax", "ay", "az", "gx", "gy", "gz",
        "roll", "pitch", "yaw", "timeantwifi",
        "usciclo1", "usciclo2", "usciclo3", "usciclo4", "usciclo5",
        "si", "accmag", "microsds", "k3"
    ]
    EXPECTED_VALUES = len(DATA_KEYS)

    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="IMUAcquirer", config_key="estabilometro")
        self.ser = None

    def _setup(self) -> bool:
        try:
            port = self.config.get("serial_port")
            baud_rate = self.config.get("baud_rate")
            self.ser = serial.Serial(port, baud_rate, timeout=1)
            log.info(f"Puerto serie '{port}' abierto para Estabilómetro/IMU a {baud_rate} baudios.")
            return True
        except serial.SerialException as e:
            log.critical(f"FATAL: No se pudo abrir el puerto serie para Estabilómetro/IMU: {e}. Compruebe permisos y conexión.")
            self.ser = None
            return False

    def _parse_stabilometer_data(self, line: str) -> Optional[dict]:
        """Parsea la trama del estabilómetro, convirtiendo valores a float si es posible."""
        try:
            values = [v.strip() for v in line.split(';')]
            if len(values) != self.EXPECTED_VALUES:
                log.warning(f"Trama con número incorrecto de valores. "
                            f"Esperados: {self.EXPECTED_VALUES}, Recibidos: {len(values)}. Trama: '{line}'")
                return None
            
            # Intentar convertir todos los valores a float. Si falla el primero,
            # probablemente es una cabecera, así que la ignoramos.
            try:
                float(values[0])
            except ValueError:
                log.info(f"Línea ignorada (posible cabecera): {line}")
                return None

            # Construir el diccionario convirtiendo cada valor
            data_dict = {}
            for key, value_str in zip(self.DATA_KEYS, values):
                try:
                    data_dict[key] = float(value_str)
                except ValueError:
                    # Si un valor no es numérico, lo guardamos como string
                    data_dict[key] = value_str 
            
            return data_dict

        except Exception as e:
            log.error(f"Error al parsear la línea del estabilómetro '{line}': {e}")
            return None

    def _acquire_data(self):
        try:
            if self.ser and self.ser.in_waiting > 0:
                line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    parsed_data = self._parse_stabilometer_data(line)
                    if parsed_data:
                        packet = self._create_data_packet("estabilometro", parsed_data)
                        self.data_queue.put(packet)
        except serial.SerialException as e:
            log.error(f"Error grave de puerto serie durante la lectura: {e}. El hilo terminará.")
            if self.ser and self.ser.is_open:
                self.ser.close()
            self.ser = None
            # Relanzamos la excepción para que el gestor principal sepa que el hilo ha muerto
            raise e
        except Exception as e:
            log.error(f"Error inesperado en adquisición de estabilómetro: {e}")

        # Pequeña pausa para no consumir 100% de CPU si no hay datos
        self.shutdown_event.wait(0.01)

    def _cleanup(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            log.info("Puerto serie del Estabilómetro/IMU cerrado.")
```

## Archivo: `.\src\data_acquirers\__init__.py`

```python

```

## Archivo: `.\src\gui\gui_controller.py`

```python
class GuiController:
    """
    Intermediario entre la lógica de la aplicación (AppController) y la GUI (MainWindow y sus vistas).
    """
    def __init__(self, app_controller, main_window):
        self.app_controller = app_controller
        self.main_window = main_window

    def update_all_views(self):
        """Pide los datos más recientes al backend y actualiza todas las vistas."""
        latest_data = self.app_controller.get_latest_data()
        service_status = self.app_controller.get_service_status()

        # Actualizar la vista de estado
        if self.main_window.status_view:
            self.main_window.status_view.update_data(latest_data, service_status)
        
        # Aquí se llamarían a los métodos de actualización de otras vistas
        # ej. self.main_window.can_view.update_data(...)
```

## Archivo: `.\src\gui\main_window.py`

```python
import tkinter as tk
from tkinter import ttk
from .gui_controller import GuiController
from .views.status_view import StatusView

class MainWindow:
    def __init__(self, app_controller):
        self.app_controller = app_controller
        self.is_running_flag = False

        self.root = tk.Tk()
        self.root.title("fire-truck-app Control Panel")
        self.root.geometry("800x600")

        self.gui_controller = GuiController(app_controller, self)
        
        self.create_widgets()

        # Iniciar el ciclo de actualización de la UI
        self.update_ui()
        
        # Manejar el cierre de la ventana
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def create_widgets(self):
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # Aquí se podrían añadir más vistas en un Notebook (pestañas) o en un panel lateral
        self.status_view = StatusView(self.main_frame)
        self.status_view.pack(fill=tk.BOTH, expand=True)

    def update_ui(self):
        """Llama al controlador para que actualice los datos de las vistas."""
        if not self.is_running_flag:
            return
        
        self.gui_controller.update_all_views()
        # Reprogramar la próxima actualización en 1 segundo (1000 ms)
        self.root.after(1000, self.update_ui)

    def run(self):
        """Inicia el bucle principal de la GUI."""
        self.is_running_flag = True
        self.root.mainloop()

    def close(self):
        """Cierra la ventana de la GUI."""
        if self.is_running_flag:
            self.is_running_flag = False
            self.root.destroy()
            
    def is_running(self) -> bool:
        return self.is_running_flag
```

## Archivo: `.\src\gui\__init__.py`

```python

```

## Archivo: `.\src\gui\views\status_view.py`

```python
import tkinter as tk
from tkinter import ttk

class StatusView(ttk.Frame):
    """
    Una vista de la GUI que muestra el estado general del sistema y los últimos datos recibidos.
    """
    def __init__(self, parent):
        super().__init__(parent, padding="10")
        
        self.data_labels = {}
        self.status_labels = {}

        # --- Sección de Estado de Servicios ---
        status_frame = ttk.LabelFrame(self, text="Estado de los Servicios", padding="10")
        status_frame.pack(fill=tk.X, pady=10)
        self.status_container = status_frame

        # --- Sección de Últimos Datos ---
        data_frame = ttk.LabelFrame(self, text="Últimos Datos Recibidos", padding="10")
        data_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        self.data_container = data_frame

    def update_data(self, latest_data: dict, service_status: dict):
        """Recibe datos del GuiController y actualiza las etiquetas."""
        
        # Actualizar estado de servicios
        for service_name, status in service_status.items():
            if service_name not in self.status_labels:
                frame = ttk.Frame(self.status_container)
                ttk.Label(frame, text=f"{service_name}:").pack(side=tk.LEFT)
                self.status_labels[service_name] = ttk.Label(frame, text="Desconocido", width=10)
                self.status_labels[service_name].pack(side=tk.LEFT, padx=5)
                frame.pack(anchor="w")
            
            self.status_labels[service_name].config(text=status, foreground="green" if status == "Running" else "red")
            
        # Actualizar últimos datos
        for data_type, packet in latest_data.items():
            if data_type not in self.data_labels:
                self.data_labels[data_type] = ttk.Label(self.data_container, text="", justify=tk.LEFT)
                self.data_labels[data_type].pack(anchor="w", pady=2)
            
            timestamp = packet.get('timestamp', 'N/A').split('.')[0] # Quitar microsegundos
            data_str = ", ".join(f"{k}: {v}" for k, v in packet.get('data', {}).items())
            self.data_labels[data_type].config(text=f"[{data_type.upper()}] @ {timestamp} -> {data_str}")
```

## Archivo: `.\src\gui\views\__init__.py`

```python

```

## Archivo: `.\src\processing\__init__.py`

```python

```

## Archivo: `.\src\transmitters\ftp_transmitter.py`

```python
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
```

## Archivo: `.\src\transmitters\__init__.py`

```python

```

## Archivo: `.\src\utils\config_loader.py`

```python
import yaml
import logging

log = logging.getLogger(__name__)

class ConfigLoader:
    """Carga y proporciona acceso a la configuración desde un archivo YAML."""
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self):
        log.info(f"Cargando configuración desde: {self.config_path}")
        with open(self.config_path, 'r') as f:
            try:
                config_data = yaml.safe_load(f)
                return config_data
            except yaml.YAMLError as e:
                log.error(f"Error al parsear el archivo YAML: {e}")
                raise
    
    def get_config(self) -> dict:
        """Devuelve el diccionario de configuración completo."""
        return self.config
    
    def get_section(self, section_name: str) -> dict:
        """Devuelve una sección específica de la configuración."""
        return self.config.get(section_name, {})
```

## Archivo: `.\src\utils\unified_logger.py`

```python
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
```

## Archivo: `.\src\utils\__init__.py`

```python

```

