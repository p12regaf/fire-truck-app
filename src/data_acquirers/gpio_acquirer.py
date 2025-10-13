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