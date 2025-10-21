import logging
import time
from datetime import datetime
from typing import Optional

import smbus2 as smbus
import sparkfun_ublox_gnss as ublox

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

# Constante para la conversión de milímetros por segundo a kilómetros por hora
MMS_TO_KMPH = 0.0036

class GPSAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="GPSAcquirer", config_key="gps")
        self.gps = None
        self.bus = None
        self.i2c_addr = self.config.get('i2c_addr', 0x42)

    def _setup(self) -> bool:
        """Inicializa la comunicación con el módulo GPS usando la librería SparkFun."""
        try:
            bus_id = self.config.get('i2c_bus', 1)
            self.bus = smbus.SMBus(bus_id)
            self.gps = ublox.UBloxGNSS(i2c_bus=self.bus, i2c_address=self.i2c_addr)

            if self.gps.is_connected():
                log.info(f"Comunicación con u-blox GNSS iniciada en bus {bus_id}, dirección {hex(self.i2c_addr)}.")
                return True
            else:
                log.critical("FATAL: Módulo u-blox GNSS detectado pero no responde correctamente.")
                self.gps = None
                self.bus.close()
                self.bus = None
                return False
        except (IOError, FileNotFoundError) as e:
            log.critical(f"FATAL: No se pudo inicializar I2C para GPS: {e}. Compruebe conexiones y configuración.")
            if self.bus:
                self.bus.close()
            self.bus = None
            self.gps = None
            return False

    def _acquire_data(self):
        """Adquiere y procesa datos del GPS usando las propiedades de la librería."""
        try:
            # Comprobamos si tenemos un "fix" 3D (el de mayor calidad)
            # fix_type: 0=No Fix, 1=Dead Reckoning, 2=2D, 3=3D, 4=GNSS+DR, 5=Time only
            if self.gps and self.gps.fix_type >= 3:
                
                lat = self.gps.latitude
                lon = self.gps.longitude
                
                if lat is None or lon is None:
                    log.debug("GPS tiene fix pero las coordenadas aún no están disponibles.")
                    self.shutdown_event.wait(0.2)
                    return

                # La velocidad se da en mm/s. La convertimos a km/h.
                speed_mms = self.gps.ground_speed
                speed_kmph = speed_mms * MMS_TO_KMPH if speed_mms is not None else 0.0

                data = {
                    "latitude": f"{lat:.6f}",
                    "longitude": f"{lon:.6f}",
                    "speed_kmph": f"{speed_kmph:.2f}",
                    "fix_status": "Active"
                }
                
                packet = self._create_data_packet("gps", data)
                self.data_queue.put(packet)
                log.debug(f"Paquete GPS válido procesado: {data}")

            else:
                log.info("GPS no tiene un fix válido (fix_type < 3). Esperando señal.")

        except (IOError, TypeError) as e:
            log.warning(f"Error de I/O al leer el GPS: {e}. Reintentando...")
        
        # Espera antes de la siguiente lectura para no saturar el bus.
        # Ajusta este valor según la tasa de refresco configurada en tu módulo (1.0s para 1Hz).
        self.shutdown_event.wait(1.0)

    def _cleanup(self):
        """Cierra el bus I2C al finalizar."""
        if self.bus:
            self.bus.close()
            log.info("Bus I2C para GPS cerrado.")