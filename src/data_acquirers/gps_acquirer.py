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