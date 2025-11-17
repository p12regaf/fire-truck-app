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
        self._last_date_str: Optional[str] = None # NUEVO: Para almacenar la fecha de la trama RMC
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
            if '.' not in coord:
                # Si no hay punto decimal, la trama está corrupta.
                log.warning(f"Coordenada GPS corrupta (sin punto decimal): {coord}")
                return None
            
            grados = int(float(coord) / 100)
            minutos = float(coord) - grados * 100
            dec = grados + minutos / 60.0

            if abs(grados) > 180: # Si es mayor que 180, es inválido para ambos
                 log.warning(f"Coordenada GPS fuera de rango (grados={grados}). Trama corrupta: {coord}")
                 return None
            
            if direction in ['S', 'W']:
                dec = -dec
            return round(dec, 7)
        except (ValueError, TypeError):
            return None

    def _parse_gnrmc(self, line: str):
        try:
            campos = line.strip().split(",")
            # Extraer velocidad
            if len(campos) >= 8 and campos[7]:
                velocidad_nudos = float(campos[7])
                velocidad_kmh = velocidad_nudos * 1.852
                self._last_speed_kmh = f"{velocidad_kmh:.2f}"
            else:
                self._last_speed_kmh = None

            # NUEVO: Extraer fecha
            if len(campos) >= 10 and campos[9]:
                ddmmyy = campos[9]
                # Formatear a dd/mm/yyyy
                self._last_date_str = f"{ddmmyy[0:2]}/{ddmmyy[2:4]}/20{ddmmyy[4:6]}"
                
        except (ValueError, IndexError) as e:
            log.warning(f"Error parseando GNRMC: {e} en línea: {line}")
            self._last_speed_kmh = None

    def _parse_gngga(self, line: str) -> Optional[Dict[str, Any]]:
        try:
            campos = line.strip().split(",")
            if len(campos) < 10:
                return None
            
            # Comprobación de integridad básica: una trama GGA válida tiene al menos 15 campos.
            if len(campos) < 15:
                log.warning(f"Trama GNGGA incompleta (campos: {len(campos)}). Descartando: {line}")
                return None
                
            fix = campos[6]
            if fix == '0' or not campos[2] or not campos[4]:
                log.info("Trama GGA recibida, pero sin fix de GPS. Se registrará el estado.")
                return {"status": "No Fix"}

            lat_decimal = self._convert_lat_lon(campos[2], campos[3])
            lon_decimal = self._convert_lat_lon(campos[4], campos[5])

            if lat_decimal is None or lon_decimal is None:
                # El log de por qué es None ya se ha hecho en _convert_lat_lon
                log.warning(f"Coordenadas inválidas recibidas en trama GNGGA. Descartando.")
                return None # Devuelve None para que la trama entera sea descartada
            
            # NUEVO: Extraer y formatear hora GPS
            gps_time_str = "N/A"
            if campos[1]:
                hhmmss = campos[1].split('.')[0]
                gps_time_str = f"{hhmmss[0:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}"

            return {
                "status": "Valid",
                "latitude": f"{lat_decimal:.7f}", # Aumentada precisión para coincidir con ejemplo
                "longitude": f"{lon_decimal:.7f}",
                "altitude_m": campos[9] if campos[9] else "N/A",
                "hdop": campos[8] if campos[8] else "N/A",
                "fix_quality": fix,
                "num_sats": campos[7] if campos[7] else "N/A",
                "speed_kmph": self._last_speed_kmh if self._last_speed_kmh is not None else "N/A",
                "gps_time": gps_time_str,
                "gps_date": self._last_date_str if self._last_date_str is not None else "N/A"
            }
        except (ValueError, IndexError) as e:
            log.warning(f"Error parseando GNGGA: {e} en línea: {line}")
            return None

    def _acquire_data(self):
        try:
            bytes_read_count = 0
            for _ in range(32):
                byte = self.bus.read_byte(self.i2c_addr)
                if byte == 13: 
                    continue
                self._buffer.append(byte)
                bytes_read_count += 1

        except IOError:
            pass
        except Exception as e:
            log.error(f"Error inesperado leyendo del GPS: {e}")
            self.shutdown_event.wait(1.0)
            return

        if len(self._buffer) > self.MAX_BUFFER_SIZE:
            log.error(f"Buffer de GPS superó el límite de {self.MAX_BUFFER_SIZE} bytes. Vaciando para recuperarse.")
            self._buffer = bytearray()

        while b'\n' in self._buffer:
            line_bytes, self._buffer = self._buffer.split(b'\n', 1)
            line_str = line_bytes.decode('ascii', errors='ignore').strip()
            
            if not line_str:
                continue

            if '$' in line_str:
                if 'RMC' in line_str:
                    self._parse_gnrmc(line_str)
                elif 'GGA' in line_str:
                    parsed_data = self._parse_gngga(line_str)
                    
                    if parsed_data:
                        packet = self._create_data_packet("gps", parsed_data)
                        self.data_queue.put(packet)
                        if parsed_data.get("status") == "Valid":
                            log.debug(f"Paquete GPS (NMEA) válido procesado: {parsed_data}")
                        else:
                            log.debug(f"Paquete GPS (NMEA) sin fix procesado: {parsed_data}")
        
        self.shutdown_event.wait(0.05)


    def _cleanup(self):
        if self.bus:
            self.bus.close()
            log.info("Bus I2C para GPS cerrado.")