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