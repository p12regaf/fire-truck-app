import logging
from datetime import datetime
import can

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class CANAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="CANAcquirer", config_key="can")
        self.bus = None

    def _setup(self) -> bool:
        try:
            interface = self.config.get('interface', 'can0')
            bitrate = self.config.get('bitrate', 500000)
            self.bus = can.interface.Bus(channel=interface, bustype='socketcan', bitrate=bitrate)
            log.info(f"Bus CAN conectado en la interfaz '{interface}' con bitrate {bitrate}.")
            return True
        except (OSError, can.CanError) as e:
            log.critical(f"FATAL: Error al inicializar el bus CAN: {e}. ¿Está la interfaz 'up'?")
            return False

    def _acquire_data(self):
        msg = self.bus.recv(timeout=1.0)
        if msg is not None:
            data = {
                "arbitration_id": f"0x{msg.arbitration_id:X}",
                "data": msg.data.hex().upper(),
                "is_extended_id": msg.is_extended_id,
            }
            packet = self._create_data_packet("can", data)
            self.data_queue.put(packet)

    def _cleanup(self):
        if self.bus:
            self.bus.shutdown()
            log.info("Bus CAN desconectado.")