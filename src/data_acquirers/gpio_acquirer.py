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
        self.period = self.config.get("log_period_sec", 1)

    def _setup(self) -> bool:
        if self.pin is None:
            log.critical("FATAL: No se ha especificado un pin GPIO para el sensor rotativo en la configuración.")
            return False
        log.info(f"Pin GPIO {self.pin} para sensor rotativo ya configurado centralmente.")
        return True

    def _acquire_data(self):
        while not self.shutdown_event.is_set():
            status_int = GPIO.input(self.pin)
            status_val = 1 if status_int == GPIO.HIGH else 0
            
            data = {
                "pin": self.pin,
                "status": status_val
            }
            packet = self._create_data_packet("rotativo", data)
            self.data_queue.put(packet)
            
            # Esperar el periodo
            if self.shutdown_event.wait(self.period):
                break
    def _cleanup(self):
        # La limpieza se hará de forma centralizada al final de la aplicación.
        log.info("GPIOAcquirer finalizando. La limpieza de GPIO se gestionará globalmente.")