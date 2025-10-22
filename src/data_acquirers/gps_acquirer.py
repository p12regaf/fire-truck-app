import logging
import time
import smbus2 as smbus # Usamos smbus2 que ya está en tus dependencias
from typing import Optional, Dict, Any

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class GPSAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="GPSAcquirer", config_key="gps")
        self.bus = None
        self.i2c_addr = self.config.get('i2c_addr', 0x42)
        
        # Buffer para almacenar datos parciales leídos del I2C
        self._buffer = bytearray()
        
        # Almacenamos la última velocidad leída de una trama RMC para añadirla a la GGA
        self._last_speed_kmh: Optional[str] = None
        
        # Límite para el buffer para evitar fugas de memoria si los datos no tienen fin de línea
        self.MAX_BUFFER_SIZE = 4096 

    def _setup(self) -> bool:
        """Inicializa la comunicación I2C con el módulo GPS."""
        try:
            bus_id = self.config.get('i2c_bus', 1)
            self.bus = smbus.SMBus(bus_id)
            
            # Verificación de conexión: intentar leer un byte.
            self.bus.read_byte(self.i2c_addr)
            
            log.info(f"Comunicación I2C (NMEA) con dispositivo en {hex(self.i2c_addr)} en bus {bus_id} iniciada.")
            return True
        except (IOError, FileNotFoundError) as e:
            log.critical(f"FATAL: No se pudo inicializar I2C para GPS: {e}. Compruebe conexiones y configuración.")
            if self.bus:
                self.bus.close()
            self.bus = None
            return False

    def _convert_lat_lon(self, coord: str, direction: str) -> Optional[float]:
        """Convierte coordenadas de formato NMEA (DDMM.MMMM) a grados decimales."""
        if not coord:
            return None
        try:
            grados = int(float(coord) / 100)
            minutos = float(coord) - grados * 100
            dec = grados + minutos / 60.0
            if direction in ['S', 'W']:
                dec = -dec
            return round(dec, 7)
        except (ValueError, TypeError):
            return None

    def _parse_gnrmc(self, line: str):
        """Parsea una trama GNRMC/GPRMC para extraer la velocidad y la guarda en estado."""
        try:
            campos = line.strip().split(",")
            if len(campos) < 8 or not campos[7]:
                self._last_speed_kmh = None
                return
            
            velocidad_nudos = float(campos[7])
            velocidad_kmh = velocidad_nudos * 1.852
            self._last_speed_kmh = f"{velocidad_kmh:.2f}"
        except (ValueError, IndexError) as e:
            log.warning(f"Error parseando GNRMC: {e} en línea: {line}")
            self._last_speed_kmh = None

    def _parse_gngga(self, line: str) -> Optional[Dict[str, Any]]:
        """Parsea una trama GNGGA/GPGGA y la combina con la última velocidad conocida."""
        try:
            campos = line.strip().split(",")
            if len(campos) < 10:
                return None
                
            fix = campos[6]
            # Si no hay fix (fix='0') o no hay coordenadas, los datos no son válidos
            if fix == '0' or not campos[2] or not campos[4]:
                log.info("Trama GGA recibida, pero sin fix de GPS.")
                return None

            lat_decimal = self._convert_lat_lon(campos[2], campos[3])
            lon_decimal = self._convert_lat_lon(campos[4], campos[5])

            if lat_decimal is None or lon_decimal is None:
                return None

            return {
                "latitude": f"{lat_decimal:.6f}",
                "longitude": f"{lon_decimal:.6f}",
                "altitude_m": campos[9] if campos[9] else "N/A",
                "hdop": campos[8] if campos[8] else "N/A",
                "fix_quality": fix,
                "num_sats": campos[7] if campos[7] else "N/A",
                "speed_kmph": self._last_speed_kmh if self._last_speed_kmh is not None else "N/A"
            }
        except (ValueError, IndexError) as e:
            log.warning(f"Error parseando GNGGA: {e} en línea: {line}")
            return None

    def _acquire_data(self):
        """Lee datos del bus I2C, busca tramas NMEA completas y las procesa."""
        try:
            # Leemos en bloques para eficiencia. 32 bytes es un tamaño común.
            block = self.bus.read_i2c_block_data(self.i2c_addr, 0, 32)
            self._buffer.extend(byte for byte in block if byte != 0) # Ignorar bytes nulos
        except IOError:
            # Es normal tener errores de I/O si el GPS no tiene datos listos.
            # Esperamos un poco para no saturar el bus.
            self.shutdown_event.wait(0.2)
            return

        if len(self._buffer) > self.MAX_BUFFER_SIZE:
            log.error(f"Buffer de GPS superó el límite de {self.MAX_BUFFER_SIZE} bytes. Vaciando para recuperarse.")
            self._buffer = bytearray()

        # Procesar todas las líneas completas que tengamos en el buffer
        while b'\n' in self._buffer:
            line_bytes, self._buffer = self._buffer.split(b'\n', 1)
            line_str = line_bytes.decode('ascii', errors='ignore').strip()

            if line_str.startswith('$GNRMC') or line_str.startswith('$GPRMC'):
                self._parse_gnrmc(line_str)
            
            elif line_str.startswith('$GNGGA') or line_str.startswith('$GPGGA'):
                parsed_data = self._parse_gngga(line_str)
                
                if parsed_data:
                    packet = self._create_data_packet("gps", parsed_data)
                    self.data_queue.put(packet)
                    log.debug(f"Paquete GPS (NMEA) válido procesado: {parsed_data}")
        
        # Pequeña pausa para no consumir 100% de CPU
        self.shutdown_event.wait(0.1)


    def _cleanup(self):
        """Cierra el bus I2C al finalizar."""
        if self.bus:
            self.bus.close()
            log.info("Bus I2C para GPS cerrado.")