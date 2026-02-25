import logging
from datetime import datetime
from typing import Optional
import serial

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class IMUAcquirer(BaseAcquirer):
    # Claves que coinciden exactamente con la cabecera de la trama
    DATA_KEYS = [
        "ax", "ay", "az", "gx", "gy", "gz",
        "roll", "pitch", "yaw", "timeantwifi",
        "usciclo1", "usciclo2", "SI", "SIF", "k3"
    ]
    EXPECTED_VALUES = len(DATA_KEYS)

    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="IMUAcquirer", config_key="estabilometro")
        self.ser = None

    def _setup(self) -> bool:
        try:
            port = self.config.get("serial_port")
            baud_rate = self.config.get("baud_rate")
            self.ser = serial.Serial(port, baud_rate, timeout=1)
            log.info(f"Puerto serie '{port}' abierto para Estabilómetro/IMU a {baud_rate} baudios.")
            return True
        except serial.SerialException as e:
            log.critical(f"FATAL: No se pudo abrir el puerto serie para Estabilómetro/IMU: {e}. Compruebe permisos y conexión.")
            self.ser = None
            return False

    def _parse_stabilometer_data(self, line: str) -> Optional[dict]:
        """
        Parsea la trama del estabilómetro. Primero comprueba si es una cabecera
        y solo después valida el número de campos para evitar falsos warnings.
        """
        try:
            # --- INICIO DE LA CORRECCIÓN ---

            # Dividimos la línea por el delimitador y eliminamos espacios en blanco.
            values = [v.strip() for v in line.split(';')]

            # Es común que las tramas terminen con ';', lo que genera un campo vacío al final. Lo eliminamos.
            if values and not values[-1]:
                values.pop()

            # 1. PRIMERO, comprobamos si es una línea de datos o una cabecera.
            #    Intentamos convertir el primer valor a float. Si falla, es una cabecera/texto.
            try:
                # Usamos `values[0]` para la comprobación.
                float(values[0])
            except (ValueError, IndexError):
                # Si falla (ValueError) o la línea está vacía (IndexError),
                # la consideramos una cabecera o basura y la ignoramos silenciosamente.
                log.info(f"Línea ignorada (posible cabecera o línea vacía): {line}")
                return None

            # 2. AHORA, si sabemos que es una línea de datos, validamos que tenga el número correcto de valores.
            if len(values) != self.EXPECTED_VALUES:
                log.warning(f"Trama de datos con número incorrecto de valores. "
                            f"Esperados: {self.EXPECTED_VALUES}, Recibidos: {len(values)}. Trama: '{line}'")
                return None
            
            # 3. Si todo es correcto, construimos el diccionario.
            data_dict = {}
            for key, value_str in zip(self.DATA_KEYS, values):
                try:
                    # Intentamos convertir cada valor a float.
                    data_dict[key] = float(value_str)
                except ValueError:
                    # Si algún valor intermedio no es numérico, lo guardamos como texto.
                    data_dict[key] = value_str 
            
            return data_dict

            # --- FIN DE LA CORRECCIÓN ---

        except Exception as e:
            log.error(f"Error inesperado al parsear la línea del estabilómetro '{line}': {e}")
            return None

    def _acquire_data(self):
        try:
            if self.ser and self.ser.in_waiting > 0:
                line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    parsed_data = self._parse_stabilometer_data(line)
                    if parsed_data:
                        packet = self._create_data_packet("estabilometro", parsed_data)
                        self.data_queue.put(packet)
        except serial.SerialException as e:
            log.error(f"Error grave de puerto serie durante la lectura: {e}. El hilo terminará.")
            if self.ser and self.ser.is_open:
                self.ser.close()
            self.ser = None
            # Relanzamos la excepción para que el gestor principal sepa que el hilo ha muerto
            raise e
        except Exception as e:
            log.error(f"Error inesperado en adquisición de estabilómetro: {e}")

        # Pequeña pausa para no consumir 100% de CPU si no hay datos
        self.shutdown_event.wait(0.01)

    def _cleanup(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            log.info("Puerto serie del Estabilómetro/IMU cerrado.")