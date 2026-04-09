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
        self._last_date_str: Optional[str] = None
        self.MAX_BUFFER_SIZE = 4096

        # Geo-fence configurable (defaults: España + Canarias)
        geo = self.config.get('geo_fence', {})
        self.geo_fence_enabled = geo.get('enabled', True)
        self.lat_min = geo.get('lat_min', 35.0)
        self.lat_max = geo.get('lat_max', 45.0)
        self.lon_min = geo.get('lon_min', -19.0)
        self.lon_max = geo.get('lon_max', 5.0)
        self.max_speed_kmh = self.config.get('max_valid_speed_kmh', 250.0)

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
        except (ValueError, TypeError) as e:
            log.warning(f"Error al convertir coordenada GPS '{coord}': {e}")
            return None
        
    def _clean_float(
        self, value: str,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        decimals: Optional[int] = None
    ) -> str:
        """
        Devuelve una cadena numérica limpia o "" si no es válida / está fuera de rango.
        """
        if not value:
            return ""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return ""
        
        if min_value is not None and v < min_value:
            return ""
        if max_value is not None and v > max_value:
            return ""
        
        if decimals is not None:
            fmt = f"{{:.{decimals}f}}"
            return fmt.format(v)
        return str(v)

    def _clean_int(
        self, value: str,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None
    ) -> str:
        """
        Devuelve una cadena entera limpia o "" si no es válida / está fuera de rango.
        """
        if not value:
            return ""
        if not value.isdigit():
            return ""
        v = int(value)
        if min_value is not None and v < min_value:
            return ""
        if max_value is not None and v > max_value:
            return ""
        return str(v)

    def _format_gps_time(self, raw_time: str) -> str:
        """
        Recibe hhmmss(.sss) y devuelve 'HH:MM:SS' o "" si es inválido.
        """
        if not raw_time:
            return ""
        hhmmss = raw_time.split('.')[0]
        if len(hhmmss) != 6 or not hhmmss.isdigit():
            log.warning(f"Hora GPS con formato inválido: {raw_time}")
            return ""
        return f"{hhmmss[0:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}"

    def _format_gps_date(self, ddmmyy: str) -> Optional[str]:
        """
        Recibe ddmmyy y devuelve 'dd/mm/yyyy' o None si es inválida.
        """
        if not ddmmyy or len(ddmmyy) != 6 or not ddmmyy.isdigit():
            log.warning(f"Fecha GPS con formato inválido: {ddmmyy}")
            return None
        return f"{ddmmyy[0:2]}/{ddmmyy[2:4]}/20{ddmmyy[4:6]}"

    # def _parse_gnrmc(self, line: str):
    #     try:
    #         campos = line.strip().split(",")
    #         # Extraer velocidad
    #         if len(campos) >= 8 and campos[7]:
    #             velocidad_nudos = float(campos[7])
    #             velocidad_kmh = velocidad_nudos * 1.852
    #             self._last_speed_kmh = f"{velocidad_kmh:.2f}"
    #         else:
    #             self._last_speed_kmh = None

    #         # NUEVO: Extraer fecha
    #         if len(campos) >= 10 and campos[9]:
    #             ddmmyy = campos[9]
    #             # Formatear a dd/mm/yyyy
    #             self._last_date_str = f"{ddmmyy[0:2]}/{ddmmyy[2:4]}/20{ddmmyy[4:6]}"
                
    #     except (ValueError, IndexError) as e:
    #         log.warning(f"Error parseando GNRMC: {e} en línea: {line}")
    #         self._last_speed_kmh = None

    def _parse_gnrmc(self, line: str):
        try:
            campos = line.strip().split(",")
            # Extraer velocidad
            self._last_speed_kmh = None
            if len(campos) >= 8 and campos[7]:
                try:
                    velocidad_nudos = float(campos[7])
                    velocidad_kmh = velocidad_nudos * 1.852
                except ValueError:
                    log.warning(f"Velocidad RMC no numérica: {campos[7]} en línea: {line}")
                    velocidad_kmh = None

                if velocidad_kmh is not None:
                    if 0.0 <= velocidad_kmh <= self.max_speed_kmh:
                        self._last_speed_kmh = f"{velocidad_kmh:.2f}"
                    else:
                        log.warning(
                            f"Velocidad GPS fuera de rango válido: "
                            f"{velocidad_kmh:.2f} km/h (max: {self.max_speed_kmh}). Descartada."
                        )

            # Extraer fecha con validación
            self._last_date_str = None
            if len(campos) >= 10 and campos[9]:
                formateada = self._format_gps_date(campos[9])
                if formateada is not None:
                    self._last_date_str = formateada

        except (ValueError, IndexError) as e:
            log.warning(f"Error parseando GNRMC: {e} en línea: {line}")
            self._last_speed_kmh = None
            self._last_date_str = None


    # def _parse_gngga(self, line: str) -> Optional[Dict[str, Any]]:
    #     try:
    #         campos = line.strip().split(",")
    #         if len(campos) < 10:
    #             return None
            
    #         # Comprobación de integridad básica: una trama GGA válida tiene al menos 15 campos.
    #         if len(campos) < 15:
    #             log.warning(f"Trama GNGGA incompleta (campos: {len(campos)}). Descartando: {line}")
    #             return None
                
    #         fix = campos[6]
    #         if fix == '0' or not campos[2] or not campos[4]:
    #             log.info("Trama GGA recibida, pero sin fix de GPS. Se registrará el estado.")
    #             return {"status": "No Fix"}
            
    #         lat_direction = campos[3]
    #         lon_direction = campos[5]

    #         if lat_direction != 'N':
    #             log.warning(f"Latitud inválida para España (dirección '{lat_direction}', se esperaba 'N'). Descartando trama.")
    #             return None

    #         lat_decimal = self._convert_lat_lon(campos[2], lat_direction)
    #         lon_decimal = self._convert_lat_lon(campos[4], lon_direction)

    #         if lat_decimal is None or lon_decimal is None:
    #             # El log de por qué es None ya se ha hecho en _convert_lat_lon
    #             log.warning(f"Coordenadas inválidas recibidas en trama GNGGA. Descartando.")
    #             return None # Devuelve None para que la trama entera sea descartada
            
    #         # Latitud: ~36° a ~44°
    #         if not (35.0 < lat_decimal < 45.0):
    #             log.warning(f"Latitud ({lat_decimal}) fuera del rango esperado para España. Descartando trama.")
    #             return None
            
    #         # Longitud: ~-18° (Canarias) a ~4° (Baleares)
    #         if not (-19.0 < lon_decimal < 5.0):
    #             log.warning(f"Longitud ({lon_decimal}) fuera del rango esperado para España. Descartando trama.")
    #             return None
            
    #         # NUEVO: Extraer y formatear hora GPS
    #         gps_time_str = "N/A"
    #         if campos[1]:
    #             hhmmss = campos[1].split('.')[0]
    #             gps_time_str = f"{hhmmss[0:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}"

    #         return {
    #             "status": "Valid",
    #             "latitude": f"{lat_decimal:.7f}", # Aumentada precisión para coincidir con ejemplo
    #             "longitude": f"{lon_decimal:.7f}",
    #             "altitude_m": campos[9] if campos[9] else "",
    #             "hdop": campos[8] if campos[8] else "",
    #             "fix_quality": fix,
    #             "num_sats": campos[7] if campos[7] else "",
    #             "speed_kmph": self._last_speed_kmh if self._last_speed_kmh is not None else "",
    #             "gps_time": gps_time_str,
    #             "gps_date": self._last_date_str if self._last_date_str is not None else ""
    #         }
    #     except (ValueError, IndexError) as e:
    #         log.warning(f"Error parseando GNGGA: {e} en línea: {line}")
    #         return None

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
            
            lat_direction = campos[3]
            lon_direction = campos[5]

            lat_decimal = self._convert_lat_lon(campos[2], lat_direction)
            lon_decimal = self._convert_lat_lon(campos[4], lon_direction)

            if lat_decimal is None or lon_decimal is None:
                log.warning("Coordenadas inválidas recibidas en trama GNGGA. Descartando.")
                return None

            # Geo-fence configurable
            if self.geo_fence_enabled:
                if not (self.lat_min < lat_decimal < self.lat_max):
                    log.warning(f"Latitud ({lat_decimal}) fuera del geo-fence [{self.lat_min}, {self.lat_max}]. Descartando.")
                    return None
                if not (self.lon_min < lon_decimal < self.lon_max):
                    log.warning(f"Longitud ({lon_decimal}) fuera del geo-fence [{self.lon_min}, {self.lon_max}]. Descartando.")
                    return None
            
            # Hora GPS validada
            gps_time_str = ""
            if campos[1]:
                gps_time_str = self._format_gps_time(campos[1])

            # Limpieza de otros campos numéricos
            altitude_m = self._clean_float(campos[9], min_value=-500.0, max_value=5000.0, decimals=1)
            hdop = self._clean_float(campos[8], min_value=0.0, max_value=50.0, decimals=1)
            num_sats = self._clean_int(campos[7], min_value=0, max_value=50)

            # Si nº de satélites no es razonable, descartamos
            if not num_sats:
                log.warning(f"Número de satélites inválido en GNGGA: '{campos[7]}'. Descartando trama.")
                return None

            data = {
                "status": "Valid",
                "latitude": f"{lat_decimal:.7f}",
                "longitude": f"{lon_decimal:.7f}",
                "altitude_m": altitude_m,
                "hdop": hdop,
                "fix_quality": fix,
                "num_sats": num_sats,
                "speed_kmph": self._last_speed_kmh if self._last_speed_kmh is not None else "",
                "gps_time": gps_time_str,
                "gps_date": self._last_date_str if self._last_date_str is not None else ""
            }

            # OPCIONAL: descartar paquetes “demasiado vacíos”
            campos_criticos_vacios = sum(
                1 for k in ("gps_time", "gps_date", "altitude_m", "hdop")
                if not data[k]
            )
            if campos_criticos_vacios >= 3:
                log.warning(f"Demasiados campos críticos vacíos en trama GNGGA. Descartando: {data}")
                return None

            return data

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
                        if parsed_data.get("status") == "Valid":
                            self.data_seen = True
                            log.debug(f"Paquete GPS (NMEA) válido procesado: {parsed_data}")
                        else:
                            log.debug(f"Paquete GPS (NMEA) sin fix procesado: {parsed_data}")
                        
                        packet = self._create_data_packet("gps", parsed_data)
                        self.data_queue.put(packet)
        
        self.shutdown_event.wait(0.05)


    def _cleanup(self):
        if self.bus:
            self.bus.close()
            log.info("Bus I2C para GPS cerrado.")