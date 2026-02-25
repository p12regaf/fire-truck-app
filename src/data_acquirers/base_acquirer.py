import abc
import time
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
        self.data_seen = False
        self.fatal_error = False
        log.info(f"Inicializando {self.name}...")

    def run(self):
        """
        Método principal del hilo. Llama al método de inicialización específico
        y luego entra en el bucle de adquisición.
        """
        log.info(f"Iniciando hilo para {self.name}.")
        setup_success = self._setup()
        simulation_init_time = time.time()
        
        # Si no hay éxito en el setup pero estamos en modo simulación, permitimos continuar
        is_simulation = self.system_config.get('system', {}).get('simulation_mode', False)
        
        if not setup_success:
            if is_simulation:
                log.warning(f"{self.name}: Falló el setup pero modo SIMULACIÓN activo. Generando datos falsos.")
            else:
                log.error(f"{self.name} no pudo inicializarse. El hilo terminará.")
                return
            
        while not self.shutdown_event.is_set():
            try:
                if setup_success:
                    self._acquire_data()
                
                # Lógica de simulación: si no hemos visto datos reales o el setup falló, inyectar fakes
                if is_simulation and (not self.data_seen or not setup_success):
                    # Solo generamos datos falsos cada 5 segundos para no saturar
                    if time.time() - simulation_init_time > 5.0:
                        self._generate_simulation_data()
                        self.data_seen = True # Esto satisfará el health check de AppController
                        simulation_init_time = time.time()

            except Exception as e:
                log.error(f"Error no controlado en el bucle de adquisición de {self.name}: {e}")
                self.shutdown_event.wait(5.0)

        self._cleanup()
        log.info(f"Hilo para {self.name} detenido limpiamente.")

    def _generate_simulation_data(self):
        """Genera datos de prueba según el tipo de trabajador."""
        import random
        fake_data = {}
        
        if "CAN" in self.name:
            fake_data = {"interface": "vcan0", "arbitration_id_hex": "18F00401", "raw_data": "000000508000FFFF"}
            packet = self._create_data_packet("can", fake_data)
            self.data_queue.put(packet)
        elif "GPS" in self.name:
            fake_data = {"status": "Valid", "latitude": "40.4168", "longitude": "-3.7038", "num_sats": "8", "fix_quality": "1"}
            packet = self._create_data_packet("gps", fake_data)
            self.data_queue.put(packet)
        elif "IMU" in self.name or "IMUAcquirer" in self.name:
            # Claves estándar: pitch_deg, roll_deg, etc.
            fake_data = {k: round(random.uniform(-5, 5), 2) for k in ["pitch_deg", "roll_deg", "yaw_deg"]}
            packet = self._create_data_packet("estabilometro", fake_data)
            self.data_queue.put(packet)
        
        if fake_data:
            log.debug(f"{self.name}: Inyectado dato de SIMULACIÓN: {fake_data}")

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