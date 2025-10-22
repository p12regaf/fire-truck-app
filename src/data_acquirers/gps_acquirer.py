import logging
import time
import smbus2 as smbus
from typing import Optional, Dict, Any

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class GPSAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="GPSAcquirer", config_key="gps")
        self.bus = None
        self.i2c_addr = self.config.get('i2c_addr', 0x42)
        
        self._buffer = bytearray()
        self._last_speed_kmh: Optional[str] = None
        self.MAX_BUFFER_SIZE = 4096 

    def _setup(self) -> bool:
        try:
            bus_id = self.config.get('i2c_bus', 1)
            self.bus = smbus.SMBus(bus_id)
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
        try:
            campos = line.strip().split(",")
            if len(campos) < 10:
                return None
                
            fix = campos[6]
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
        """
        Lee datos del bus I2C byte a byte, que es más robusto para un flujo de stream NMEA,
        y los procesa cuando encuentra un salto de línea.
        """
        try:
            # === CAMBIO CLAVE: Leer byte a byte ===
            # Intentamos leer hasta 32 bytes en este ciclo para no bloquear el hilo,
            # pero lo hacemos de uno en uno para manejar el stream correctamente.
            bytes_read_count = 0
            for _ in range(32):
                byte = self.bus.read_byte(self.i2c_addr)
                # El carácter de fin de línea en NMEA es 10 (\n). El retorno de carro es 13 (\r).
                # Nos interesa el \n para separar las líneas. Ignoramos el \r (13).
                if byte == 13: # Ignorar retorno de carro
                    continue
                self._buffer.append(byte)
                bytes_read_count += 1

        except IOError:
            # Si no hay más bytes que leer (IOError), es el momento de procesar lo que tenemos.
            pass
        except Exception as e:
            log.error(f"Error inesperado leyendo del GPS: {e}")
            self.shutdown_event.wait(1.0) # Esperar un poco antes de reintentar
            return

        if len(self._buffer) > self.MAX_BUFFER_SIZE:
            log.error(f"Buffer de GPS superó el límite de {self.MAX_BUFFER_SIZE} bytes. Vaciando para recuperarse.")
            self._buffer = bytearray()

        while b'\n' in self._buffer:
            line_bytes, self._buffer = self._buffer.split(b'\n', 1)
            line_str = line_bytes.decode('ascii', errors='ignore').strip()
            
            if not line_str:
                continue

            if '$' in line_str: # Asegurarnos de que es una trama NMEA
                if 'RMC' in line_str:
                    self._parse_gnrmc(line_str)
                elif 'GGA' in line_str:
                    parsed_data = self._parse_gngga(line_str)
                    
                    if parsed_data:
                        packet = self._create_data_packet("gps", parsed_data)
                        self.data_queue.put(packet)
                        log.debug(f"Paquete GPS (NMEA) válido procesado: {parsed_data}")
        
        # Pequeña pausa para no consumir 100% de CPU si el GPS envía datos muy rápido.
        self.shutdown_event.wait(0.05)


    def _cleanup(self):
        if self.bus:
            self.bus.close()
            log.info("Bus I2C para GPS cerrado.")