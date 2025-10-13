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