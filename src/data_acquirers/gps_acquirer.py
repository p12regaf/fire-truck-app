import logging
import time
import struct
from typing import Optional, Tuple

import smbus2 as smbus

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

# Constante para la conversión de milímetros por segundo a kilómetros por hora
MMS_TO_KMPH = 0.0036

# --- Constantes del protocolo u-blox ---
# Caracteres de sincronización que inician cada mensaje UBX
UBX_SYNC_CHAR_1 = 0xB5
UBX_SYNC_CHAR_2 = 0x62

# Clases de mensajes UBX
UBX_CLASS_NAV = 0x01  # Mensajes de Navegación

# IDs de mensajes UBX dentro de la clase NAV
UBX_ID_NAV_PVT = 0x07 # Position, Velocity, Time Solution

# Registros I2C del módulo u-blox
# Registro para leer el número de bytes disponibles en el buffer
UBLOX_I2C_BYTES_AVAIL_REG = 0xFD
# Registro para leer el stream de datos
UBLOX_I2C_DATA_STREAM_REG = 0xFF


class GPSAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="GPSAcquirer", config_key="gps")
        self.bus = None
        self.i2c_addr = self.config.get('i2c_addr', 0x42)
        # Búfer para almacenar datos parciales leídos del I2C
        self._buffer = bytearray()

    def _calculate_checksum(self, payload: bytes) -> Tuple[int, int]:
        """Calcula el checksum Fletcher de 8 bits para un mensaje UBX."""
        ck_a, ck_b = 0, 0
        for byte in payload:
            ck_a = (ck_a + byte) & 0xFF
            ck_b = (ck_b + ck_a) & 0xFF
        return ck_a, ck_b

    def _send_ubx_poll_request(self, class_id: int, msg_id: int) -> bool:
        """Construye y envía una solicitud de sondeo (poll) UBX por I2C."""
        try:
            # Una solicitud de sondeo tiene una longitud de payload de 0
            payload_len = 0
            # El mensaje a checksumear incluye: Clase, ID, Longitud
            message_core = struct.pack('<BBH', class_id, msg_id, payload_len)
            
            ck_a, ck_b = self._calculate_checksum(message_core)
            
            # El mensaje completo a enviar
            message = bytes([UBX_SYNC_CHAR_1, UBX_SYNC_CHAR_2]) + message_core + bytes([ck_a, ck_b])
            
            # En I2C, se escribe al registro de data stream
            self.bus.write_i2c_block_data(self.i2c_addr, UBLOX_I2C_DATA_STREAM_REG, list(message))
            return True
        except IOError as e:
            log.error(f"Error al enviar mensaje UBX a {hex(self.i2c_addr)}: {e}")
            return False

    def _read_and_parse_ubx(self) -> Optional[dict]:
        """Lee datos del bus I2C, busca un mensaje UBX válido y lo decodifica."""
        try:
            # 1. Consultar cuántos bytes hay disponibles para leer
            bytes_available_data = self.bus.read_i2c_block_data(self.i2c_addr, UBLOX_I2C_BYTES_AVAIL_REG, 2)
            bytes_available = (bytes_available_data[0] << 8) | bytes_available_data[1]

            if bytes_available == 0:
                return None

            # 2. Leer los bytes disponibles del stream de datos
            # Limitamos la lectura para no bloquear el bus demasiado tiempo
            data_to_read = min(bytes_available, 128) 
            raw_data = self.bus.read_i2c_block_data(self.i2c_addr, UBLOX_I2C_DATA_STREAM_REG, data_to_read)
            self._buffer.extend(raw_data)

        except IOError as e:
            log.warning(f"Error de I/O al leer del GPS: {e}")
            return None

        # 3. Buscar un mensaje UBX completo en nuestro búfer
        while len(self._buffer) >= 8: # Mínimo para cabecera y checksum
            # Buscar el inicio del mensaje
            if self._buffer[0] != UBX_SYNC_CHAR_1 or self._buffer[1] != UBX_SYNC_CHAR_2:
                self._buffer.pop(0) # Descartar byte y seguir buscando
                continue

            # Tenemos un posible inicio. Leer la cabecera.
            # < = Little-endian, B = uchar (1 byte), H = ushort (2 bytes)
            class_id, msg_id, payload_len = struct.unpack_from('<BBH', self._buffer, 2)
            
            # Comprobar si tenemos el mensaje completo en el búfer
            msg_end_idx = 6 + payload_len + 2 # Cabecera (6) + payload + checksum (2)
            if len(self._buffer) < msg_end_idx:
                return None # Mensaje incompleto, esperar más datos

            # Extraer el mensaje completo
            message_bytes = self._buffer[:msg_end_idx]
            # Quitar el mensaje procesado del búfer
            self._buffer = self._buffer[msg_end_idx:]

            # 4. Validar el checksum
            payload = message_bytes[2:6+payload_len]
            ck_a, ck_b = self._calculate_checksum(payload)
            
            if ck_a != message_bytes[msg_end_idx - 2] or ck_b != message_bytes[msg_end_idx - 1]:
                log.warning("Checksum de mensaje UBX incorrecto. Descartando.")
                continue # El checksum no coincide, buscar el siguiente mensaje

            # 5. Si el checksum es válido y es el mensaje que nos interesa, decodificarlo
            if class_id == UBX_CLASS_NAV and msg_id == UBX_ID_NAV_PVT:
                return self._parse_nav_pvt(message_bytes[6:6+payload_len])

        return None # No se encontró un mensaje completo y válido

    def _parse_nav_pvt(self, payload: bytes) -> dict:
        """Decodifica el payload de un mensaje NAV-PVT."""
        # Estructura del payload NAV-PVT (solo los campos que nos interesan)
        # offset 20: fixType (1 byte)
        # offset 24: lon (4 bytes, int32, 1e-7 deg)
        # offset 28: lat (4 bytes, int32, 1e-7 deg)
        # offset 60: gSpeed (4 bytes, int32, mm/s)
        
        # Formato: 20 bytes de padding, 1 byte fixType, 3 bytes padding, 
        # 2x int32 para lon/lat, 28 bytes padding, 1x int32 para gSpeed
        # 'x' es un byte de padding
        # 'B' es un unsigned char (1 byte)
        # 'l' es un signed long (4 bytes)
        fix_type, lon, lat, g_speed = struct.unpack_from('<20x B 3x l l 28x l', payload)
        
        # La librería original considera fix >= 3 como válido
        if fix_type >= 3:
            # Escalar los valores a sus unidades correctas
            latitude = lat / 1e7
            longitude = lon / 1e7
            speed_mms = g_speed
            speed_kmph = speed_mms * MMS_TO_KMPH

            return {
                "latitude": f"{latitude:.6f}",
                "longitude": f"{longitude:.6f}",
                "speed_kmph": f"{speed_kmph:.2f}",
                "fix_status": "Active"
            }
        else:
            log.info(f"GPS no tiene un fix válido (fix_type={fix_type}). Esperando señal.")
            return {}

    def _setup(self) -> bool:
        """Inicializa la comunicación I2C con el módulo GPS."""
        try:
            bus_id = self.config.get('i2c_bus', 1)
            self.bus = smbus.SMBus(bus_id)
            
            # Verificación de conexión: intentar leer un registro.
            # Si esto falla, el dispositivo no está en el bus.
            self.bus.read_byte(self.i2c_addr)
            
            log.info(f"Comunicación I2C con dispositivo en {hex(self.i2c_addr)} en bus {bus_id} iniciada.")
            # Opcional: podrías enviar un mensaje de configuración aquí si fuera necesario
            return True
        except (IOError, FileNotFoundError) as e:
            log.critical(f"FATAL: No se pudo inicializar I2C para GPS: {e}. Compruebe conexiones y configuración.")
            if self.bus:
                self.bus.close()
            self.bus = None
            return False

    def _acquire_data(self):
        """Solicita, lee y procesa datos del GPS."""
        # 1. Solicitar el último dato NAV-PVT al módulo
        if not self._send_ubx_poll_request(UBX_CLASS_NAV, UBX_ID_NAV_PVT):
            # Si falla el envío, esperar y reintentar en el próximo ciclo
            self.shutdown_event.wait(1.0)
            return

        # 2. Darle tiempo al módulo para procesar y preparar la respuesta
        self.shutdown_event.wait(0.1)

        # 3. Leer y decodificar la respuesta
        parsed_data = self._read_and_parse_ubx()

        if parsed_data:
            packet = self._create_data_packet("gps", parsed_data)
            self.data_queue.put(packet)
            log.debug(f"Paquete GPS válido procesado: {parsed_data}")
        
        # 4. Esperar antes de la siguiente lectura para una tasa de ~1Hz.
        self.shutdown_event.wait(0.9)

    def _cleanup(self):
        """Cierra el bus I2C al finalizar."""
        if self.bus:
            self.bus.close()
            log.info("Bus I2C para GPS cerrado.")