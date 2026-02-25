import logging
import random
import time
from datetime import datetime

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class SimulatedGPSAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="SimulatedGPSAcquirer", config_key="gps")
        self._last_speed_kmh = "0.00"
        self._last_date_str = datetime.now().strftime("%d/%m/%Y")

    def _setup(self) -> bool:
        log.info("Simulated GPS Acquirer initialized.")
        return True

    def _acquire_data(self):
        # Generate fake GPS data around Madrid
        data = {
            "status": "Valid",
            "latitude": f"{40.4168 + random.uniform(-0.01, 0.01):.7f}",
            "longitude": f"{-3.7038 + random.uniform(-0.01, 0.01):.7f}",
            "altitude_m": f"{650.0 + random.uniform(-5, 5):.1f}",
            "hdop": f"{1.0 + random.uniform(0, 0.5):.1f}",
            "fix_quality": "1",
            "num_sats": str(random.randint(8, 12)),
            "speed_kmph": f"{random.uniform(0, 100):.2f}",
            "gps_time": datetime.now().strftime("%H:%M:%S"),
            "gps_date": self._last_date_str
        }
        packet = self._create_data_packet("gps", data)
        self.data_queue.put(packet)
        self.shutdown_event.wait(1.0)

    def _cleanup(self):
        log.info("Simulated GPS Acquirer stopped.")


class SimulatedCANAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="SimulatedCANAcquirer", config_key="can")
        self.log_interval_sec = self.config.get('log_interval_sec', 10.0)

    def _setup(self) -> bool:
        log.info("Simulated CAN Acquirer initialized.")
        return True

    def _acquire_data(self):
        # Generate fake CAN data for PGNs (Engine Speed, Vehicle Speed)
        engine_speed = random.uniform(800, 3000)
        vehicle_speed = random.uniform(0, 120)
        
        data = {
            "Engine Speed": round(engine_speed, 2),
            "Wheel-Based Vehicle Speed": round(vehicle_speed, 2),
            "raw_data": "0011223344556677",
            "interface": "sim0",
            "arbitration_id_hex": "18F00400"
        }
        packet = self._create_data_packet("can", data)
        self.data_queue.put(packet)
        self.shutdown_event.wait(self.log_interval_sec)

    def _cleanup(self):
        log.info("Simulated CAN Acquirer stopped.")


class SimulatedIMUAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="SimulatedIMUAcquirer", config_key="estabilometro")

    def _setup(self) -> bool:
        log.info("Simulated IMU Acquirer initialized.")
        return True

    def _acquire_data(self):
        # Generate fake IMU data
        data = {key: random.uniform(-1, 1) for key in [
            "ax", "ay", "az", "gx", "gy", "gz",
            "roll", "pitch", "yaw"
        ]}
        # Add other fields
        data.update({
            "timeantwifi": 100,
            "usciclo1": 10, "usciclo2": 10, "usciclo3": 10, "usciclo4": 10, "usciclo5": 10,
            "si": 1, "accmag": 0, "microsds": 1, "k3": 0
        })
        
        packet = self._create_data_packet("estabilometro", data)
        self.data_queue.put(packet)
        self.shutdown_event.wait(0.1)

    def _cleanup(self):
        log.info("Simulated IMU Acquirer stopped.")


class SimulatedGPIOAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="SimulatedGPIOAcquirer", config_key="gpio_rotativo")
        self.pin = self.config.get("pin", 22)
        self.period = self.config.get("log_period_sec", 1)

    def _setup(self) -> bool:
        log.info("Simulated GPIO Acquirer initialized.")
        return True

    def _acquire_data(self):
        data = {
            "pin": self.pin,
            "status": random.randint(0, 1)
        }
        packet = self._create_data_packet("rotativo", data)
        self.data_queue.put(packet)
        self.shutdown_event.wait(self.period)

    def _cleanup(self):
        log.info("Simulated GPIO Acquirer stopped.")
