import logging
import time
from datetime import datetime
from typing import Optional, Dict, Any
import can

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class CANAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="CANAcquirer", config_key="can")
        self.bus = None
        
        # Cargar configuración específica de OBD-II
        self.request_id = self.config.get('request_id', 0x7DF)
        self.queries = self.config.get('queries', [])
        self.query_interval = self.config.get('query_interval_sec', 0.2)
        self.response_timeout = self.config.get('response_timeout_sec', 0.5)
        
        self.current_query_index = 0

    def _setup(self) -> bool:
        if not self.queries:
            log.warning("CANAcquirer está habilitado pero no se han definido 'queries' en la configuración. El hilo no hará nada.")
            return True # No es un error fatal, simplemente no hará nada.

        try:
            interface = self.config.get('interface', 'can0')
            bitrate = self.config.get('bitrate', 500000)
            
            # Filtro para recibir solo respuestas de diagnóstico (IDs 0x7E8 a 0x7EF)
            # La máscara 0x7F8 asegura que los primeros 8 bits coincidan con 0x7E8
            filters = [{"can_id": 0x7E8, "can_mask": 0x7F8, "extended": False}]
            
            self.bus = can.interface.Bus(channel=interface, bustype='socketcan', bitrate=bitrate, can_filters=filters)
            log.info(f"Bus CAN conectado en '{interface}' con bitrate {bitrate}. Filtro de respuesta OBD-II activado.")
            return True
        except (OSError, can.CanError) as e:
            log.critical(f"FATAL: Error al inicializar el bus CAN: {e}. ¿Está la interfaz '{interface}' activa?")
            return False

    def _parse_obd_response(self, request_payload: list, response_msg: can.Message) -> Optional[Dict[str, Any]]:
        """
        Parsea una respuesta OBD-II y calcula el valor real según el PID.
        """
        data = response_msg.data
        # La respuesta correcta es Modo+0x40, PID
        expected_mode = request_payload[1]
        expected_pid = request_payload[2]

        if data[1] == (expected_mode + 0x40) and data[2] == expected_pid:
            val_a = data[3]
            val_b = data[4] if len(data) > 4 else 0
            
            value = None
            # --- Fórmulas de cálculo para PIDs comunes ---
            if expected_pid == 0x0C: # Engine RPM
                value = ((val_a * 256) + val_b) / 4
            elif expected_pid == 0x0D: # Vehicle Speed
                value = val_a
            elif expected_pid == 0x05: # Engine Coolant Temperature
                value = val_a - 40
            elif expected_pid == 0x11: # Throttle Position
                value = (val_a * 100) / 255
            # --- Añadir más PIDs aquí ---
            
            if value is not None:
                return {"value": round(value, 2)}
        
        return None

    def _acquire_data(self):
        if not self.queries or not self.bus:
            self.shutdown_event.wait(1.0) # Esperar si no hay nada que hacer
            return

        # 1. Seleccionar la próxima consulta a realizar
        query = self.queries[self.current_query_index]
        
        # 2. Crear y enviar el mensaje de petición
        request_msg = can.Message(
            arbitration_id=self.request_id,
            data=query['request_payload'],
            is_extended_id=False
        )
        try:
            self.bus.send(request_msg)
            log.debug(f"Enviada consulta OBD-II para: {query['name']}")
        except can.CanError as e:
            log.error(f"Error al enviar mensaje CAN: {e}")
            self.shutdown_event.wait(self.query_interval) # Esperar antes de reintentar
            return

        # 3. Escuchar la respuesta
        start_time = time.monotonic()
        response_msg = None
        while time.monotonic() - start_time < self.response_timeout:
            msg = self.bus.recv(timeout=0.1) # Pequeño timeout para no bloquear
            if msg:
                # Comprobar si la respuesta es para nuestra petición
                if msg.data[2] == query['request_payload'][2]:
                    response_msg = msg
                    break # Respuesta encontrada

        # 4. Procesar la respuesta si se recibió
        if response_msg:
            parsed_data = self._parse_obd_response(query['request_payload'], response_msg)
            if parsed_data:
                final_data = {
                    "query": query['name'],
                    "value": parsed_data['value'],
                    "unit": query.get('unit', 'N/A'),
                    "raw_response": response_msg.data.hex().upper()
                }
                packet = self._create_data_packet("can", final_data)
                self.data_queue.put(packet)
                log.debug(f"Respuesta recibida y procesada para {query['name']}: {final_data['value']} {final_data['unit']}")
            else:
                log.warning(f"Respuesta CAN para '{query['name']}' recibida pero no se pudo parsear. Datos: {response_msg.data.hex().upper()}")
        else:
            log.warning(f"Timeout: No se recibió respuesta para la consulta '{query['name']}'.")

        # 5. Avanzar a la siguiente consulta y esperar
        self.current_query_index = (self.current_query_index + 1) % len(self.queries)
        self.shutdown_event.wait(self.query_interval)


    def _cleanup(self):
        if self.bus:
            self.bus.shutdown()
            log.info("Bus CAN desconectado.")